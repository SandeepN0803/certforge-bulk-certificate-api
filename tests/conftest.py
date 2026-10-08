import os
import shutil
import tempfile

# Must be set BEFORE the app is imported: settings are read at import time.
_TMP = tempfile.mkdtemp(prefix="certforge_tests_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["STORAGE_DIR"] = f"{_TMP}/storage"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(settings.storage_dir, ignore_errors=True)
    # TestClient runs BackgroundTasks before returning, so jobs are finished when POST returns.
    with TestClient(app) as c:
        yield c


def make_payload(names, **extra):
    body = {
        "event_name": "Python Bootcamp 2026",
        "issuer": "REVA University",
        "issue_date": "2026-10-07",
        "recipients": [{"name": n, "email": f"{i}@example.com"} for i, n in enumerate(names)],
    }
    body.update(extra)
    return body
