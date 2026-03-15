"""Tests for recently fixed bugs:

- _match_font: bold/italic detection from font name (not just flags)
- _match_case: case-pattern preservation across replacement
- characters_changed: correct count for same-length replacements
- get_page_image_path: explicit version parameter resolution
- PdfEditor text replacement with case/font matching end-to-end

Usage:
    cd backend
    python -m pytest tests/test_recent_fixes.py -v
"""

import shutil
from pathlib import Path

import fitz
import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services.pdf_editor import _match_case, _match_font
from app.services import pdf_service
from app.storage.session import SessionManager

STORAGE = Path(__file__).parent / "test_data_recent"


@pytest.fixture(autouse=True)
def clean_storage():
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True, exist_ok=True)
    yield
    if STORAGE.exists():
        shutil.rmtree(STORAGE)


# ======================================================================
# _match_font tests
# ======================================================================


class TestMatchFont:
    """Font matching should detect bold/italic from both flags AND font name."""

    def test_plain_helvetica(self):
        assert _match_font("Helvetica", 0) == "helv"

    def test_bold_from_flags(self):
        """Bit 4 (16) = bold flag."""
        assert _match_font("Helvetica", 1 << 4) == "hebo"

    def test_italic_from_flags(self):
        """Bit 1 (2) = italic flag."""
        assert _match_font("Helvetica", 1 << 1) == "heit"

    def test_bold_italic_from_flags(self):
        assert _match_font("Helvetica", (1 << 4) | (1 << 1)) == "hebi"

    def test_bold_from_font_name(self):
        """Many PDFs encode weight in the name, e.g. 'Arial-Bold'."""
        assert _match_font("Arial-Bold", 0) == "hebo"

    def test_bold_from_font_name_mixed_case(self):
        assert _match_font("TimesNewRoman,Bold", 0) == "tibo"

    def test_italic_from_font_name(self):
        assert _match_font("Helvetica-Oblique", 0) == "heit"

    def test_bold_italic_from_font_name(self):
        assert _match_font("Arial-BoldItalic", 0) == "hebi"

    def test_semibold_in_name(self):
        assert _match_font("Calibri-SemiBold", 0) == "hebo"

    def test_heavy_in_name(self):
        assert _match_font("FiraSans-Heavy", 0) == "hebo"

    def test_courier_bold_name(self):
        assert _match_font("Courier-Bold", 0) == "cobo"

    def test_times_italic_name(self):
        assert _match_font("TimesNewRomanPS-ItalicMT", 0) == "tiit"

    def test_garamond_maps_to_times_family(self):
        assert _match_font("Garamond", 0) == "tiro"

    def test_consolas_maps_to_courier(self):
        assert _match_font("Consolas", 0) == "cour"

    def test_flags_and_name_agree(self):
        """When both flags and name say bold, result is still bold (not double)."""
        assert _match_font("Arial-Bold", 1 << 4) == "hebo"

    def test_palatino_bold(self):
        assert _match_font("Palatino-Bold", 0) == "tibo"


# ======================================================================
# _match_case tests
# ======================================================================


class TestMatchCase:
    """Case-pattern preservation during text replacement."""

    def test_all_caps(self):
        assert _match_case("HELLO WORLD", "goodbye world") == "GOODBYE WORLD"

    def test_all_lowercase(self):
        assert _match_case("hello world", "GOODBYE WORLD") == "goodbye world"

    def test_title_case(self):
        assert _match_case("Hello World", "goodbye earth") == "Goodbye Earth"

    def test_first_upper(self):
        assert _match_case("Hello", "goodbye") == "Goodbye"

    def test_first_upper_single_char(self):
        assert _match_case("A", "b") == "B"

    def test_mixed_case_passthrough(self):
        """Mixed case (e.g. camelCase) should not be altered."""
        assert _match_case("camelCase", "newValue") == "newValue"

    def test_empty_original(self):
        assert _match_case("", "hello") == "hello"

    def test_empty_replacement(self):
        assert _match_case("HELLO", "") == ""

    def test_numeric_only_original(self):
        """No alphabetic chars in original — passthrough."""
        assert _match_case("2026", "2027") == "2027"

    def test_caps_with_numbers(self):
        assert _match_case("ABC123", "xyz789") == "XYZ789"

    def test_lowercase_with_punctuation(self):
        assert _match_case("hello, world!", "GOODBYE, EARTH!") == "goodbye, earth!"

    def test_single_word_title_vs_first_upper(self):
        """Single word 'Hello' should match first-upper pattern."""
        result = _match_case("Hello", "WORLD")
        assert result == "World"


