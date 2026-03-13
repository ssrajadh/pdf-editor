"""Tests for the PyMuPDF redact-and-overlay PDF editor.

Tests programmatic text replacement across different font types,
backgrounds, overflow detection, and match strategies.

Usage:
    cd backend
    python -m tests.test_pdf_editor_v2
"""

import shutil
import sys
import time
from pathlib import Path

import fitz  # PyMuPDF

from app.services.pdf_editor import PdfEditor
from app.services import pdf_service
from app.storage.session import SessionManager

STORAGE = Path(__file__).parent / "test_data_v2"
RESUME_PATH = Path(__file__).parent.parent.parent / "SohamR_Resume_Intern.pdf"

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((name, passed, detail))
    print(f"  [{status}] {name}: {detail}" if detail else f"  [{status}] {name}")


def _create_session(mgr: SessionManager, pdf_path: Path) -> str:
    pdf_bytes = pdf_path.read_bytes()
    page_count = pdf_service.get_page_count(pdf_path)
    session_id = mgr.create_session(pdf_bytes, pdf_path.name, page_count)
    session_path = mgr.get_session_path(session_id)
    pdf_service.render_all_pages(pdf_path, session_path / "pages")
    return session_id


def _read_text(mgr: SessionManager, session_id: str, page: int) -> str:
    session_path = mgr.get_session_path(session_id)
    working = session_path / "working.pdf"
    pdf_path = working if working.exists() else session_path / "original.pdf"
    data = pdf_service.extract_text(pdf_path, page)
    return data["full_text"]


def _create_simple_pdf(path: Path, text: str = "Hello World",
                        font_size: float = 16, bg_color: tuple = None,
                        text_color: tuple = None, bold: bool = False):
    """Create a simple single-page PDF with reportlab."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rlcanvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = rlcanvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    if bg_color:
        c.setFillColorRGB(*bg_color)
        c.rect(0, 0, w, h, fill=True, stroke=False)

    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, font_size)
    if text_color:
        c.setFillColorRGB(*text_color)
    else:
        c.setFillColorRGB(0, 0, 0)

    c.drawString(1 * inch, h - 2 * inch, text)
    c.showPage()
    c.save()


def _create_multi_text_pdf(path: Path):
    """Create a PDF with the same text appearing twice."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as rlcanvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = rlcanvas.Canvas(str(path), pagesize=letter)
    w, h = letter
    c.setFont("Helvetica", 14)
    c.drawString(1 * inch, h - 2 * inch, "Company Name: Acme Corp")
    c.drawString(1 * inch, h - 3 * inch, "Contact: Acme Corp Support")
    c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# Test 1: Basic text replacement
# ---------------------------------------------------------------------------

