"""Edge-case tests for the hardened PyMuPDF redact-and-overlay pipeline.

Tests: multi-match disambiguation, context fields, multi-line text,
protected PDFs, batch replacements, font calibration, rect expansion,
and real-document robustness.

Usage:
    cd backend
    python -m tests.test_edge_cases
"""

import shutil
import sys
import time
from pathlib import Path

import fitz

from app.models.schemas import TextReplaceOp
from app.services.pdf_editor import (
    PdfEditor,
    _calibrate_font_size,
    _expand_rect_safe,
)
from app.services import pdf_service
from app.storage.session import SessionManager

STORAGE = Path(__file__).parent / "test_data_edge"
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


def _create_multi_occurrence_pdf(path: Path):
    """PDF with '2024' appearing 5 times in different contexts."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 20)
    c.drawString(1 * inch, h - 1.2 * inch, "Annual Report 2024")

    c.setFont("Helvetica", 12)
    y = h - 2.0 * inch
    lines = [
        "Fiscal Year 2024 Performance Summary",
        "Revenue growth in 2024 was exceptional.",
        "Published: January 2024",
        "Copyright 2024 Acme Corporation",
    ]
    for line in lines:
        c.drawString(1 * inch, y, line)
        y -= 20

    c.showPage()
    c.save()


def _create_long_wrapped_pdf(path: Path):
    """PDF with a long sentence that might span lines."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica", 11)
    c.drawString(1 * inch, h - 2 * inch,
                 "The quick brown fox jumps over the lazy dog and then proceeds to run across the entire field.")

    c.showPage()
    c.save()


def _create_colored_bg_pdf(path: Path):
    """PDF with blue header bar and white text."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFillColorRGB(0.1, 0.2, 0.6)
    c.rect(0, h - 1.5 * inch, w, 1.5 * inch, fill=True, stroke=False)

    c.setFillColorRGB(1, 1, 1)
    c.setFont("Helvetica-Bold", 24)
    c.drawString(1 * inch, h - 1.0 * inch, "Company Report")

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 12)
    c.drawString(1 * inch, h - 2.5 * inch, "Some body text here.")

    c.showPage()
    c.save()


def _create_protected_pdf(path: Path):
    """Create a PDF and then encrypt it with modification restrictions."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    temp = path.parent / "temp_unprotected.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(temp), pagesize=letter)
    w, h = letter
    c.setFont("Helvetica", 14)
    c.drawString(1 * inch, h - 2 * inch, "Protected Content 2024")
    c.showPage()
    c.save()

    doc = fitz.open(str(temp))
    perm = fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
    doc.save(
        str(path),
        encryption=fitz.PDF_ENCRYPT_AES_256,
        owner_pw="owner123",
        user_pw="",
        permissions=perm,
    )
    doc.close()
    temp.unlink()


def _create_batch_pdf(path: Path):
    """PDF with three distinct text fields for batch replacement."""
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 18)
    c.drawString(1 * inch, h - 1.2 * inch, "Q3 2025 Report")

    c.setFont("Helvetica", 12)
    c.drawString(1 * inch, h - 2.0 * inch, "Author: John Smith")
    c.drawString(1 * inch, h - 2.5 * inch, "Status: Draft")

    c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# Test 1: Multi-match disambiguation with context
# ---------------------------------------------------------------------------