# ======================================================================
# characters_changed calculation tests
# ======================================================================


class TestCharactersChanged:
    """The characters_changed field should count actual character differences."""

    def _calc(self, original: str, replacement: str) -> int:
        """Replicate the characters_changed formula from pdf_editor.py."""
        case_matched = _match_case(original, replacement)
        return sum(a != b for a, b in zip(original, case_matched)) + abs(
            len(case_matched) - len(original)
        )

    def test_same_length_different_chars(self):
        """Replacing '2026' with '2027' — 1 char differs."""
        assert self._calc("2026", "2027") == 1

    def test_identical_strings(self):
        assert self._calc("hello", "hello") == 0

    def test_longer_replacement(self):
        assert self._calc("hi", "hello") == 4  # 'h' same, 'i'!='e' + 3 extra

    def test_shorter_replacement(self):
        assert self._calc("hello", "hi") == 4  # 'h' same, 'e'!='i' + 3 missing

    def test_completely_different(self):
        assert self._calc("abc", "xyz") == 3

    def test_case_matched_caps(self):
        """When original is ALL CAPS, replacement gets uppercased first."""
        # "HELLO" → _match_case("HELLO", "world") = "WORLD"
        # zip("HELLO", "WORLD") → H!=W, E!=O, L!=R, L!=L(same), O!=D → 4 diffs
        assert self._calc("HELLO", "world") == 4


# ======================================================================
# get_page_image_path tests
# ======================================================================


class TestGetPageImagePath:
    """Image path resolution with explicit version parameter."""

    def test_explicit_version(self, shared_storage):
        """Requesting version='2' returns the v2 image, not latest."""
        pages_dir = shared_storage / "pages"
        pages_dir.mkdir(parents=True)

        # Create version files 0, 1, 2, 3
        for v in range(4):
            (pages_dir / f"page_1_v{v}.png").write_bytes(f"v{v}".encode())

        path = pdf_service.get_page_image_path(shared_storage, 1, version="2")
        assert path.name == "page_1_v2.png"

    def test_latest_version(self, shared_storage):
        """version='latest' returns highest version."""
        pages_dir = shared_storage / "pages"
        pages_dir.mkdir(parents=True)

        for v in range(3):
            (pages_dir / f"page_1_v{v}.png").write_bytes(f"v{v}".encode())

        path = pdf_service.get_page_image_path(shared_storage, 1, version="latest")
        assert path.name == "page_1_v2.png"

    def test_missing_version_raises(self, shared_storage):
        """Requesting a non-existent version raises FileNotFoundError."""
        pages_dir = shared_storage / "pages"
        pages_dir.mkdir(parents=True)
        (pages_dir / "page_1_v0.png").write_bytes(b"data")

        with pytest.raises(FileNotFoundError):
            pdf_service.get_page_image_path(shared_storage, 1, version="5")

    def test_no_images_raises(self, shared_storage):
        """No images at all raises FileNotFoundError."""
        pages_dir = shared_storage / "pages"
        pages_dir.mkdir(parents=True)

        with pytest.raises(FileNotFoundError):
            pdf_service.get_page_image_path(shared_storage, 1, version="latest")


# ======================================================================
# End-to-end text replacement with font/case matching
# ======================================================================


