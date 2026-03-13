"""Generate a test PDF that uses simple Type1 fonts (programmatically editable)."""

from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

OUTPUT = Path(__file__).parent / "fixtures" / "test_document.pdf"


def generate():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUTPUT), pagesize=letter)
    w, h = letter

    # Title
    c.setFont("Helvetica-Bold", 24)
    c.drawString(1 * inch, h - 1.2 * inch, "Q3 2025 Revenue Report")

    # Subtitle
    c.setFont("Helvetica", 14)
    c.drawString(1 * inch, h - 1.7 * inch, "Prepared by: John Smith")

    # Divider
    c.setStrokeColorRGB(0.3, 0.3, 0.3)
    c.line(1 * inch, h - 1.9 * inch, w - 1 * inch, h - 1.9 * inch)

    # Section header
    c.setFont("Helvetica-Bold", 16)
    c.drawString(1 * inch, h - 2.5 * inch, "Financial Highlights")

    # Body text
    c.setFont("Helvetica", 11)
    y = h - 3.0 * inch
    lines = [
        "Total Revenue: $1.2M for Q3 2025",
        "Year-over-Year Growth: 45% compared to Q3 2024",
        "Operating Margin: 18.5% (up from 15.2%)",
        "New Customers Acquired: 847 enterprise accounts",
        "",
        "The Q3 2025 quarter exceeded expectations across all key metrics.",
        "Revenue growth was primarily driven by expansion in the EMEA region,",
        "with notable contributions from new product lines launched in Q2 2025.",
        "",
        "Updated: September 2025",
        "",
        "Copyright 2025 Acme Corporation. All rights reserved.",
    ]
    for line in lines:
        if line:
            c.drawString(1 * inch, y, line)
        y -= 18

    c.showPage()
    c.save()
    print(f"Generated: {OUTPUT} ({OUTPUT.stat().st_size} bytes)")


if __name__ == "__main__":
    generate()
