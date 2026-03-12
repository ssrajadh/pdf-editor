"""PDF loading, page rendering, text extraction, and export."""

import asyncio
import io
import json
import logging
import subprocess
from pathlib import Path

import pdfplumber
import pikepdf
from reportlab.pdfgen import canvas

logger = logging.getLogger(__name__)


def get_page_count(pdf_path: Path) -> int:
    """Return the number of pages in a PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


def render_page(pdf_path: Path, page_num: int, output_dir: Path, version: int = 0, dpi: int = 200) -> Path:
    """Render a single PDF page to PNG using pdftoppm (poppler).

    page_num is 1-indexed.
    Returns the path to the rendered PNG.
    """
    output_stem = output_dir / f"page_{page_num}_v{version}"

    subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-r", str(dpi),
            "-f", str(page_num),
            "-l", str(page_num),
            "-singlefile",
            str(pdf_path),
            str(output_stem),
        ],
        check=True,
        capture_output=True,
    )

    actual_output = Path(f"{output_stem}.png")
    if actual_output.exists():
        return actual_output

    raise FileNotFoundError(f"pdftoppm did not produce expected output at {actual_output}")


async def render_page_async(pdf_path: Path, page_num: int, output_dir: Path, version: int = 0, dpi: int = 200) -> Path:
    """Async wrapper around render_page."""
    return await asyncio.to_thread(render_page, pdf_path, page_num, output_dir, version, dpi)


def render_all_pages(pdf_path: Path, output_dir: Path, dpi: int = 200) -> list[Path]:
    """Render all pages of a PDF to PNGs."""
    count = get_page_count(pdf_path)
    paths = []
    for i in range(1, count + 1):
        p = render_page(pdf_path, i, output_dir, version=0, dpi=dpi)
        paths.append(p)
    return paths


async def render_all_pages_async(pdf_path: Path, output_dir: Path, dpi: int = 200) -> list[Path]:
    """Async wrapper around render_all_pages."""
    return await asyncio.to_thread(render_all_pages, pdf_path, output_dir, dpi)


def extract_text(pdf_path: Path, page_num: int) -> dict:
    """Extract text with positional metadata from a PDF page.

    page_num is 1-indexed.
    Returns dict with 'full_text' and 'blocks'.
    """
    with pdfplumber.open(pdf_path) as pdf:
        if page_num < 1 or page_num > len(pdf.pages):
            raise ValueError(f"Page {page_num} out of range (1-{len(pdf.pages)})")

        page = pdf.pages[page_num - 1]
        full_text = page.extract_text() or ""

        blocks = []
        for char in page.chars:
            blocks.append({
                "text": char.get("text", ""),
                "x0": float(char.get("x0", 0)),
                "y0": float(char.get("top", 0)),
                "x1": float(char.get("x1", 0)),
                "y1": float(char.get("bottom", 0)),
                "font_name": char.get("fontname", ""),
                "font_size": float(char.get("size", 0)),
            })

        return {"full_text": full_text, "blocks": blocks}


def get_page_image_path(session_path: Path, page_num: int, version: str = "latest") -> Path:
    """Get the path to a rendered page image.

    If version is "latest", find the highest version number.
    """
    pages_dir = session_path / "pages"

    if version == "latest":
        matches = sorted(pages_dir.glob(f"page_{page_num}_v*.png"))
        if not matches:
            raise FileNotFoundError(f"No rendered image for page {page_num}")
        return matches[-1]

    target = pages_dir / f"page_{page_num}_v{version}.png"
    if not target.exists():
        raise FileNotFoundError(f"Image not found: {target}")
    return target


# ------------------------------------------------------------------
# PDF export — text layer + page merging
# ------------------------------------------------------------------


def get_page_dimensions(pdf_path: Path) -> list[tuple[float, float]]:
    """Return (width, height) in PDF points for each page."""
    with pdfplumber.open(pdf_path) as pdf:
        return [(float(p.width), float(p.height)) for p in pdf.pages]


def build_text_layer_pdf(
    text_blocks: list[dict],
    page_width: float,
    page_height: float,
) -> bytes:
    """Create a single-page PDF with invisible text at the original positions.

    Uses renderMode 3 (invisible) so text is selectable but not visible.
    Coordinates: pdfplumber y0 is from page top; reportlab y is from bottom.
    """
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))

    text_obj = c.beginText()
    text_obj.setTextRenderMode(3)

    current_font = ("Helvetica", 10.0)
    text_obj.setFont(*current_font)

    for block in text_blocks:
        char = block.get("text", "")
        if not char:
            continue

        font_size = block.get("font_size") or 10.0
        x = block["x0"]
        y = page_height - block["y1"]

        desired_font = ("Helvetica", max(font_size, 1.0))
        if desired_font != current_font:
            current_font = desired_font
            text_obj.setFont(*current_font)

        text_obj.setTextOrigin(x, y)
        text_obj.textOut(char)

    c.drawText(text_obj)
    c.showPage()
    c.save()
    return buf.getvalue()


def _build_image_page_pdf(
    image_path: Path,
    page_width: float,
    page_height: float,
) -> bytes:
    """Create a single-page PDF with the image scaled to fill the page."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(page_width, page_height))
    c.drawImage(
        str(image_path), 0, 0, page_width, page_height,
        preserveAspectRatio=False,
    )
    c.showPage()
    c.save()
    return buf.getvalue()


