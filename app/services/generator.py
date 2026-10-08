"""Pure PDF rendering. No database or HTTP knowledge, so it is easy to test in isolation."""
import hashlib
from datetime import date
from io import BytesIO

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1b2a49")
GOLD = colors.HexColor("#b8923a")
GREY = colors.HexColor("#6b7280")


def _fit_font_size(text: str, font: str, max_width: float, start: int, minimum: int = 14) -> int:
    size = start
    while size > minimum and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def _draw_qr(c: canvas.Canvas, url: str, x: float, y: float, size: float) -> None:
    widget = qr.QrCodeWidget(url)
    x0, y0, x1, y1 = widget.getBounds()
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def render_certificate(
    *,
    certificate_id: str,
    recipient_name: str,
    event_name: str,
    issuer: str,
    issue_date: date,
    verify_url: str,
    achievement: str | None = None,
) -> bytes:
    """Render the single predefined template and return the PDF bytes."""
    buf = BytesIO()
    # invariant=1 removes timestamps so identical input yields identical bytes (stable hashes).
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H), invariant=1)
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor(issuer)

    # Double border
    c.setStrokeColor(NAVY)
    c.setLineWidth(6)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.rect(38, 38, PAGE_W - 76, PAGE_H - 76)

    mid = PAGE_W / 2
    c.setFillColor(NAVY)
    c.setFont("Times-Bold", 42)
    c.drawCentredString(mid, PAGE_H - 140, "CERTIFICATE")
    c.setFillColor(GOLD)
    c.setFont("Times-Roman", 16)
    c.drawCentredString(mid, PAGE_H - 168, "OF  " + ("ACHIEVEMENT" if achievement else "PARTICIPATION"))

    c.setFillColor(GREY)
    c.setFont("Helvetica", 13)
    c.drawCentredString(mid, PAGE_H - 215, "This certificate is proudly presented to")

    name_font = "Times-BoldItalic"
    size = _fit_font_size(recipient_name, name_font, PAGE_W - 200, 40)
    c.setFillColor(NAVY)
    c.setFont(name_font, size)
    c.drawCentredString(mid, PAGE_H - 270, recipient_name)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(mid - 220, PAGE_H - 282, mid + 220, PAGE_H - 282)

    c.setFillColor(GREY)
    c.setFont("Helvetica", 13)
    c.drawCentredString(mid, PAGE_H - 315, "for successfully participating in")
    ev_font = "Helvetica-Bold"
    ev_size = _fit_font_size(event_name, ev_font, PAGE_W - 200, 22, 12)
    c.setFillColor(NAVY)
    c.setFont(ev_font, ev_size)
    c.drawCentredString(mid, PAGE_H - 345, event_name)
    if achievement:
        c.setFillColor(GOLD)
        c.setFont("Helvetica-Oblique", 13)
        c.drawCentredString(mid, PAGE_H - 370, achievement)

    # Footer: issuer, date, QR
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(80, 86, issuer)
    c.setFillColor(GREY)
    c.setFont("Helvetica", 10)
    c.drawString(80, 102, "Issued by")
    c.drawString(80, 130, f"Date: {issue_date.strftime('%d %B %Y')}")

    _draw_qr(c, verify_url, PAGE_W - 150, 62, 70)
    c.setFont("Helvetica", 7)
    c.drawCentredString(PAGE_W - 115, 52, "Scan to verify")
    c.drawCentredString(mid, 52, f"Certificate ID: {certificate_id}")

    c.showPage()
    c.save()
    return buf.getvalue()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
