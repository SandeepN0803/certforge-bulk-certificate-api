import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config import settings

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


class RecipientIn(BaseModel):
    """Validated per recipient, so one bad row never rejects the whole request."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=254)
    achievement: str | None = Field(default=None, max_length=120)

    @field_validator("name", "achievement")
    @classmethod
    def no_control_chars(cls, v):
        if v is not None and _CONTROL_CHARS.search(v):
            raise ValueError("must not contain control characters")
        return v

    @field_validator("email")
    @classmethod
    def valid_email(cls, v):
        if v is not None and not _EMAIL_RE.match(v):
            raise ValueError("is not a valid email address")
        return v


class JobCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=150)
    issuer: str = Field(min_length=1, max_length=100)
    issue_date: date = Field(default_factory=date.today)
    # Items are kept loosely typed here on purpose; each is validated individually later.
    recipients: list[Any] = Field(min_length=1, max_length=settings.max_recipients)


class FailureOut(BaseModel):
    index: int
    recipient_name: str | None
    error: str | None


class JobCreatedOut(BaseModel):
    job_id: str
    status: str
    total: int
    status_url: str


class JobStatusOut(BaseModel):
    job_id: str
    status: str
    event_name: str
    total: int
    succeeded: int
    failed: int
    pending: int
    progress_percent: float
    created_at: str
    completed_at: str | None
    failures: list[FailureOut]
    download_all_url: str


class CertificateOut(BaseModel):
    certificate_id: str
    index: int
    recipient_name: str | None
    status: str
    error: str | None
    sha256: str | None
    download_url: str | None
    verify_url: str | None
