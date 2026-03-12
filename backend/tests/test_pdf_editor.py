"""Tests for the programmatic PDF text editing engine.

Usage:
    cd backend
    .venv/bin/python -m tests.test_pdf_editor
"""

import json
import sys
import shutil
from pathlib import Path

from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.pdf_editor import PdfEditor
from app.services import pdf_service
from app.storage.session import SessionManager

# Use a temp directory for test sessions
TEST_DATA_DIR = Path("/tmp/test_pdf_editor_data")


def setup_test_session(session_mgr: SessionManager, pdf_path: Path) -> str:
    """Create a session and render pages from a test PDF."""
    pdf_bytes = pdf_path.read_bytes()
    page_count = pdf_service.get_page_count(pdf_path)
    session_id = session_mgr.create_session(pdf_bytes, pdf_path.name, page_count)
    session_path = session_mgr.get_session_path(session_id)
    pdf_service.render_all_pages(pdf_path, session_path / "pages")
    return session_id


def create_simple_pdf(path: Path) -> None:
    """Create a PDF with known text content for testing."""
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 24)
    c.drawString(72, h - 72, "Q3 2025 Revenue Report")

    c.setFont("Helvetica", 12)
    c.drawString(72, h - 100, "Prepared by: Finance Department")
    c.drawString(72, h - 120, "Total Revenue: $4.2M")
    c.drawString(72, h - 140, "Growth Rate: 12% YoY")
    c.drawString(72, h - 160, "Updated: 2025")

    c.setFillColorRGB(0, 0, 1)
    c.drawString(72, h - 180, "Blue text for style test")

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 10)
    c.drawString(72, 40, "Copyright 2025 Acme Corporation")

    c.save()


def create_multi_occurrence_pdf(path: Path) -> None:
    """Create a PDF where the same text appears multiple times."""
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica", 12)
    c.drawString(72, h - 72, "Annual Report 2024")
    c.drawString(72, h - 92, "Fiscal Year 2024 Performance")
    c.drawString(72, h - 112, "Copyright 2024 Acme Corp")
    c.drawString(72, h - 132, "Published: Dec 2024")

    c.save()


def verify_pdf_text(pdf_path: Path, page_num: int, expected: str, label: str) -> bool:
    """Check that expected text appears in the PDF page."""
    import pikepdf
    pdf = pikepdf.open(pdf_path)
    page = pdf.pages[page_num - 1]
    content = pikepdf.parse_content_stream(page)

    all_text = ""
    for operands, operator in content:
        op_str = str(operator)
        if op_str == "Tj" and operands:
            obj = operands[0]
            if isinstance(obj, pikepdf.String):
                all_text += bytes(obj).decode("latin-1", errors="replace")
        elif op_str == "TJ" and operands:
            arr = operands[0] if len(operands) == 1 else operands
            for item in arr:
                if isinstance(item, pikepdf.String):
                    all_text += bytes(item).decode("latin-1", errors="replace")

    pdf.close()

    found = expected in all_text
    status = "PASS" if found else "FAIL"
    print(f"    [{status}] {label}: '{expected}' {'found' if found else 'NOT FOUND'} in PDF")
    if not found:
        print(f"           All text: {all_text[:300]}")
    return found


def verify_text_absent(pdf_path: Path, page_num: int, text: str, label: str) -> bool:
    """Check that text does NOT appear in the PDF page."""
    import pikepdf
    pdf = pikepdf.open(pdf_path)
    page = pdf.pages[page_num - 1]
    content = pikepdf.parse_content_stream(page)

    all_text = ""
    for operands, operator in content:
        op_str = str(operator)
        if op_str == "Tj" and operands:
            obj = operands[0]
            if isinstance(obj, pikepdf.String):
                all_text += bytes(obj).decode("latin-1", errors="replace")

    pdf.close()

    absent = text not in all_text
    status = "PASS" if absent else "FAIL"
    print(f"    [{status}] {label}: '{text}' {'absent' if absent else 'STILL PRESENT'}")
    return absent