def test_basic_replace(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 1: Basic text replacement (Hello -> World)")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "test_basic.pdf"
    _create_simple_pdf(pdf_path, "Hello World")

    session_id = _create_session(mgr, pdf_path)
    result = editor.apply_text_replace(session_id, 1, "Hello", "World")

    print(f"  Result: success={result.success}, time={result.time_ms}ms")
    print(f"  Detail: {result.error_message or 'OK'}")
    record("Basic replace: succeeds", result.success)

    text = _read_text(mgr, session_id, 1)
    record("Basic replace: text changed", "World World" in text,
           f"Text contains 'World World': {'World World' in text}")
    record("Basic replace: old text gone", "Hello" not in text,
           f"'Hello' absent: {'Hello' not in text}")

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 2: Background color detection (gray background)
# ---------------------------------------------------------------------------

def test_background_color(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 2: Background color detection (gray)")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "test_gray_bg.pdf"
    _create_simple_pdf(pdf_path, "Test Text", bg_color=(0.85, 0.85, 0.85))

    session_id = _create_session(mgr, pdf_path)

    # Directly test background detection
    doc = fitz.open(str(mgr.get_working_pdf_path(session_id)))
    page = doc[0]
    rects = page.search_for("Test Text")
    record("Gray bg: text found", len(rects) > 0)

    if rects:
        bg = editor._detect_background_color(page, rects[0])
        print(f"  Detected bg color: {bg}")
        is_gray = all(0.7 < c < 1.0 for c in bg) and not all(c > 0.98 for c in bg)
        record("Gray bg: detected non-white bg", is_gray,
               f"bg={tuple(round(c, 2) for c in bg)}")
    doc.close()

    result = editor.apply_text_replace(session_id, 1, "Test Text", "New Text")
    record("Gray bg: replacement succeeds", result.success)

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 3: Overflow detection (replacement too long)
# ---------------------------------------------------------------------------

def test_overflow(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 3: Overflow detection")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "test_overflow.pdf"
    _create_simple_pdf(pdf_path, "Q3")

    session_id = _create_session(mgr, pdf_path)
    result = editor.apply_text_replace(
        session_id, 1, "Q3",
        "Third Quarter Financial Performance Review 2025",
    )

    print(f"  Result: success={result.success}, escalate={result.escalate}")
    print(f"  Error: {result.error_message}")
    record("Overflow: detected", not result.success and result.escalate,
           f"escalate={result.escalate}")
    record("Overflow: mentions width", "wide" in (result.error_message or "").lower(),
           result.error_message or "")

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 4: Duplicate text (match strategy)
# ---------------------------------------------------------------------------

def test_duplicate_text(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 4: Duplicate text — match strategies")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "test_dup.pdf"
    _create_multi_text_pdf(pdf_path)

    # Test: search finds both
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    rects = page.search_for("Acme Corp")
    print(f"  Found {len(rects)} occurrences of 'Acme Corp'")
    record("Dup text: finds multiple", len(rects) >= 2, f"count={len(rects)}")
    doc.close()

    # Test: first_occurrence replaces only first
    session_id = _create_session(mgr, pdf_path)
    result = editor.apply_text_replace(
        session_id, 1, "Acme Corp", "Beta Inc", match_strategy="first_occurrence",
    )
    record("Dup text: first_occurrence succeeds", result.success)

    text = _read_text(mgr, session_id, 1)
    has_beta = "Beta Inc" in text
    has_acme = "Acme Corp" in text
    record("Dup text: first replaced", has_beta, f"'Beta Inc' found: {has_beta}")
    record("Dup text: second kept", has_acme,
           f"'Acme Corp' still present: {has_acme}")

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 5: Bold text
# ---------------------------------------------------------------------------

def test_bold_text(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 5: Bold text replacement")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "test_bold.pdf"
    _create_simple_pdf(pdf_path, "Bold Title", bold=True, font_size=24)

    session_id = _create_session(mgr, pdf_path)

    # Inspect properties before edit
    doc = fitz.open(str(mgr.get_working_pdf_path(session_id)))
    page = doc[0]
    rects = page.search_for("Bold Title")
    if rects:
        blocks = page.get_text("dict", clip=rects[0])["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    flags = span.get("flags", 0)
                    font = span.get("font", "")
                    is_bold = bool(flags & (1 << 4))
                    print(f"  Original: font={font}, flags={flags}, bold={is_bold}")
    doc.close()

    result = editor.apply_text_replace(session_id, 1, "Bold Title", "New Title")
    record("Bold: replacement succeeds", result.success)

    # Verify the new text exists
    text = _read_text(mgr, session_id, 1)
    record("Bold: new text present", "New Title" in text)

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 6: Real resume (CID fonts)
# ---------------------------------------------------------------------------

def test_real_resume(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 6: Real resume (CID fonts)")
    print(f"{'='*60}")

    if not RESUME_PATH.exists():
        print(f"  SKIP: Resume not found at {RESUME_PATH}")
        return

    # Analyze the PDF structure first
    doc = fitz.open(str(RESUME_PATH))
    page = doc[0]
    print(f"  Pages: {len(doc)}")
    print(f"  Page size: {page.rect.width} x {page.rect.height}")

    # Get font info
    fonts = page.get_fonts()
    print(f"  Fonts ({len(fonts)}):")
    for font in fonts:
        print(f"    {font}")

    # Extract text via PyMuPDF
    full_text = page.get_text("text")
    print(f"  Text extracted: {len(full_text)} chars")
    print(f"  First 200 chars: {full_text[:200]!r}")
    doc.close()

    # Find the name in the text
    import re
    name_match = re.match(r"([A-Z][a-z]+ [A-Z][a-z]+)", full_text)
    if name_match:
        name = name_match.group(1)
        print(f"\n  Detected name: {name!r}")
    else:
        name = full_text.split("\n")[0].strip()
        print(f"\n  Using first line as name: {name!r}")

    session_id = _create_session(mgr, RESUME_PATH)

    # Check that PyMuPDF can find the name
    doc = fitz.open(str(mgr.get_working_pdf_path(session_id)))
    page = doc[0]
    rects = page.search_for(name)
    print(f"  search_for({name!r}): {len(rects)} rects found")
    for i, r in enumerate(rects):
        print(f"    [{i}] {r}")
    record("Resume: text found by search_for", len(rects) > 0,
           f"{len(rects)} rects for {name!r}")

    if rects:
        props = editor._get_text_properties(page, rects[0], name)
        print(f"  Text properties: {props}")
        if props:
            from app.services.pdf_editor import _match_font
            matched = _match_font(props["font"], props["flags"])
            print(f"  Matched standard font: {matched}")
            record("Resume: font matched", bool(matched), f"matched={matched}")

            replacement_width = fitz.get_text_length(
                "Test Name", fontname=matched, fontsize=props["size"],
            )
            original_width = rects[0].width
            print(f"  Width check: replacement={replacement_width:.0f}, original={original_width:.0f}")
            record("Resume: replacement fits",
                   replacement_width <= original_width * 1.15,
                   f"{replacement_width:.0f} vs {original_width:.0f}")
    doc.close()

    # The actual replacement
    t0 = time.monotonic()
    result = editor.apply_text_replace(session_id, 1, name, "Test Name")
    elapsed = int((time.monotonic() - t0) * 1000)

    print(f"\n  --- REPLACEMENT RESULT ---")
    print(f"  Success: {result.success}")
    print(f"  Time: {elapsed}ms")
    print(f"  Error: {result.error_message}")

    record("Resume: replacement succeeds", result.success,
           f"time={elapsed}ms, error={result.error_message}")

    if result.success:
        text_after = _read_text(mgr, session_id, 1)
        record("Resume: new text present", "Test Name" in text_after,
               f"'Test Name' in text: {'Test Name' in text_after}")
        record("Resume: old name gone", name not in text_after,
               f"'{name}' absent: {name not in text_after}")
        record("Resume: fast (<1s)", elapsed < 1000,
               f"{elapsed}ms")

    # Try replacing a job title
    doc = fitz.open(str(mgr.get_working_pdf_path(session_id)))
    page = doc[0]
    job_search = page.search_for("SOFTWARE ENGINEER")
    print(f"\n  search_for('SOFTWARE ENGINEER'): {len(job_search)} rects")
    doc.close()

    if job_search:
        result2 = editor.apply_text_replace(
            session_id, 1,
            "SOFTWARE ENGINEER",
            "LEAD ENGINEER",
            match_strategy="first_occurrence",
        )
        print(f"  Job title replace: success={result2.success}, time={result2.time_ms}ms")
        record("Resume: job title replace", result2.success,
               f"'SOFTWARE ENGINEER' -> 'LEAD ENGINEER', time={result2.time_ms}ms")

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True)

    mgr = SessionManager(STORAGE)
    editor = PdfEditor(session_manager=mgr)

    test_basic_replace(mgr, editor)
    test_background_color(mgr, editor)
    test_overflow(mgr, editor)
    test_duplicate_text(mgr, editor)
    test_bold_text(mgr, editor)
    test_real_resume(mgr, editor)

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    for name, p, detail in results:
        status = "PASS" if p else "FAIL"
        print(f"  [{status}] {name}: {detail}" if detail else f"  [{status}] {name}")
    print(f"\n  {passed}/{passed + failed} tests passed")

    if STORAGE.exists():
        shutil.rmtree(STORAGE)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