def merge_edited_page(
    image_path: Path,
    text_blocks: list[dict] | None,
    page_width: float,
    page_height: float,
) -> pikepdf.Pdf:
    """Build a replacement PDF page: image background + optional invisible text overlay.

    Returns a single-page pikepdf.Pdf.
    """
    image_pdf_bytes = _build_image_page_pdf(image_path, page_width, page_height)
    merged = pikepdf.open(io.BytesIO(image_pdf_bytes))

    if text_blocks:
        text_pdf_bytes = build_text_layer_pdf(text_blocks, page_width, page_height)
        text_pdf = pikepdf.open(io.BytesIO(text_pdf_bytes))
        merged.pages[0].add_overlay(text_pdf.pages[0])

    return merged


def export_pdf(session_path: Path, metadata: dict) -> Path:
    """Produce the final output PDF with edited pages merged in.

    - Unedited pages pass through byte-identical from the original.
    - Edited pages are replaced with the edited image + preserved text layer.
    """
    original_pdf_path = session_path / "original.pdf"
    output_path = session_path / "output.pdf"
    versions = metadata.get("current_page_versions", {})
    page_count = metadata["page_count"]

    page_dims = get_page_dimensions(original_pdf_path)

    output = pikepdf.open(original_pdf_path)

    replacement_pdfs: list[pikepdf.Pdf] = []

    try:
        for page_num in range(1, page_count + 1):
            version = int(versions.get(str(page_num), 0))
            if version == 0:
                continue

            image_path = session_path / "pages" / f"page_{page_num}_v{version}.png"
            if not image_path.exists():
                logger.warning("Edited image missing for page %d v%d, skipping", page_num, version)
                continue

            width, height = page_dims[page_num - 1]

            text_blocks = _load_text_layer_blocks(session_path, page_num, version)

            replacement = merge_edited_page(image_path, text_blocks, width, height)
            replacement_pdfs.append(replacement)
            output.pages[page_num - 1] = replacement.pages[0]

        output.save(output_path)
    finally:
        output.close()
        for p in replacement_pdfs:
            p.close()

    logger.info("Exported PDF to %s", output_path)
    return output_path


def _load_text_layer_blocks(
    session_path: Path, page_num: int, version: int,
) -> list[dict] | None:
    """Load text layer blocks for a page version. Returns None if stale or missing."""
    version_layer = session_path / "edits" / f"page_{page_num}_v{version}_text.json"
    if version_layer.exists():
        data = json.loads(version_layer.read_text())
        if data.get("stale"):
            return None
        blocks = data.get("blocks", [])
        if blocks:
            return blocks

    original_layer = session_path / "edits" / f"page_{page_num}_text_layer.json"
    if original_layer.exists():
        data = json.loads(original_layer.read_text())
        blocks = data.get("blocks", [])
        if blocks:
            return blocks

    return None


async def export_pdf_async(session_path: Path, metadata: dict) -> Path:
    """Async wrapper around export_pdf."""
    return await asyncio.to_thread(export_pdf, session_path, metadata)
