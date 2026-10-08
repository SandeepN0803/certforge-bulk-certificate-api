import io
import zipfile

from app.services import jobs as job_service
from tests.conftest import make_payload


# ---- Creating a generation job -------------------------------------------------
def test_create_job_returns_202_and_job_id(client):
    r = client.post("/api/v1/jobs", json=make_payload(["Asha", "Ravi", "Meena"]))
    assert r.status_code == 202
    body = r.json()
    assert body["total"] == 3
    assert body["job_id"]
    assert body["status_url"] == f"/api/v1/jobs/{body['job_id']}"


def test_idempotency_key_prevents_duplicate_jobs(client):
    headers = {"Idempotency-Key": "abc-123"}
    first = client.post("/api/v1/jobs", json=make_payload(["Asha"]), headers=headers)
    second = client.post("/api/v1/jobs", json=make_payload(["Asha"]), headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200  # replay, not a new job
    assert first.json()["job_id"] == second.json()["job_id"]


# ---- Input validation ----------------------------------------------------------
def test_empty_recipient_list_is_rejected(client):
    assert client.post("/api/v1/jobs", json=make_payload([])).status_code == 422


def test_missing_event_name_is_rejected(client):
    payload = make_payload(["Asha"])
    del payload["event_name"]
    assert client.post("/api/v1/jobs", json=payload).status_code == 422


def test_invalid_recipients_fail_individually_not_the_whole_job(client):
    payload = make_payload(["Asha"])
    payload["recipients"] += [
        {"name": "   "},                      # blank name
        {"name": "Bad Email", "email": "nope"},
        "not-an-object",
        {"name": "Ravi"},
    ]
    job = client.post("/api/v1/jobs", json=payload).json()
    status = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert status["status"] == "COMPLETED_WITH_ERRORS"
    assert status["succeeded"] == 2
    assert status["failed"] == 3
    assert {f["index"] for f in status["failures"]} == {1, 2, 3}


def test_all_invalid_marks_job_failed(client):
    job = client.post("/api/v1/jobs", json=make_payload([""])).json()
    assert client.get(f"/api/v1/jobs/{job['job_id']}").json()["status"] == "FAILED"


# ---- Certificate generation ----------------------------------------------------
def test_generated_certificate_is_a_pdf_with_matching_hash(client):
    job = client.post("/api/v1/jobs", json=make_payload(["Asha"])).json()
    cert = client.get(f"/api/v1/jobs/{job['job_id']}/certificates").json()[0]
    pdf = client.get(cert["download_url"])
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")
    assert job_service.sha256_hex(pdf.content) == cert["sha256"]


# ---- Job status / progress -----------------------------------------------------
def test_job_status_reports_progress(client):
    job = client.post("/api/v1/jobs", json=make_payload(["A", "B", "C", "D"])).json()
    s = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert s["status"] == "COMPLETED"
    assert (s["total"], s["succeeded"], s["failed"], s["pending"]) == (4, 4, 0, 0)
    assert s["progress_percent"] == 100.0
    assert s["completed_at"] is not None


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404


# ---- Individual certificate failure --------------------------------------------
def test_one_generation_failure_does_not_stop_the_others(client, monkeypatch):
    real = job_service.render_certificate

    def flaky(**kwargs):
        if kwargs["recipient_name"] == "Boom":
            raise RuntimeError("renderer exploded")
        return real(**kwargs)

    monkeypatch.setattr(job_service, "render_certificate", flaky)
    job = client.post("/api/v1/jobs", json=make_payload(["Asha", "Boom", "Ravi"])).json()
    s = client.get(f"/api/v1/jobs/{job['job_id']}").json()
    assert s["status"] == "COMPLETED_WITH_ERRORS"
    assert (s["succeeded"], s["failed"]) == (2, 1)
    assert "renderer exploded" in s["failures"][0]["error"]
    failed = client.get(f"/api/v1/jobs/{job['job_id']}/certificates?status=FAILED").json()
    assert failed[0]["recipient_name"] == "Boom" and failed[0]["download_url"] is None


# ---- Retrieving certificates ---------------------------------------------------
def test_download_all_zip_contains_pdfs_and_manifest(client):
    job = client.post("/api/v1/jobs", json=make_payload(["Asha", "Ravi"])).json()
    r = client.get(f"/api/v1/jobs/{job['job_id']}/download")
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert sorted(n for n in names if n.endswith(".pdf")) == ["0000_Asha.pdf", "0001_Ravi.pdf"]
    assert "manifest.csv" in names


def test_failed_certificate_cannot_be_downloaded(client):
    job = client.post("/api/v1/jobs", json=make_payload(["Asha", ""])).json()
    certs = client.get(f"/api/v1/jobs/{job['job_id']}/certificates?status=FAILED").json()
    r = client.get(f"/api/v1/certificates/{certs[0]['certificate_id']}/download")
    assert r.status_code == 409


def test_verify_endpoint_confirms_real_and_rejects_fake(client):
    job = client.post("/api/v1/jobs", json=make_payload(["Asha"])).json()
    cert = client.get(f"/api/v1/jobs/{job['job_id']}/certificates").json()[0]
    ok = client.get(f"/api/v1/verify/{cert['certificate_id']}")
    assert ok.status_code == 200 and ok.json()["valid"] is True
    assert ok.json()["recipient_name"] == "Asha"
    assert client.get("/api/v1/verify/fake-id").status_code == 404