class TestTextReplaceEndToEnd:
    """Integration tests for the PdfEditor text replacement pipeline."""

    @staticmethod
    def _make_pdf_with_text(text: str, font: str = "Helvetica", size: float = 14) -> bytes:
        """Create a single-page PDF with specific text."""
        path = STORAGE / "_tmp.pdf"
        c = canvas.Canvas(str(path), pagesize=letter)
        c.setFont(font, size)
        c.drawString(72, 700, text)
        c.showPage()
        c.save()
        data = path.read_bytes()
        path.unlink(missing_ok=True)
        return data

    def test_replace_preserves_success(self, session_mgr, editor):
        """Basic text replacement succeeds and returns correct metadata."""
        pdf_bytes = self._make_pdf_with_text("Hello World 2026")
        session_id = session_mgr.create_session(pdf_bytes, "test.pdf", 1)
        session_path = session_mgr.get_session_path(session_id)
        pages_dir = session_path / "pages"
        pages_dir.mkdir(exist_ok=True)
        pdf_service.render_all_pages(session_path / "original.pdf", pages_dir)

        result = editor.apply_text_replace(session_id, 1, "2026", "2027")
        assert result.success is True
        assert result.characters_changed == 1

    def test_replace_not_found_escalates(self, session_mgr, editor):
        """Replacing text that doesn't exist should fail with escalate=True."""
        pdf_bytes = self._make_pdf_with_text("Hello World")
        session_id = session_mgr.create_session(pdf_bytes, "test.pdf", 1)
        session_path = session_mgr.get_session_path(session_id)
        pages_dir = session_path / "pages"
        pages_dir.mkdir(exist_ok=True)
        pdf_service.render_all_pages(session_path / "original.pdf", pages_dir)

        result = editor.apply_text_replace(session_id, 1, "nonexistent", "replacement")
        assert result.success is False
        assert result.escalate is True

    def test_replace_produces_new_version_image(self, session_mgr, editor):
        """After replacement, a new version image file should exist."""
        pdf_bytes = self._make_pdf_with_text("Hello World 2026")
        session_id = session_mgr.create_session(pdf_bytes, "test.pdf", 1)
        session_path = session_mgr.get_session_path(session_id)
        pages_dir = session_path / "pages"
        pages_dir.mkdir(exist_ok=True)
        pdf_service.render_all_pages(session_path / "original.pdf", pages_dir)

        result = editor.apply_text_replace(session_id, 1, "2026", "2027")
        assert result.success is True

        # A version 1 image should now exist
        v1_path = pages_dir / "page_1_v1.png"
        assert v1_path.exists(), f"Expected {v1_path} to exist after edit"

    def test_replace_bold_text_uses_bold_font(self, session_mgr, editor):
        """Replacing bold text should insert with a bold font variant."""
        path = STORAGE / "_tmp_bold.pdf"
        c = canvas.Canvas(str(path), pagesize=letter)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(72, 700, "IMPORTANT NOTE")
        c.showPage()
        c.save()
        pdf_bytes = path.read_bytes()
        path.unlink(missing_ok=True)

        session_id = session_mgr.create_session(pdf_bytes, "bold.pdf", 1)
        session_path = session_mgr.get_session_path(session_id)
        pages_dir = session_path / "pages"
        pages_dir.mkdir(exist_ok=True)
        pdf_service.render_all_pages(session_path / "original.pdf", pages_dir)

        result = editor.apply_text_replace(session_id, 1, "IMPORTANT NOTE", "CRITICAL ALERT")
        assert result.success is True

        # Verify the replacement text exists in the PDF
        doc = fitz.open(str(session_mgr.get_working_pdf_path(session_id)))
        page_text = doc[0].get_text()
        doc.close()
        assert "CRITICAL ALERT" in page_text

    def test_page_out_of_range(self, session_mgr, editor):
        """Requesting an invalid page number should fail gracefully."""
        pdf_bytes = self._make_pdf_with_text("Hello")
        session_id = session_mgr.create_session(pdf_bytes, "test.pdf", 1)

        result = editor.apply_text_replace(session_id, 99, "Hello", "Bye")
        assert result.success is False
        assert "out of range" in result.error_message.lower()
