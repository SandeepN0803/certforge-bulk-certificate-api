from datetime import date

from app.services.generator import render_certificate, sha256_hex

KW = dict(
    certificate_id="abc",
    recipient_name="Asha",
    event_name="Event",
    issuer="Org",
    issue_date=date(2026, 1, 1),
    verify_url="http://x/verify/abc",
)


def test_pdf_is_valid_and_deterministic():
    a, b = render_certificate(**KW), render_certificate(**KW)
    assert a.startswith(b"%PDF") and sha256_hex(a) == sha256_hex(b)


def test_very_long_name_still_renders():
    assert render_certificate(**{**KW, "recipient_name": "X" * 100}).startswith(b"%PDF")
