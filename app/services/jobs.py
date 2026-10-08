"""Job lifecycle: create (validate), process (generate), and status helpers."""
import logging
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Certificate, CertStatus, Job, JobStatus
from app.schemas import JobCreate, RecipientIn
from app.services.generator import render_certificate, sha256_hex

log = logging.getLogger(__name__)


def verify_url_for(certificate_id: str) -> str:
    return f"{settings.public_base_url}/api/v1/verify/{certificate_id}"


def _format_errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'recipient'}: {e['msg']}" for e in exc.errors()
    )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_job(db: Session, payload: JobCreate, idempotency_key: str | None) -> tuple[Job, bool]:
    """Validate every recipient individually and persist the job.

    Returns (job, created). With an Idempotency-Key, a retried request returns the
    original job instead of generating everything twice.
    """
    if idempotency_key:
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing, False

    job = Job(
        event_name=payload.event_name.strip(),
        issuer=payload.issuer.strip(),
        issue_date=payload.issue_date,
        idempotency_key=idempotency_key,
        total=len(payload.recipients),
    )
    for index, raw in enumerate(payload.recipients):
        if not isinstance(raw, dict):
            job.certificates.append(
                Certificate(index=index, status=CertStatus.FAILED, error="recipient must be a JSON object")
            )
            job.failed += 1
            continue
        try:
            r = RecipientIn.model_validate(raw)
            job.certificates.append(
                Certificate(
                    index=index,
                    recipient_name=r.name,
                    email=r.email,
                    achievement=r.achievement or None,
                )
            )
        except ValidationError as exc:
            name = raw.get("name") if isinstance(raw.get("name"), str) else None
            job.certificates.append(
                Certificate(
                    index=index,
                    recipient_name=name[:100] if name else None,
                    status=CertStatus.FAILED,
                    error=_format_errors(exc),
                )
            )
            job.failed += 1

    if job.failed == job.total:  # nothing valid, nothing to process
        job.status = JobStatus.FAILED
        job.completed_at = _utcnow()

    db.add(job)
    try:
        db.commit()
    except IntegrityError:  # two identical idempotent requests raced
        db.rollback()
        existing = db.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
        if existing:
            return existing, False
        raise
    return job, True


def process_job(job_id: str) -> None:
    """Generate all pending certificates of a job. Runs in the background with its own session.

    Each certificate is isolated in try/except and committed individually, so one failure
    never blocks the rest and progress is visible while the job runs. Only PENDING
    certificates are touched, which makes the function safe to re-run (resumable).
    """
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if job is None:
            return
        job.status = JobStatus.PROCESSING
        db.commit()

        out_dir = settings.storage_dir / "jobs" / job.id
        out_dir.mkdir(parents=True, exist_ok=True)

        pending = db.scalars(
            select(Certificate)
            .where(Certificate.job_id == job.id, Certificate.status == CertStatus.PENDING)
            .order_by(Certificate.index)
        ).all()

        for cert in pending:
            try:
                pdf = render_certificate(
                    certificate_id=cert.id,
                    recipient_name=cert.recipient_name,
                    event_name=job.event_name,
                    issuer=job.issuer,
                    issue_date=job.issue_date,
                    verify_url=verify_url_for(cert.id),
                    achievement=cert.achievement,
                )
                path = out_dir / f"{cert.id}.pdf"
                path.write_bytes(pdf)
                cert.file_path = str(path)
                cert.sha256 = sha256_hex(pdf)
                cert.issued_at = _utcnow()
                cert.status = CertStatus.SUCCESS
                job.succeeded += 1
            except Exception as exc:  # noqa: BLE001 - isolate any per-certificate failure
                log.exception("certificate %s failed", cert.id)
                cert.status = CertStatus.FAILED
                cert.error = f"generation failed: {exc}"[:500]
                job.failed += 1
            db.commit()

        if job.failed == 0:
            job.status = JobStatus.COMPLETED
        elif job.succeeded == 0:
            job.status = JobStatus.FAILED
        else:
            job.status = JobStatus.COMPLETED_WITH_ERRORS
        job.completed_at = _utcnow()
        db.commit()
    except Exception:  # unexpected crash of the whole job
        log.exception("job %s crashed", job_id)
        db.rollback()
        job = db.get(Job, job_id)
        if job:
            job.status = JobStatus.FAILED
            job.completed_at = _utcnow()
            db.commit()
    finally:
        db.close()