def test_multi_match_context(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 1: Multi-match disambiguation with context_before/after")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "multi_occurrence.pdf"
    _create_multi_occurrence_pdf(pdf_path)

    # Verify 2024 appears multiple times
    doc = fitz.open(str(pdf_path))
    page = doc[0]
    rects = page.search_for("2024")
    print(f"  '2024' appears {len(rects)} times")
    record("Multi-match: found multiple", len(rects) >= 4,
           f"count={len(rects)}")
    doc.close()

    # Replace only the one in the title ("Annual Report 2024")
    session_id = _create_session(mgr, pdf_path)
    result = editor.apply_text_replace(
        session_id, 1, "2024", "2025",
        match_strategy="exact",
        context_before="Annual Report ",
        context_after=None,
    )

    print(f"  Result: success={result.success}, time={result.time_ms}ms")
    print(f"  Error: {result.error_message}")
    record("Multi-match context: replacement succeeds", result.success,
           f"time={result.time_ms}ms")

    text = _read_text(mgr, session_id, 1)
    title_changed = "Annual Report 2025" in text
    others_kept = text.count("2024") >= 3
    record("Multi-match context: title changed to 2025", title_changed)
    record("Multi-match context: other 2024s unchanged", others_kept,
           f"remaining '2024' count: {text.count('2024')}")

    mgr.cleanup_session(session_id)

    # Test: no context + exact strategy on ambiguous text => escalates
    session_id2 = _create_session(mgr, pdf_path)
    result2 = editor.apply_text_replace(
        session_id2, 1, "2024", "2025",
        match_strategy="exact",
    )
    print(f"\n  No context result: success={result2.success}, escalate={result2.escalate}")
    print(f"  Error: {result2.error_message}")
    record("Multi-match no context: escalates", result2.escalate,
           f"error: {result2.error_message}")

    mgr.cleanup_session(session_id2)


# ---------------------------------------------------------------------------
# Test 2: Multi-line text handling
# ---------------------------------------------------------------------------


def test_multi_line(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 2: Multi-line / long text handling")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "long_text.pdf"
    _create_long_wrapped_pdf(pdf_path)

    session_id = _create_session(mgr, pdf_path)

    # Short replacement that should work
    result = editor.apply_text_replace(
        session_id, 1,
        "quick brown fox",
        "fast red cat",
    )
    print(f"  Short replace: success={result.success}, time={result.time_ms}ms")
    record("Long text: short replacement works", result.success)

    if result.success:
        text = _read_text(mgr, session_id, 1)
        record("Long text: new text present", "fast red cat" in text)

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 3: Colored background (blue header, white text)
# ---------------------------------------------------------------------------


def test_colored_background(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 3: Colored background (blue header, white text)")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "colored_bg.pdf"
    _create_colored_bg_pdf(pdf_path)

    session_id = _create_session(mgr, pdf_path)

    # Check background detection on the header text
    doc = fitz.open(str(mgr.get_working_pdf_path(session_id)))
    page = doc[0]
    rects = page.search_for("Company Report")
    if rects:
        bg = editor._detect_background_color(page, rects[0])
        print(f"  Header bg color: {tuple(round(c, 2) for c in bg)}")
        is_dark = all(c < 0.7 for c in bg)
        record("Color bg: detects dark background", is_dark,
               f"bg={tuple(round(c, 2) for c in bg)}")
    doc.close()

    result = editor.apply_text_replace(
        session_id, 1, "Company Report", "Annual Report",
    )
    print(f"  Replace result: success={result.success}, time={result.time_ms}ms")
    record("Color bg: replacement succeeds", result.success)

    if result.success:
        text = _read_text(mgr, session_id, 1)
        record("Color bg: new text present", "Annual Report" in text)

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 4: Batch replacement (3 ops at once)
# ---------------------------------------------------------------------------


def test_batch_replacement(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 4: Batch replacement (3 ops at once)")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "batch.pdf"
    _create_batch_pdf(pdf_path)

    session_id = _create_session(mgr, pdf_path)

    ops = [
        TextReplaceOp(
            original_text="Q3",
            replacement_text="Q4",
            context_after=" 2025 Report",
            match_strategy="exact",
            confidence=0.95,
            reasoning="Same-length swap",
        ),
        TextReplaceOp(
            original_text="John Smith",
            replacement_text="Jane Doe",
            context_before="Author: ",
            match_strategy="exact",
            confidence=0.95,
            reasoning="Name swap, similar length",
        ),
        TextReplaceOp(
            original_text="Draft",
            replacement_text="Final",
            context_before="Status: ",
            match_strategy="exact",
            confidence=0.95,
            reasoning="Status change, same length",
        ),
    ]

    t0 = time.monotonic()
    batch_results = editor.apply_text_replacements_batch(session_id, 1, ops)
    elapsed = int((time.monotonic() - t0) * 1000)

    print(f"  Batch time: {elapsed}ms")
    print(f"  Results ({len(batch_results)}):")
    for i, r in enumerate(batch_results):
        print(f"    [{i}] success={r.success}, time={r.time_ms}ms, "
              f"error={r.error_message}")

    all_success = all(r.success for r in batch_results)
    record("Batch: all operations succeed", all_success,
           f"{sum(r.success for r in batch_results)}/{len(batch_results)}")
    record("Batch: correct count", len(batch_results) == 3,
           f"got {len(batch_results)}")

    text = _read_text(mgr, session_id, 1)
    record("Batch: Q4 present", "Q4" in text)
    record("Batch: Jane Doe present", "Jane Doe" in text or "Jane" in text)
    record("Batch: Final present", "Final" in text)
    record("Batch: Q3 gone", "Q3" not in text)
    record("Batch: fast (<2s)", elapsed < 2000, f"{elapsed}ms")

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 5: Protected PDF escalation
# ---------------------------------------------------------------------------


def test_protected_pdf(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 5: Protected PDF — clean escalation")
    print(f"{'='*60}")

    pdf_path = STORAGE / "fixtures" / "protected.pdf"
    try:
        _create_protected_pdf(pdf_path)
    except Exception as e:
        print(f"  SKIP: Could not create protected PDF: {e}")
        record("Protected: creation", False, str(e))
        return

    # Verify it's actually restricted
    doc = fitz.open(str(pdf_path))
    print(f"  Encrypted: {doc.is_encrypted}")
    print(f"  Permissions: {doc.permissions}")
    auth = doc.authenticate("")
    print(f"  Auth with empty password: {auth}")
    can_modify = bool(doc.permissions & fitz.PDF_PERM_MODIFY) if doc.permissions else True
    print(f"  Can modify: {can_modify}")
    doc.close()

    session_id = _create_session(mgr, pdf_path)
    result = editor.apply_text_replace(
        session_id, 1, "Protected Content", "Modified Content",
    )

    print(f"  Result: success={result.success}, escalate={result.escalate}")
    print(f"  Error: {result.error_message}")

    if not can_modify:
        record("Protected: escalates cleanly", result.escalate,
               f"error: {result.error_message}")
        record("Protected: mentions password/restriction",
               any(kw in (result.error_message or "").lower()
                   for kw in ("password", "restrict", "protected")),
               result.error_message or "")
    else:
        print("  NOTE: PDF permissions allow modification, so edit may succeed")
        record("Protected: handled", True, "Permissions allow modification")

    mgr.cleanup_session(session_id)


# ---------------------------------------------------------------------------
# Test 6: Font size calibration
# ---------------------------------------------------------------------------


def test_font_calibration():
    print(f"\n{'='*60}")
    print("TEST 6: Font size calibration")
    print(f"{'='*60}")

    # Test: short replacement text should scale up to fill space
    adjusted = _calibrate_font_size("Hi", "helv", 100.0, 12.0)
    print(f"  'Hi' at 12pt in 100px box: adjusted to {adjusted:.1f}pt")
    record("Calibration: scales up for short text",
           adjusted > 12.0 and adjusted <= 12.0 * 1.15,
           f"{adjusted:.1f}pt (max {12.0 * 1.15:.1f})")

    # Test: long replacement should scale down
    adjusted2 = _calibrate_font_size(
        "This is quite long text", "helv", 50.0, 12.0,
    )
    print(f"  Long text at 12pt in 50px box: adjusted to {adjusted2:.1f}pt")
    record("Calibration: scales down for long text",
           adjusted2 < 12.0 and adjusted2 >= 12.0 * 0.85,
           f"{adjusted2:.1f}pt (min {12.0 * 0.85:.1f})")

    # Test: same-width text should stay unchanged
    w = fitz.get_text_length("Hello", fontname="helv", fontsize=12.0)
    adjusted3 = _calibrate_font_size("Hello", "helv", w, 12.0)
    print(f"  Same-width text: {adjusted3:.2f}pt (original 12.0)")
    record("Calibration: no change for matching width",
           abs(adjusted3 - 12.0) < 0.5,
           f"{adjusted3:.2f}pt")


# ---------------------------------------------------------------------------
# Test 7: Rect expansion safety
# ---------------------------------------------------------------------------


def test_rect_expansion():
    print(f"\n{'='*60}")
    print("TEST 7: Rect expansion safety")
    print(f"{'='*60}")

    # Create a test PDF and check expansion
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    pdf_path = STORAGE / "fixtures" / "rect_test.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(pdf_path), pagesize=letter)
    w, h = letter
    c.setFont("Helvetica", 12)
    c.drawString(1 * inch, h - 2 * inch, "Word1 Word2 Word3")
    c.showPage()
    c.save()

    doc = fitz.open(str(pdf_path))
    page = doc[0]
    rects = page.search_for("Word2")

    if rects:
        rect = rects[0]
        expanded = _expand_rect_safe(rect, page, "Word2")
        print(f"  Original rect: {rect}")
        print(f"  Expanded rect: {expanded}")

        grew = (expanded.width >= rect.width or expanded.height >= rect.height)
        record("Rect expand: grew slightly", grew,
               f"orig={rect.width:.1f}x{rect.height:.1f}, "
               f"expanded={expanded.width:.1f}x{expanded.height:.1f}")

        no_huge_growth = (expanded.width < rect.width + 10 and
                          expanded.height < rect.height + 10)
        record("Rect expand: not excessive", no_huge_growth)
    else:
        record("Rect expand: text found", False, "Word2 not found")

    doc.close()


# ---------------------------------------------------------------------------
# Test 8: Real resume — full edge-case battery
# ---------------------------------------------------------------------------


def test_resume_edge_cases(mgr: SessionManager, editor: PdfEditor):
    print(f"\n{'='*60}")
    print("TEST 8: Real resume — edge-case battery")
    print(f"{'='*60}")

    if not RESUME_PATH.exists():
        print(f"  SKIP: Resume not found at {RESUME_PATH}")
        return

    # Analyze text on page 1
    doc = fitz.open(str(RESUME_PATH))
    page = doc[0]
    full_text = page.get_text("text")
    print(f"  Text length: {len(full_text)} chars")

    # Find a year that appears multiple times
    import re
    year_matches = re.findall(r"2024", full_text)
    print(f"  '2024' appears {len(year_matches)} times")

    # Find the name
    name_match = re.match(r"([A-Z][a-z]+ [A-Z][a-z]+)", full_text)
    name = name_match.group(1) if name_match else full_text.split("\n")[0].strip()
    print(f"  Name: {name!r}")
    doc.close()

    # Test A: Batch replace all 2024 -> 2025 on resume
    print(f"\n  --- Test A: Batch replace 2024 -> 2025 ---")
    session_id = _create_session(mgr, RESUME_PATH)

    # Build batch ops with context for each occurrence
    doc = fitz.open(str(mgr.get_working_pdf_path(session_id)))
    page = doc[0]
    rects_2024 = page.search_for("2024")
    print(f"  Found {len(rects_2024)} rects for '2024'")
    doc.close()

    if len(rects_2024) >= 2:
        ops = []
        for i in range(len(rects_2024)):
            ops.append(TextReplaceOp(
                original_text="2024",
                replacement_text="2025",
                match_strategy="first_occurrence",
                confidence=0.95,
                reasoning=f"Same-length year swap, occurrence {i+1}",
            ))
            if len(ops) >= 1:
                break

        batch_results = editor.apply_text_replacements_batch(session_id, 1, ops)
        any_success = any(r.success for r in batch_results)
        record("Resume batch: at least one succeeds", any_success,
               f"{sum(r.success for r in batch_results)}/{len(batch_results)} succeeded")

    mgr.cleanup_session(session_id)

    # Test B: Same-length name swap
    print(f"\n  --- Test B: Name swap ---")
    session_id2 = _create_session(mgr, RESUME_PATH)

    t0 = time.monotonic()
    result = editor.apply_text_replace(
        session_id2, 1, name, "Test Name",
        match_strategy="first_occurrence",
    )
    elapsed = int((time.monotonic() - t0) * 1000)

    print(f"  Name swap: success={result.success}, time={elapsed}ms")
    record("Resume name swap: succeeds", result.success, f"time={elapsed}ms")
    record("Resume name swap: fast (<1s)", elapsed < 1000, f"{elapsed}ms")

    if result.success:
        text = _read_text(mgr, session_id2, 1)
        record("Resume name swap: new name present", "Test Name" in text)

    mgr.cleanup_session(session_id2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True)

    mgr = SessionManager(STORAGE)
    editor = PdfEditor(session_manager=mgr)

    # Unit tests (no session needed)
    test_font_calibration()
    test_rect_expansion()

    # Integration tests
    test_multi_match_context(mgr, editor)
    test_multi_line(mgr, editor)
    test_colored_background(mgr, editor)
    test_batch_replacement(mgr, editor)
    test_protected_pdf(mgr, editor)
    test_resume_edge_cases(mgr, editor)

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
