import csv
import io
import re
import zipfile

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Response
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Certificate, CertStatus, Job
from app.schemas import CertificateOut, FailureOut, JobCreate, JobCreatedOut, JobStatusOut
from app.services import jobs as job_service

router = APIRouter(prefix="/api/v1", tags=["certificates"])


def _get_job(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


def _slug(text: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text or "unknown").strip("_")[:40] or "unknown"


@router.post("/jobs", status_code=202, response_model=JobCreatedOut)
def create_job(
    payload: JobCreate,
    background: BackgroundTasks,
    response: Response,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
):
    """Submit a bulk request. Returns immediately; generation runs in the background."""
    job, created = job_service.create_job(db, payload, idempotency_key)
    if created and job.status == "PENDING":
        background.add_task(job_service.process_job, job.id)
    if not created:
        response.status_code = 200  # replay of an earlier idempotent request
    return JobCreatedOut(
        job_id=job.id, status=job.status, total=job.total, status_url=f"/api/v1/jobs/{job.id}"
    )


@router.get("/jobs/{job_id}", response_model=JobStatusOut)
def job_status(job_id: str, db: Session = Depends(get_db)):
    job = _get_job(db, job_id)
    done = job.succeeded + job.failed
    failures = [
        FailureOut(index=c.index, recipient_name=c.recipient_name, error=c.error)
        for c in job.certificates
        if c.status == CertStatus.FAILED
    ]
    return JobStatusOut(
        job_id=job.id,
        status=job.status,
        event_name=job.event_name,
        total=job.total,
        succeeded=job.succeeded,
        failed=job.failed,
        pending=job.total - done,
        progress_percent=round(done / job.total * 100, 1) if job.total else 100.0,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        failures=failures,
        download_all_url=f"/api/v1/jobs/{job.id}/download",
    )


@router.get("/jobs/{job_id}/certificates", response_model=list[CertificateOut])
def list_certificates(
    job_id: str,
    status: str | None = Query(default=None, pattern="^(PENDING|SUCCESS|FAILED)$"),
    db: Session = Depends(get_db),
):
    job = _get_job(db, job_id)
    out = []
    for c in job.certificates:
        if status and c.status != status:
            continue
        ok = c.status == CertStatus.SUCCESS
        out.append(
            CertificateOut(
                certificate_id=c.id,
                index=c.index,
                recipient_name=c.recipient_name,
                status=c.status,
                error=c.error,
                sha256=c.sha256,
                download_url=f"/api/v1/certificates/{c.id}/download" if ok else None,
                verify_url=job_service.verify_url_for(c.id) if ok else None,
            )
        )
    return out


@router.get("/certificates/{certificate_id}/download")
def download_certificate(certificate_id: str, db: Session = Depends(get_db)):
    cert = db.get(Certificate, certificate_id)
    if cert is None:
        raise HTTPException(404, "Certificate not found")
    if cert.status != CertStatus.SUCCESS or not cert.file_path:
        raise HTTPException(409, f"Certificate is not available (status: {cert.status})")
    return FileResponse(
        cert.file_path, media_type="application/pdf", filename=f"certificate_{_slug(cert.recipient_name)}.pdf"
    )


@router.get("/jobs/{job_id}/download")
def download_all(job_id: str, db: Session = Depends(get_db)):
    """ZIP with every successful PDF plus manifest.csv listing success/failure of each row."""
    job = _get_job(db, job_id)
    if job.status in ("PENDING", "PROCESSING"):
        raise HTTPException(409, "Job is still running; poll the status endpoint first")
    buf = io.BytesIO()
    manifest = io.StringIO()
    writer = csv.writer(manifest)
    writer.writerow(["index", "recipient_name", "status", "certificate_id", "file", "sha256", "error"])
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for c in job.certificates:
            fname = ""
            if c.status == CertStatus.SUCCESS and c.file_path:
                fname = f"{c.index:04d}_{_slug(c.recipient_name)}.pdf"
                zf.write(c.file_path, fname)
            writer.writerow([c.index, c.recipient_name or "", c.status, c.id, fname, c.sha256 or "", c.error or ""])
        zf.writestr("manifest.csv", manifest.getvalue())
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="certificates_{job.id}.zip"'},
    )


@router.get("/verify/{certificate_id}")
def verify_certificate(certificate_id: str, db: Session = Depends(get_db)):
    """Public endpoint the QR code on each certificate points to."""
    cert = db.get(Certificate, certificate_id)
    if cert is None or cert.status != CertStatus.SUCCESS:
        raise HTTPException(404, detail={"valid": False, "message": "No such certificate"})
    return {
        "valid": True,
        "certificate_id": cert.id,
        "recipient_name": cert.recipient_name,
        "event_name": cert.job.event_name,
        "issuer": cert.job.issuer,
        "issue_date": cert.job.issue_date.isoformat(),
        "issued_at": cert.issued_at.isoformat() if cert.issued_at else None,
        "sha256": cert.sha256,
    }
