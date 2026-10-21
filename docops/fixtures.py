from email.message import EmailMessage
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def invoice_text(number: str = "NS-1042", total: str = "220.00", confidence: str = "0.97", extra: str = "") -> str:
    return f"""Northstar Office Supply - Synthetic Demonstration Invoice
Vendor: Northstar Office Supply
Invoice: {number}
Invoice date: 2026-10-01
Due date: 2026-10-31
Currency: USD
Subtotal: 200.00
Tax: 20.00
Total: {total}
PO: PO-2026-17
Confidence: {confidence}
Item: Office supplies | 2 | 100.00 | 200.00
{extra}
"""


def build_fixtures(directory: Path) -> dict[str, Path]:
    from reportlab.pdfgen import canvas

    directory.mkdir(parents=True, exist_ok=True)
    fixtures = {}
    for name, text in {
        "valid": invoice_text(),
        "duplicate": invoice_text(extra="Delivery channel: forwarded email"),
        "incorrect-math": invoice_text("NS-1043", total="260.00"),
        "malformed-ai": invoice_text("NS-1044", extra="Fixture: malformed-ai"),
        "low-confidence": invoice_text("NS-1045", confidence="0.63"),
        "worker-crash": invoice_text("NS-1046"),
        "database-recovery": invoice_text("NS-1047"),
        "provider-retry": invoice_text("NS-1048"),
    }.items():
        path = directory / (name + ".txt")
        path.write_text(text, encoding="utf-8")
        fixtures[name] = path
    corrupted = directory / "corrupted.pdf"
    corrupted.write_bytes(b"%PDF-1.7\nThis synthetic file is deliberately corrupted.\n%%EOF")
    fixtures["corrupted"] = corrupted
    # The source labels deliberately match common structured invoice fields.
    valid_pdf = directory / "valid.pdf"
    pdf = canvas.Canvas(str(valid_pdf), pagesize=(612, 792))
    pdf.setTitle("Synthetic Northstar Invoice NS-1042")
    pdf.setFillColorRGB(0.08, 0.22, 0.29)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(44, 742, "Northstar Office Supply")
    pdf.setFont("Helvetica", 10)
    pdf.drawString(44, 715, "Synthetic demonstration invoice / no real customer data")
    pdf.setStrokeColorRGB(0.7, 0.79, 0.84)
    pdf.line(44, 696, 568, 696)
    for index, line in enumerate(invoice_text().splitlines()[1:]):
        pdf.drawString(44, 668 - index * 27, line)
    pdf.save()
    fixtures["valid-pdf"] = valid_pdf
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 30)
    except OSError:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    image = Image.new("RGB", (1700, 1150), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(invoice_text("NS-1049").splitlines()):
        draw.text((65, 60 + index * 72), line, font=font, fill=(17, 40, 52))
    image_path = directory / "scanned.png"
    image.save(image_path)
    fixtures["scanned-image"] = image_path
    scanned = directory / "scanned.pdf"
    # Raster-only PDF proves the parser must take its OCR branch.
    image.save(scanned, "PDF", resolution=150.0)
    fixtures["scanned"] = scanned
    email = EmailMessage()
    email["From"] = "supplier@northstar.example"
    email["To"] = "accounts@northstar.example"
    email["Subject"] = "Invoice NS-1050"
    email["Message-ID"] = "<synthetic-ns1050@northstar.example>"
    email.set_content("Please review the attached synthetic invoice.")
    email.add_attachment(
        invoice_text("NS-1050").encode(), maintype="text", subtype="plain", filename="email-invoice.txt"
    )
    message = directory / "invoice.eml"
    message.write_bytes(email.as_bytes())
    fixtures["email"] = message
    # Render a preview fixture from the same synthetic source PDF.
    import pymupdf

    with pymupdf.open(valid_pdf) as document:
        document[0].get_pixmap(matrix=pymupdf.Matrix(1.25, 1.25)).save(directory / "valid-pdf-preview.png")
    return fixtures
