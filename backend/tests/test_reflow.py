"""Tests for line-level text reflow in programmatic text replacement.

Verifies that when replacement text is moderately longer than the original,
subsequent text on the same line is shifted rightward rather than clipping.

Usage:
    cd backend
    python -m pytest tests/test_reflow.py -v
"""

from pathlib import Path

import fitz
import pytest

from app.services.pdf_editor import PdfEditor
from app.storage.session import SessionManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(tmp_path: Path, pdf_path: Path):
    """Create a session from a PDF file and return (editor, session_id, mgr)."""
    storage = tmp_path / "data"
    storage.mkdir()
    mgr = SessionManager(storage)

    pdf_bytes = pdf_path.read_bytes()
    doc = fitz.open(str(pdf_path))
    page_count = len(doc)
    doc.close()

    session_id = mgr.create_session(pdf_bytes, pdf_path.name, page_count)

    from app.services import pdf_service
    session_path = mgr.get_session_path(session_id)
    working = session_path / "working.pdf"
    pdf_service.render_all_pages(
        working if working.exists() else pdf_path,
        session_path / "pages",
    )

    return PdfEditor(mgr), session_id, mgr


def _build_test_pdf(tmp_path: Path, text_parts: list[tuple[tuple[float, float], str]],
                    fontsize: float = 12, fontname: str = "helv") -> Path:
    """Build a single-page PDF with specified text at specified positions."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for pos, text in text_parts:
        page.insert_text(pos, text, fontname=fontname, fontsize=fontsize)
    pdf_path = tmp_path / "reflow_test.pdf"
    doc.save(str(pdf_path))
    doc.close()
    return pdf_path


# ---------------------------------------------------------------------------
# Test 1: Basic reflow — replacement longer, room on line
# ---------------------------------------------------------------------------

class TestBasicReflow:
    """Replacement is longer than original, but line has room to shift."""

    def test_reflow_shifts_subsequent_text(self, tmp_path):
        """'John' -> 'Jonathan' with ' Smith' after it on the same line."""
        pdf_path = _build_test_pdf(tmp_path, [
            ((72, 100), "John Smith - Software Engineer"),
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        result = editor.apply_text_replace_with_reflow(
            session_id, 1, "John", "Jonathan",
        )
        assert result.success, f"Reflow failed: {result.error_message}"

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        text = doc[0].get_text().strip()
        doc.close()

        assert "Jonathan" in text, f"Replacement text not found in: {text!r}"
        # Subsequent text should still be present
        assert "Smith" in text, f"Subsequent text 'Smith' lost: {text!r}"

    def test_reflow_preserves_all_subsequent_content(self, tmp_path):
        """All text after the target on the same line should survive."""
        pdf_path = _build_test_pdf(tmp_path, [
            ((72, 100), "Rev Report - Draft"),
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        result = editor.apply_text_replace_with_reflow(
            session_id, 1, "Rev", "Revenue",
        )
        assert result.success, f"Reflow failed: {result.error_message}"

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        text = doc[0].get_text().strip()
        doc.close()

        assert "Revenue" in text
        assert "Report" in text, f"Subsequent 'Report' lost: {text!r}"
        assert "Draft" in text, f"Subsequent 'Draft' lost: {text!r}"


# ---------------------------------------------------------------------------
# Test 2: No room for reflow — should escalate
# ---------------------------------------------------------------------------

class TestReflowNoRoom:
    """When there isn't enough space, reflow should fail with escalate=True."""

    def test_reflow_escalates_when_line_full(self, tmp_path):
        """Text fills the line — no room to shift right."""
        # Build a PDF with text stretching nearly to the right margin.
        # Use unique target text so search_for isn't ambiguous.
        filler = "A" * 70
        text = f"TARGET {filler} END"
        pdf_path = _build_test_pdf(tmp_path, [
            ((10, 100), text),  # start near left edge to fill the line
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        # Replace "TARGET" with something much longer — should fail
        result = editor.apply_text_replace_with_reflow(
            session_id, 1, "TARGET",
            "THIS IS A VERY LONG REPLACEMENT TEXT THAT WILL NOT FIT",
        )
        assert not result.success, f"Expected failure but got success"
        assert result.escalate


# ---------------------------------------------------------------------------
# Test 3: Reflow with no subsequent spans
# ---------------------------------------------------------------------------

class TestReflowNoSubsequent:
    """Target is the last (or only) text on the line."""

    def test_reflow_last_word_on_line(self, tmp_path):
        """If target is at the end of the line, reflow just needs page room."""
        pdf_path = _build_test_pdf(tmp_path, [
            ((72, 100), "Hello World"),
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        result = editor.apply_text_replace_with_reflow(
            session_id, 1, "World", "Everyone",
        )
        assert result.success, f"Reflow failed: {result.error_message}"

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        text = doc[0].get_text().strip()
        doc.close()

        assert "Everyone" in text


# ---------------------------------------------------------------------------
# Test 4: Reflow doesn't affect other lines
# ---------------------------------------------------------------------------

class TestReflowIsolation:
    """Reflow on one line should not disturb text on other lines."""

    def test_other_lines_unaffected(self, tmp_path):
        """Multi-line document: only the target line should be reflowed."""
        pdf_path = _build_test_pdf(tmp_path, [
            ((72, 100), "Line one: short"),
            ((72, 120), "Line two: untouched"),
            ((72, 140), "Line three: also safe"),
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        result = editor.apply_text_replace_with_reflow(
            session_id, 1, "short", "somewhat longer text",
        )
        assert result.success, f"Reflow failed: {result.error_message}"

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        text = doc[0].get_text()
        doc.close()

        assert "somewhat longer text" in text
        assert "untouched" in text, "Line 2 was damaged"
        assert "also safe" in text, "Line 3 was damaged"


# ---------------------------------------------------------------------------
# Test 5: Auto-reflow via apply_text_replace overflow path
# ---------------------------------------------------------------------------

class TestAutoReflow:
    """apply_text_replace should automatically try reflow for moderate overflow."""

    def test_overflow_triggers_reflow(self, tmp_path):
        """Replacement ~150% width should trigger auto-reflow, not fail."""
        pdf_path = _build_test_pdf(tmp_path, [
            ((72, 100), "Name: John - Senior Details"),
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        # "John" -> "Jonathan" is ~175% width — within the 2x auto-reflow range
        result = editor.apply_text_replace(session_id, 1, "John", "Jonathan")
        assert result.success, f"Auto-reflow should have succeeded: {result.error_message}"

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        text = doc[0].get_text().strip()
        doc.close()

        assert "Jonathan" in text


# ---------------------------------------------------------------------------
# Test 6: Shorter replacement falls through to normal replace
# ---------------------------------------------------------------------------

class TestReflowShortCircuit:
    """If replacement is shorter/equal, reflow delegates to normal replace."""

    def test_shorter_replacement_uses_normal_path(self, tmp_path):
        """'Jonathan' -> 'Jo' should not need reflow."""
        pdf_path = _build_test_pdf(tmp_path, [
            ((72, 100), "Jonathan Smith"),
        ])
        editor, session_id, mgr = _make_session(tmp_path, pdf_path)

        result = editor.apply_text_replace_with_reflow(
            session_id, 1, "Jonathan", "Jo",
        )
        assert result.success

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        text = doc[0].get_text().strip()
        doc.close()

        assert "Jo" in text
        assert "Smith" in text
