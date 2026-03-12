"""Test visual element description and full PageContext assembly.

Usage:
    cd backend
    .venv/bin/python -m tests.test_visual_description [path/to/test.pdf]

If no PDF is provided, creates a synthetic test page with shapes/text.
"""

import asyncio
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.model_provider import GeminiProvider
from app.services.orchestrator import (
    PageContext,
    describe_visual_elements,
    build_page_context,
    page_context_to_text_blocks_json,
)
from app.storage.session import SessionManager


def create_synthetic_page() -> Image.Image:
    """Create a synthetic PDF-like page image with visual elements for testing."""
    width, height = 1224, 1584  # 612x792 at 200 DPI
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Header background
    draw.rectangle([0, 0, width, 160], fill="#1a237e")
    draw.text((60, 50), "Q3 2025 Revenue Report", fill="white")
    draw.text((60, 100), "Finance Department", fill="#bbdefb")

    # Fake bar chart
    chart_x, chart_y = 60, 300
    draw.text((chart_x, chart_y - 30), "Sales by Region", fill="black")
    bars = [("NA", 360, "#1565c0"), ("EU", 240, "#42a5f5"), ("APAC", 160, "#90caf9"), ("RoW", 80, "#bbdefb")]
    for i, (label, h, color) in enumerate(bars):
        x = chart_x + i * 140
        draw.rectangle([x, chart_y + (400 - h), x + 100, chart_y + 400], fill=color)
        draw.text((x + 30, chart_y + 410), label, fill="black")

    # Fake pie chart (circle with segments)
    pie_cx, pie_cy = 900, 500
    draw.ellipse([pie_cx - 150, pie_cy - 150, pie_cx + 150, pie_cy + 150], fill="#e3f2fd", outline="#1565c0")
    draw.pieslice([pie_cx - 150, pie_cy - 150, pie_cx + 150, pie_cy + 150], 0, 155, fill="#1565c0")
    draw.pieslice([pie_cx - 150, pie_cy - 150, pie_cx + 150, pie_cy + 150], 155, 260, fill="#42a5f5")
    draw.pieslice([pie_cx - 150, pie_cy - 150, pie_cx + 150, pie_cy + 150], 260, 328, fill="#90caf9")
    draw.text((pie_cx - 80, pie_cy + 170), "Revenue Split", fill="black")

    # Horizontal divider
    draw.line([(60, 880), (width - 60, 880)], fill="#bdbdbd", width=2)

    # Key metrics boxes
    for i, (label, value) in enumerate([("Revenue", "$4.2M"), ("Growth", "12%"), ("Margin", "18.5%")]):
        bx = 60 + i * 380
        draw.rectangle([bx, 920, bx + 340, 1040], outline="#1565c0", width=2)
        draw.text((bx + 20, 940), label, fill="#616161")
        draw.text((bx + 20, 975), value, fill="#1a237e")

    # Footer
    draw.line([(60, 1480), (width - 60, 1480)], fill="#bdbdbd", width=1)
    draw.text((60, 1500), "Copyright 2025 Acme Corporation", fill="#9e9e9e")

    return img


async def test_describe_visual_elements():
    """Test the visual element description with a synthetic page."""
    print("=" * 70)
    print("TEST: describe_visual_elements")
    print("=" * 70)

    provider = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.model_timeout_seconds,
    )

    image = create_synthetic_page()
    print(f"Created synthetic page: {image.size[0]}x{image.size[1]}")

    sample_text = """\
Q3 2025 Revenue Report
Finance Department
Sales by Region
NA EU APAC RoW
Revenue Split
Revenue $4.2M
Growth 12%
Margin 18.5%
Copyright 2025 Acme Corporation"""

    print("\nCalling describe_visual_elements...")
    description = await describe_visual_elements(image, sample_text, provider)

    print(f"\nVisual description ({len(description)} chars):")
    print("-" * 40)
    print(description)
    print("-" * 40)

    # Basic validation
    desc_lower = description.lower()
    checks = {
        "mentions chart/graph": any(w in desc_lower for w in ["chart", "graph", "bar"]),
        "mentions color/blue": any(w in desc_lower for w in ["blue", "color", "dark"]),
        "mentions header/banner": any(w in desc_lower for w in ["header", "banner", "top", "background"]),
        "mentions divider/line": any(w in desc_lower for w in ["divider", "line", "separator", "border"]),
    }

    all_passed = True
    for check_name, passed in checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {check_name}")
        if not passed:
            all_passed = False

    return all_passed, description


async def test_with_real_pdf(pdf_path: str):
    """Test with a real PDF file."""
    print("\n" + "=" * 70)
    print(f"TEST: build_page_context with {pdf_path}")
    print("=" * 70)

    provider = GeminiProvider(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        timeout=settings.model_timeout_seconds,
    )

    session_mgr = SessionManager(settings.storage_path)

    # Create a session from the PDF
    pdf_bytes = Path(pdf_path).read_bytes()
    from app.services.pdf_service import get_page_count, render_all_pages

    page_count = get_page_count(Path(pdf_path))
    session_id = session_mgr.create_session(pdf_bytes, Path(pdf_path).name, page_count)
    session_path = session_mgr.get_session_path(session_id)

    print(f"Session: {session_id} ({page_count} pages)")

    # Render pages
    render_all_pages(Path(pdf_path), session_path / "pages")
    print("Pages rendered.")

    # Build context for page 1
    print("\nBuilding PageContext for page 1...")
    ctx = await build_page_context(session_id, 1, provider, session_mgr)

    print(f"\nPageContext:")
    print(f"  page_num: {ctx.page_num}")
    print(f"  dimensions: {ctx.page_width} x {ctx.page_height}")
    print(f"  full_text: {len(ctx.full_text)} chars")
    print(f"  text_blocks: {len(ctx.text_blocks)} blocks")
    print(f"  visual_description ({len(ctx.visual_description)} chars):")
    print(f"    {ctx.visual_description[:300]}{'...' if len(ctx.visual_description) > 300 else ''}")

    # Verify cache works — second call should be instant
    print("\nBuilding PageContext again (should use cache)...")
    ctx2 = await build_page_context(session_id, 1, provider, session_mgr)
    assert ctx2.visual_description == ctx.visual_description, "Cache mismatch!"
    print("  Cache hit confirmed.")

    # Show text blocks JSON preview
    blocks_json = page_context_to_text_blocks_json(ctx)
    print(f"\nText blocks JSON preview ({len(blocks_json)} chars):")
    print(f"  {blocks_json[:200]}...")

    # Cleanup
    session_mgr.cleanup_session(session_id)
    print(f"\nSession {session_id} cleaned up.")
    return True


async def main():
    if not settings.gemini_api_key:
        print("ERROR: GEMINI_API_KEY not set in .env")
        sys.exit(1)

    print(f"Using model: {settings.gemini_model}")
    print(f"Planning model: {settings.planning_model}")

    # Test 1: describe_visual_elements with synthetic image
    passed, _ = await test_describe_visual_elements()
    if not passed:
        print("\nWARNING: Some checks failed (may be acceptable — LLM output varies)")

    # Test 2: Full PageContext with a real PDF if provided
    if len(sys.argv) > 1:
        pdf_path = sys.argv[1]
        if not Path(pdf_path).exists():
            print(f"\nERROR: PDF not found: {pdf_path}")
            sys.exit(1)
        await test_with_real_pdf(pdf_path)
    else:
        print("\nSkipping real PDF test (pass a PDF path as argument to test)")

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
