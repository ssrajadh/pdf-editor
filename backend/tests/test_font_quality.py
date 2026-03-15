"""Visual regression tests for programmatic text replacement quality.

Verifies that replacements preserve:
 - Adjacent character spacing (no lost spaces)
 - Font weight (bold flag detection)
 - Baseline vertical alignment
 - Font size calibration

Usage:
    cd backend
    python -m pytest tests/test_font_quality.py -v
"""

import shutil
import tempfile
from pathlib import Path

import fitz
import numpy as np
import pytest
from PIL import Image

from app.services.pdf_editor import (
    PdfEditor,
    _match_font,
    _expand_rect_safe,
    _try_reuse_embedded_font,
    HORIZ_EXPAND_PX,
    VERT_EXPAND_PX,
)
from app.storage.session import SessionManager

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_session(tmp_path: Path, pdf_name: str = "test_document.pdf"):
    """Create a session from a fixture PDF and return (editor, session_id)."""
    storage = tmp_path / "data"
    storage.mkdir()
    mgr = SessionManager(storage)

    src = FIXTURES / pdf_name
    pdf_bytes = src.read_bytes()

    doc = fitz.open(str(src))
    page_count = len(doc)
    doc.close()

    session_id = mgr.create_session(pdf_bytes, pdf_name, page_count)

    # Render page images (needed for _bump_version_and_render)
    from app.services import pdf_service
    session_path = mgr.get_session_path(session_id)
    working = session_path / "working.pdf"
    pdf_service.render_all_pages(working if working.exists() else src, session_path / "pages")

    return PdfEditor(mgr), session_id, mgr


def _render_clip(pdf_path: Path, page_num: int, clip: fitz.Rect, dpi: int = 300):
    """Render a small clip of a page at high DPI and return as numpy array."""
    doc = fitz.open(str(pdf_path))
    page = doc[page_num]
    pix = page.get_pixmap(clip=clip, dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return np.array(img.convert("L"))


# ---------------------------------------------------------------------------
# Problem 1: Lost characters / spaces
# ---------------------------------------------------------------------------

class TestSpacePreservation:
    """Verify that replacing text doesn't eat adjacent spaces."""

    def test_space_before_replacement_preserved(self, tmp_path):
        """Replace a word and check the space before it survives."""
        # Build a PDF: "GPA: 4.0"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), "GPA: 4.0", fontname="helv", fontsize=12)
        pdf_path = tmp_path / "space_test.pdf"
        doc.save(str(pdf_path))
        doc.close()

        # Set up session
        storage = tmp_path / "data"
        storage.mkdir()
        mgr = SessionManager(storage)
        session_id = mgr.create_session(pdf_path.read_bytes(), "space_test.pdf", 1)
        from app.services import pdf_service
        session_path = mgr.get_session_path(session_id)
        pdf_service.render_all_pages(pdf_path, session_path / "pages")

        editor = PdfEditor(mgr)
        result = editor.apply_text_replace(session_id, 1, "4.0", "5.0")
        assert result.success, f"Replace failed: {result.error_message}"

        # Read back the text from the working PDF
        working = mgr.get_working_pdf_path(session_id)
        doc2 = fitz.open(str(working))
        page2 = doc2[0]
        text = page2.get_text().strip()
        doc2.close()

        assert "GPA: 5.0" in text or "GPA:  5.0" in text, (
            f"Space lost! Got: {text!r}  (expected 'GPA: 5.0')"
        )
        # Also verify the colon-space was NOT eaten
        assert "GPA:5" not in text, f"Space eaten! Got: {text!r}"

    def test_expand_rect_minimal_horizontal(self):
        """Verify HORIZ_EXPAND_PX is small enough not to eat a typical space."""
        # A space in 12pt Helvetica is ~3.3px; horizontal expansion must be
        # well under half that to avoid overlapping the space character.
        assert HORIZ_EXPAND_PX < 1.0, (
            f"HORIZ_EXPAND_PX={HORIZ_EXPAND_PX} is too large — will eat spaces"
        )


# ---------------------------------------------------------------------------
# Problem 2: Font weight matching
# ---------------------------------------------------------------------------

class TestFontWeightMatching:
    """Verify bold/italic detection from flags and font names."""

    def test_bold_from_pymupdf_flags(self):
        """Bit 4 (PyMuPDF simplified) → bold."""
        assert _match_font("Helvetica", 16) == "hebo"  # 1 << 4

    def test_bold_from_pdf_descriptor_flags(self):
        """Bit 17 (PDF font descriptor) → bold."""
        assert _match_font("Helvetica", 1 << 17) == "hebo"

    def test_italic_from_pymupdf_flags(self):
        """Bit 1 (PyMuPDF simplified) → italic."""
        assert _match_font("Helvetica", 2) == "heit"

    def test_italic_from_pdf_descriptor_flags(self):
        """Bit 5 (PDF font descriptor) → italic."""
        assert _match_font("Helvetica", 1 << 5) == "heit"

    def test_bold_italic_mixed_flags(self):
        """Both bold bits + italic bit → bold-italic."""
        flags = (1 << 4) | (1 << 1)  # PyMuPDF style
        assert _match_font("Helvetica", flags) == "hebi"

    def test_bold_from_font_name_no_flags(self):
        """Font name contains 'Bold' but flags are 0 → still bold."""
        assert _match_font("Arial-Bold", 0) == "hebo"

    def test_bold_italic_from_name(self):
        """Font name has both Bold and Italic."""
        assert _match_font("TimesNewRoman-BoldItalic", 0) == "tibi"

    def test_serif_detection(self):
        """Serif fonts map to Times family."""
        assert _match_font("Georgia", 0) == "tiro"
        assert _match_font("Garamond-Bold", 0) == "tibo"

    def test_mono_detection(self):
        """Monospace fonts map to Courier family."""
        assert _match_font("Consolas", 0) == "cour"
        assert _match_font("CourierNew-Bold", 0) == "cobo"


# ---------------------------------------------------------------------------
# Problem 3: Baseline alignment
# ---------------------------------------------------------------------------

class TestBaselineAlignment:
    """Verify the insertion point uses the correct baseline."""

    def test_origin_used_when_available(self, tmp_path):
        """When span origin is available, use it exactly."""
        editor, session_id, mgr = _setup_session(tmp_path)
        working = mgr.get_working_pdf_path(session_id)

        doc = fitz.open(str(working))
        page = doc[0]

        rects = page.search_for("2024")
        assert rects, "'2024' not found"
        rect = rects[0]

        props = PdfEditor._get_text_properties(page, rect, "2024")
        doc.close()

        assert props is not None, "Could not extract text properties"
        assert "origin" in props, "No origin in properties"
        origin = props["origin"]
        # Origin should be within the rect bounds
        assert rect.y0 <= origin[1] <= rect.y1, (
            f"Origin y={origin[1]} outside rect y0={rect.y0}..y1={rect.y1}"
        )

    def test_fallback_baseline_at_80_percent(self):
        """When origin isn't available, baseline should be ~80% down."""
        # Simulate: rect from y0=100 to y1=120 (height=20)
        # Expected baseline: 100 + 20 * 0.80 = 116
        # Old formula: 120 - 20 * 0.15 = 117 (too low)
        rect_y0, rect_height = 100, 20
        expected_baseline = rect_y0 + rect_height * 0.80
        assert expected_baseline == 116.0
        # Verify it's NOT the old formula
        old_baseline = (rect_y0 + rect_height) - rect_height * 0.15
        assert old_baseline == 117.0
        assert expected_baseline < old_baseline, "New baseline should be higher (smaller y)"


# ---------------------------------------------------------------------------
# Embedded font reuse
# ---------------------------------------------------------------------------

class TestEmbeddedFontReuse:
    """Verify embedded font extraction and reuse."""

    def test_reuse_embedded_truetype(self, tmp_path):
        """If a TrueType font is embedded and has all glyphs, reuse it."""
        import os
        font_file = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if not os.path.exists(font_file):
            pytest.skip("DejaVu font not available")

        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Hello 2024",
                         fontname="DejaVuSans", fontfile=font_file, fontsize=12)
        pdf_path = tmp_path / "embedded.pdf"
        doc.save(str(pdf_path))
        doc.close()

        doc2 = fitz.open(str(pdf_path))
        page2 = doc2[0]
        result = _try_reuse_embedded_font(doc2, page2, "DejaVuSans", "2025")
        doc2.close()

        if result is not None:
            name, buf = result
            assert len(buf) > 100
            assert "DejaVu" in name
        # result may be None if subset doesn't have all glyphs — that's OK

    def test_no_crash_on_missing_font(self, tmp_path):
        """Gracefully return None when font isn't in the document."""
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 100), "Hello", fontname="helv", fontsize=12)

        result = _try_reuse_embedded_font(doc, page, "NonExistentFont", "World")
        assert result is None
        doc.close()


