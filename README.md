# CertForge - Bulk Certificate Generator API

Submit a list of recipients in **one request**, get a job back instantly, poll progress, and download
the certificates as individual PDFs or a single ZIP. Every certificate carries a **QR code** that
opens a public verification endpoint, so anyone can check a certificate is genuine.

Built with **FastAPI + SQLAlchemy (SQLite) + ReportLab**.

## What makes it different
- **Per-recipient validation**: one bad row is recorded as `FAILED` with a reason; the rest still generate.
- **QR-verifiable certificates** + a SHA-256 stored for each PDF (`/api/v1/verify/{id}`).
- **Idempotency-Key header**: a retried request returns the original job instead of generating twice.
- **ZIP download with `manifest.csv`** listing success/failure of every input row.
- **Deterministic PDFs** (no timestamps), so the stored hash is reproducible.

## Setup
```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
uvicorn app.main:app --reload
```
Interactive docs: http://localhost:8000/docs

Optional env vars: `DATABASE_URL` (default SQLite file), `STORAGE_DIR` (default `./storage`),
`PUBLIC_BASE_URL` (used in QR codes, default `http://localhost:8000`), `MAX_RECIPIENTS` (default 1000).

## Run tests
```bash
pytest -v
```
Covers: job creation, input validation, PDF generation, status/progress, single-certificate failure,
retrieval (PDF, ZIP, verify), idempotency.

## API usage

### 1. Submit a bulk request
```bash
curl -X POST http://localhost:8000/api/v1/jobs \
  -H "Content-Type: application/json" -H "Idempotency-Key: bootcamp-2026-batch-1" \
  -d '{
    "event_name": "Python Bootcamp 2026",
    "issuer": "REVA University",
    "issue_date": "2026-10-07",
    "recipients": [
      {"name": "Asha Rao", "email": "asha@example.com", "achievement": "Top Performer"},
      {"name": "Ravi Kumar"},
      {"name": "", "email": "broken"}
    ]
  }'
```
Response `202`:
```json
{"job_id": "b1f0...", "status": "PENDING", "total": 3, "status_url": "/api/v1/jobs/b1f0..."}
```

### 2. Track progress
`GET /api/v1/jobs/{job_id}`
```json
{"status": "COMPLETED_WITH_ERRORS", "total": 3, "succeeded": 2, "failed": 1, "pending": 0,
 "progress_percent": 100.0,
 "failures": [{"index": 2, "recipient_name": null, "error": "name: String should have at least 1 character"}]}
```
Job statuses: `PENDING -> PROCESSING -> COMPLETED | COMPLETED_WITH_ERRORS | FAILED`.

### 3. Retrieve certificates
| Endpoint | Purpose |
|---|---|
| `GET /api/v1/jobs/{id}/certificates?status=SUCCESS\|FAILED` | List certificates with download + verify links |
| `GET /api/v1/certificates/{cert_id}/download` | One PDF |
| `GET /api/v1/jobs/{id}/download` | ZIP of all PDFs + `manifest.csv` |
| `GET /api/v1/verify/{cert_id}` | Public verification (target of the QR code) |

## Design decisions
**Asynchronous (background) processing.** A request may contain up to 1000 recipients; rendering them
inside the HTTP request would risk timeouts and block the client. The API validates and stores the job,
returns `202 Accepted`, and generates in a FastAPI background task. Progress is committed after *each*
certificate, so the status endpoint shows live progress.
*Alternative considered:* Celery + Redis. It is the right tool at larger scale (retries, multiple workers,
survives restarts) but adds infrastructure that is overkill for this scope.
*Known trade-off:* if the server restarts mid-job, the job stays `PROCESSING`. `process_job` only handles
`PENDING` certificates, so it is resumable by design; a startup sweep would be the next step.

**Validation at two levels.** Request-level problems (empty list, missing event name, > max recipients) -> `422`.
Recipient-level problems -> that certificate is stored as `FAILED` with an explanation. Failures never block siblings,
and each generation is wrapped in its own `try/except` and commit.

**One predefined template, rendered with ReportLab** (pure Python, no browser or system dependencies).
`generator.py` has no DB/HTTP knowledge, so it is unit-testable alone.

**SQLite + SQLAlchemy 2.0.** Zero setup; switch by changing `DATABASE_URL` (e.g. PostgreSQL).
Certificates are files on disk, with path + SHA-256 in the DB.

**Security notes.** Files are named by UUID (no user input in paths); ZIP entry names are slugified;
names are length-limited and stripped of control characters.

## Project structure
```
app/
  main.py            app + startup
  config.py          env-based settings
  database.py        engine / session
  models.py          Job, Certificate
  schemas.py         request/response + per-recipient validation
  services/
    generator.py     PDF + QR rendering (pure)
    jobs.py          create_job (validate) / process_job (generate)
  routers/jobs.py    HTTP endpoints
tests/               pytest suite
```

## Learnings & future scope
- Learned to separate request-level vs item-level validation so bulk APIs stay forgiving.
- Future: Celery/RQ workers, resume-on-startup, S3 storage, email delivery of PDFs, CSV upload,
  custom fonts for non-Latin names, rate limiting + auth, PDF-hash verification upload endpoint.