# ── Test cases ───────────────────────────────────────────────────────────


def test_same_length_replace(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test same-length text replacement: Q3 -> Q4."""
    print("\n" + "=" * 70)
    print("TEST: Same-length text replacement (Q3 -> Q4)")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    result = editor.apply_text_replace(
        session_id, 1, "Q3", "Q4", match_strategy="first_occurrence",
    )

    print(f"  Result: success={result.success}, time={result.time_ms}ms, "
          f"chars_changed={result.characters_changed}")
    if result.error_message:
        print(f"  Error: {result.error_message}")

    issues = []
    if not result.success:
        issues.append(f"Replace failed: {result.error_message}")

    working_pdf = session_mgr.get_working_pdf_path(session_id)
    original_pdf = session_mgr.get_session_path(session_id) / "original.pdf"

    if not verify_pdf_text(working_pdf, 1, "Q4", "Working PDF has Q4"):
        issues.append("Q4 not found in working PDF")
    if not verify_pdf_text(original_pdf, 1, "Q3", "Original PDF still has Q3"):
        issues.append("Original PDF was modified")
    if not verify_text_absent(working_pdf, 1, "Q3 2025", "Working PDF no longer has 'Q3 2025'"):
        issues.append("Q3 still present in working PDF")

    # Check re-rendered page image exists
    session_path = session_mgr.get_session_path(session_id)
    metadata = session_mgr.get_metadata(session_id)
    version = metadata["current_page_versions"]["1"]
    new_image = session_path / "pages" / f"page_1_v{version}.png"
    if new_image.exists():
        img = Image.open(new_image)
        print(f"    [PASS] New version image: {new_image.name} ({img.size[0]}x{img.size[1]})")
    else:
        issues.append(f"New version image not found: {new_image}")
        print(f"    [FAIL] New version image not found")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_shorter_replace(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test shorter replacement: 'Revenue Report' -> 'Report'."""
    print("\n" + "=" * 70)
    print("TEST: Shorter text replacement (Revenue Report -> Report)")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    result = editor.apply_text_replace(
        session_id, 1, "Revenue Report", "Report", match_strategy="first_occurrence",
    )

    print(f"  Result: success={result.success}, time={result.time_ms}ms")

    issues = []
    if not result.success:
        issues.append(f"Replace failed: {result.error_message}")

    working_pdf = session_mgr.get_working_pdf_path(session_id)
    if not verify_pdf_text(working_pdf, 1, "Q3 2025 Report", "Shorter text applied"):
        issues.append("Shortened text not correct")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_overflow_escalation(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test that long replacements escalate properly."""
    print("\n" + "=" * 70)
    print("TEST: Overflow escalation (Hi -> Hello World Foo Bar Baz)")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    result = editor.apply_text_replace(
        session_id, 1, "Q3", "Quarter Three of the Year Two Thousand", match_strategy="first_occurrence",
    )

    print(f"  Result: success={result.success}, escalate={result.escalate}, "
          f"error={result.error_message}")

    issues = []
    if result.success:
        issues.append("Should have failed due to overflow")
    if not result.escalate:
        issues.append("Should have set escalate=True")

    # Original should be untouched
    original_pdf = session_mgr.get_session_path(session_id) / "original.pdf"
    if not verify_pdf_text(original_pdf, 1, "Q3", "Original untouched"):
        issues.append("Original was modified")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_multiple_occurrences(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test replacing all occurrences of '2024' with '2025'."""
    print("\n" + "=" * 70)
    print("TEST: Multiple occurrences (all 2024 -> 2025)")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "multi.pdf"
    create_multi_occurrence_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    # Use "exact" strategy which should match all occurrences of text containing "2024"
    # We need to do individual replacements with context for "exact" matching
    texts_to_replace = [
        ("Annual Report 2024", "Annual Report 2025"),
        ("Fiscal Year 2024 Performance", "Fiscal Year 2025 Performance"),
        ("Copyright 2024 Acme Corp", "Copyright 2025 Acme Corp"),
        ("Published: Dec 2024", "Published: Dec 2025"),
    ]

    issues = []
    for old, new in texts_to_replace:
        result = editor.apply_text_replace(
            session_id, 1, old, new, match_strategy="exact",
        )
        print(f"  '{old}' -> '{new}': success={result.success}")
        if not result.success:
            issues.append(f"Failed: {old} -> {new}: {result.error_message}")

    working_pdf = session_mgr.get_working_pdf_path(session_id)
    for _, new_text in texts_to_replace:
        if not verify_pdf_text(working_pdf, 1, new_text, f"Has '{new_text}'"):
            issues.append(f"'{new_text}' not found")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_text_not_found(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test graceful handling when text is not found."""
    print("\n" + "=" * 70)
    print("TEST: Text not found (nonexistent -> something)")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    result = editor.apply_text_replace(
        session_id, 1, "NONEXISTENT_TEXT_12345", "something",
        match_strategy="exact",
    )

    print(f"  Result: success={result.success}, escalate={result.escalate}")

    issues = []
    if result.success:
        issues.append("Should have failed — text doesn't exist")
    if not result.escalate:
        issues.append("Should escalate to visual")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_style_color_change(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test changing text color."""
    print("\n" + "=" * 70)
    print("TEST: Style change — color")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    result = editor.apply_style_change(
        session_id, 1, "Q3 2025 Revenue Report",
        {"color": "#FF0000"},
    )

    print(f"  Result: success={result.success}, applied={result.changes_applied}, "
          f"time={result.time_ms}ms")

    issues = []
    if not result.success:
        issues.append(f"Style change failed: {result.error_message}")
    if result.changes_applied.get("color") != "#FF0000":
        issues.append("Color not in applied changes")

    # Verify color operator was inserted in the content stream
    working_pdf = session_mgr.get_working_pdf_path(session_id)
    import pikepdf
    pdf = pikepdf.open(working_pdf)
    content = pikepdf.parse_content_stream(pdf.pages[0])
    found_red = False
    for operands, operator in content:
        if str(operator) == "rg" and len(operands) == 3:
            r = float(str(operands[0]))
            g = float(str(operands[1]))
            b = float(str(operands[2]))
            if r > 0.9 and g < 0.1 and b < 0.1:
                found_red = True
                break
    pdf.close()

    if found_red:
        print("    [PASS] Red color operator (rg) found in content stream")
    else:
        issues.append("Red color operator not found")
        print("    [FAIL] Red color operator not found")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_style_font_size(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test changing font size."""
    print("\n" + "=" * 70)
    print("TEST: Style change — font size")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    result = editor.apply_style_change(
        session_id, 1, "Q3 2025 Revenue Report",
        {"font_size": 30},
    )

    print(f"  Result: success={result.success}, applied={result.changes_applied}")

    issues = []
    if not result.success:
        issues.append(f"Font size change failed: {result.error_message}")
    if result.changes_applied.get("font_size") != 30.0:
        issues.append("font_size not in applied changes")

    # Verify Tf was updated
    working_pdf = session_mgr.get_working_pdf_path(session_id)
    import pikepdf
    pdf = pikepdf.open(working_pdf)
    content = pikepdf.parse_content_stream(pdf.pages[0])
    found_30 = False
    for operands, operator in content:
        if str(operator) == "Tf" and len(operands) >= 2:
            size = float(str(operands[1]))
            if abs(size - 30.0) < 0.1:
                found_30 = True
                break
    pdf.close()

    if found_30:
        print("    [PASS] Font size 30 found in Tf operator")
    else:
        issues.append("Font size 30 not found in content stream")
        print("    [FAIL] Font size 30 not found")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_original_untouched(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Verify original.pdf is never modified, only working.pdf."""
    print("\n" + "=" * 70)
    print("TEST: Original PDF untouched after edits")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    original_pdf = session_mgr.get_session_path(session_id) / "original.pdf"
    original_bytes_before = original_pdf.read_bytes()

    editor.apply_text_replace(session_id, 1, "Q3", "Q4", match_strategy="first_occurrence")
    editor.apply_style_change(session_id, 1, "Growth Rate", {"color": "#00FF00"})

    original_bytes_after = original_pdf.read_bytes()

    issues = []
    if original_bytes_before != original_bytes_after:
        issues.append("original.pdf was modified!")
        print("    [FAIL] original.pdf bytes changed")
    else:
        print("    [PASS] original.pdf bytes identical")

    working_pdf = session_mgr.get_working_pdf_path(session_id)
    if not verify_pdf_text(working_pdf, 1, "Q4", "Working has Q4"):
        issues.append("Working PDF doesn't have edits")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


def test_sequential_edits(session_mgr: SessionManager, editor: PdfEditor) -> bool:
    """Test multiple sequential edits on the same working PDF."""
    print("\n" + "=" * 70)
    print("TEST: Sequential edits accumulate on working PDF")
    print("=" * 70)

    pdf_path = TEST_DATA_DIR / "simple.pdf"
    create_simple_pdf(pdf_path)
    session_id = setup_test_session(session_mgr, pdf_path)

    # Edit 1: Q3 -> Q4
    r1 = editor.apply_text_replace(session_id, 1, "Q3", "Q4", "first_occurrence")
    print(f"  Edit 1 (Q3->Q4): success={r1.success}")

    # Edit 2: 2025 -> 2026 (in title only)
    r2 = editor.apply_text_replace(
        session_id, 1, "Q4 2025 Revenue Report", "Q4 2026 Revenue Report", "exact",
    )
    print(f"  Edit 2 (2025->2026 in title): success={r2.success}")

    issues = []
    working_pdf = session_mgr.get_working_pdf_path(session_id)

    if not verify_pdf_text(working_pdf, 1, "Q4 2026 Revenue Report", "Both edits applied"):
        issues.append("Sequential edits not accumulated")

    # Version should have incremented twice
    metadata = session_mgr.get_metadata(session_id)
    version = metadata["current_page_versions"]["1"]
    if version != 2:
        issues.append(f"Expected version 2, got {version}")
        print(f"    [FAIL] Version is {version}, expected 2")
    else:
        print(f"    [PASS] Version is {version}")

    session_mgr.cleanup_session(session_id)

    if issues:
        print(f"\n  FAILED: {'; '.join(issues)}")
        return False
    print("\n  PASSED")
    return True


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    # Setup
    if TEST_DATA_DIR.exists():
        shutil.rmtree(TEST_DATA_DIR)
    TEST_DATA_DIR.mkdir(parents=True)

    session_mgr = SessionManager(TEST_DATA_DIR / "sessions")
    editor = PdfEditor(session_manager=session_mgr)

    tests = [
        ("Same-length replace", test_same_length_replace),
        ("Shorter replace", test_shorter_replace),
        ("Overflow escalation", test_overflow_escalation),
        ("Multiple occurrences", test_multiple_occurrences),
        ("Text not found", test_text_not_found),
        ("Style: color", test_style_color_change),
        ("Style: font size", test_style_font_size),
        ("Original untouched", test_original_untouched),
        ("Sequential edits", test_sequential_edits),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn(session_mgr, editor)
        except Exception as e:
            print(f"\n  EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            passed = False
        results.append((name, passed))

    # Summary
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    passed_count = sum(1 for _, p in results if p)
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
    print(f"\n{passed_count}/{len(results)} tests passed")

    # Cleanup
    if TEST_DATA_DIR.exists():
        shutil.rmtree(TEST_DATA_DIR)

    if passed_count < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
