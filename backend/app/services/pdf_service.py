"""PDF loading, page rendering, and text extraction."""

import asyncio
import subprocess
from pathlib import Path

import pdfplumber


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