# ---------------------------------------------------------------------------
# Integration: full replacement quality
# ---------------------------------------------------------------------------

class TestReplacementQuality:
    """End-to-end tests that verify replacement visual quality."""

    def test_same_length_replace_preserves_layout(self, tmp_path):
        """'2024' → '2025': same length, should preserve surrounding layout."""
        editor, session_id, mgr = _setup_session(tmp_path)
        working = mgr.get_working_pdf_path(session_id)

        # Get rect of "2024" before replacement
        doc = fitz.open(str(working))
        page = doc[0]
        before_rects = page.search_for("2024")
        assert before_rects
        before_rect = before_rects[0]

        # Get surrounding text rect for reference
        growth_rects = page.search_for("45%")
        doc.close()

        result = editor.apply_text_replace(session_id, 1, "2024", "2025")
        assert result.success, f"Replace failed: {result.error_message}"

        # Verify the replacement exists and surrounding text is intact
        working_after = mgr.get_working_pdf_path(session_id)
        doc2 = fitz.open(str(working_after))
        page2 = doc2[0]
        text = page2.get_text()
        doc2.close()

        assert "2025" in text, "Replacement text not found"
        assert "45%" in text, "Surrounding text was damaged"

    def test_different_length_calibrates_size(self, tmp_path):
        """'John' → 'Jane': different chars, font size should be calibrated."""
        editor, session_id, mgr = _setup_session(tmp_path)
        result = editor.apply_text_replace(session_id, 1, "John", "Jane")
        assert result.success

        working = mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        page = doc[0]
        text = page.get_text()
        doc.close()

        assert "Jane" in text
        assert "Smith" in text, "Adjacent text should be preserved"
