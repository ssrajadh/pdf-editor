"""Programmatic PDF text editing engine using PyMuPDF (fitz).

Uses the redact-and-overlay technique instead of content stream manipulation:
1. Search for text and get its bounding box
2. Redact (cover) the original text with a filled rect matching the background
3. Insert replacement text at the same position with matched styling

This works reliably across all PDF types including CID fonts.
"""

import logging
import time
from collections import Counter

import fitz  # PyMuPDF

from app.models.schemas import StyleChangeResult, TextReplaceResult
from app.services import pdf_service
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

OVERFLOW_RATIO = 1.15


def _match_font(original_font_name: str, flags: int) -> str:
    """Map an arbitrary PDF font name to the closest standard PyMuPDF font.

    Standard fonts available without embedding:
      helv (Helvetica), hebo (Helvetica-Bold), heit (Helvetica-Oblique), hebi (Helvetica-BoldOblique)
      tiro (Times-Roman), tibo (Times-Bold), tiit (Times-Italic), tibi (Times-BoldItalic)
      cour (Courier), cobo (Courier-Bold), coit (Courier-Oblique), cobi (Courier-BoldOblique)
    """
    name = original_font_name.lower()
    is_bold = bool(flags & (1 << 4))  # bit 4 = bold
    is_italic = bool(flags & (1 << 1))  # bit 1 = italic

    if any(s in name for s in ("courier", "mono", "consol", "fixed")):
        if is_bold and is_italic:
            return "cobi"
        return "cobo" if is_bold else ("coit" if is_italic else "cour")
    elif any(s in name for s in ("times", "serif", "roman", "garamond",
                                  "georgia", "cambria", "palat")):
        if is_bold and is_italic:
            return "tibi"
        return "tibo" if is_bold else ("tiit" if is_italic else "tiro")
    else:
        if is_bold and is_italic:
            return "hebi"
        return "hebo" if is_bold else ("heit" if is_italic else "helv")


def _color_int_to_rgb(color_int: int) -> tuple[float, float, float]:
    """Convert PyMuPDF's integer color (0xRRGGBB) to (r, g, b) floats in 0-1."""
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >> 8) & 0xFF) / 255.0
    b = (color_int & 0xFF) / 255.0
    return (r, g, b)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    """Convert '#RRGGBB' to (r, g, b) floats in 0-1 range."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return (0.0, 0.0, 0.0)
    return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0)


class PdfEditor:
    """Edits text in PDFs using the redact-and-overlay technique.

    Instead of parsing content stream operators, we:
    1. Search for text and get its bounding box
    2. Redact (cover) the original text with a filled rect
    3. Insert replacement text at the same position with matched styling

    This works reliably across all PDF types including CID fonts.
    """

    def __init__(self, session_manager: SessionManager):
        self.sessions = session_manager

    def apply_text_replace(
        self,
        session_id: str,
        page_num: int,
        original_text: str,
        replacement_text: str,
        match_strategy: str = "exact",
    ) -> TextReplaceResult:
        """Replace text in the PDF using redact-and-overlay."""
        t0 = time.monotonic()

        working_path = self.sessions.get_working_pdf_path(session_id)

        try:
            doc = fitz.open(str(working_path))
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return TextReplaceResult(
                success=False, original_text=original_text,
                new_text=replacement_text, escalate=True,
                error_message=f"Failed to open PDF: {e}", time_ms=elapsed,
                characters_changed=0,
            )

        try:
            if page_num < 1 or page_num > len(doc):
                elapsed = int((time.monotonic() - t0) * 1000)
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text,
                    error_message=f"Page {page_num} out of range (1-{len(doc)})",
                    time_ms=elapsed, characters_changed=0,
                )

            page = doc[page_num - 1]

            # 1. Find text occurrences
            rects = page.search_for(original_text)

            if not rects:
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.warning(
                    "Text not found via search_for: %r on page %d",
                    original_text, page_num,
                )
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text, escalate=True,
                    error_message=f"Text '{original_text}' not found on page {page_num}",
                    time_ms=elapsed, characters_changed=0,
                )

            if match_strategy == "first_occurrence":
                rects = rects[:1]

            # 2. For each match, get text properties, check fit, redact, and overlay
            for rect in rects:
                props = self._get_text_properties(page, rect, original_text)
                matched_font = _match_font(
                    props["font"] if props else "Helvetica",
                    props["flags"] if props else 0,
                )
                font_size = props["size"] if props else 11.0

                # 3. Check if replacement fits
                replacement_width = fitz.get_text_length(
                    replacement_text, fontname=matched_font, fontsize=font_size,
                )
                original_width = rect.width

                if original_width > 0 and replacement_width > original_width * OVERFLOW_RATIO:
                    elapsed = int((time.monotonic() - t0) * 1000)
                    logger.info(
                        "Replacement too wide: %.0f vs %.0f available (%.0f%%)",
                        replacement_width, original_width,
                        100 * replacement_width / original_width,
                    )
                    return TextReplaceResult(
                        success=False, original_text=original_text,
                        new_text=replacement_text, escalate=True,
                        error_message=(
                            f"Replacement text too wide: {replacement_width:.0f}px "
                            f"vs {original_width:.0f}px available"
                        ),
                        time_ms=elapsed, characters_changed=0,
                    )

                # 4. Detect background color
                bg_color = self._detect_background_color(page, rect)

                # 5. Redact original text
                annot = page.add_redact_annot(rect)
                annot.set_colors(fill=bg_color)
                page.apply_redactions()

                # 6. Insert replacement text
                color_rgb = _color_int_to_rgb(props["color"]) if props else (0, 0, 0)

                if props and props.get("origin"):
                    text_point = fitz.Point(props["origin"][0], props["origin"][1])
                else:
                    text_point = fitz.Point(rect.x0, rect.y1 - rect.height * 0.15)

                rc = page.insert_text(
                    text_point,
                    replacement_text,
                    fontname=matched_font,
                    fontsize=font_size,
                    color=color_rgb,
                )
                if rc < 0:
                    elapsed = int((time.monotonic() - t0) * 1000)
                    return TextReplaceResult(
                        success=False, original_text=original_text,
                        new_text=replacement_text, escalate=True,
                        error_message="Text insertion failed — overflow",
                        time_ms=elapsed, characters_changed=0,
                    )

                logger.info(
                    "Redact-and-overlay: %r -> %r at rect=%s, font=%s/%.1f, bg=%s",
                    original_text, replacement_text, rect,
                    matched_font, font_size, bg_color,
                )

            # 7. Save
            doc.save(str(working_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()

            # 8. Re-render the page
            session_path = self.sessions.get_session_path(session_id)
            metadata = self.sessions.get_metadata(session_id)
            current_version = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            new_version = current_version + 1
            pdf_service.render_page(
                working_path, page_num,
                session_path / "pages", version=new_version,
            )
            metadata["current_page_versions"][str(page_num)] = new_version
            self.sessions.update_metadata(session_id, metadata)

            elapsed = int((time.monotonic() - t0) * 1000)
            return TextReplaceResult(
                success=True,
                original_text=original_text,
                new_text=replacement_text,
                time_ms=elapsed,
                characters_changed=abs(len(replacement_text) - len(original_text)),
            )

        except Exception as e:
            logger.error("Text replacement failed: %s", e, exc_info=True)
            elapsed = int((time.monotonic() - t0) * 1000)
            return TextReplaceResult(
                success=False, original_text=original_text,
                new_text=replacement_text, escalate=True,
                error_message=f"PDF editing error: {e}",
                time_ms=elapsed, characters_changed=0,
            )
        finally:
            if not doc.is_closed:
                doc.close()

    def apply_style_change(
        self,
        session_id: str,
        page_num: int,
        target_text: str,
        changes: dict,
    ) -> StyleChangeResult:
        """Modify visual properties of text using redact-and-overlay.

        Same technique: find the text, redact it, re-insert with modified properties.
        Supports: color, font_size, bold, italic.
        """
        t0 = time.monotonic()
        working_path = self.sessions.get_working_pdf_path(session_id)

        try:
            doc = fitz.open(str(working_path))
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return StyleChangeResult(
                success=False, target_text=target_text,
                changes_applied={}, escalate=True,
                error_message=f"Failed to open PDF: {e}", time_ms=elapsed,
            )

        try:
            page = doc[page_num - 1]
            rects = page.search_for(target_text)

            if not rects:
                elapsed = int((time.monotonic() - t0) * 1000)
                return StyleChangeResult(
                    success=False, target_text=target_text,
                    changes_applied={}, escalate=True,
                    error_message=f"Text '{target_text}' not found",
                    time_ms=elapsed,
                )

            rect = rects[0]
            props = self._get_text_properties(page, rect, target_text)

            original_font = props["font"] if props else "Helvetica"
            original_flags = props["flags"] if props else 0
            original_size = props["size"] if props else 11.0
            original_color = _color_int_to_rgb(props["color"]) if props else (0, 0, 0)
            origin = props.get("origin") if props else None

            applied: dict = {}

            new_flags = original_flags
            if "bold" in changes:
                if changes["bold"]:
                    new_flags |= (1 << 4)
                else:
                    new_flags &= ~(1 << 4)
                applied["bold"] = changes["bold"]

            if "italic" in changes:
                if changes["italic"]:
                    new_flags |= (1 << 1)
                else:
                    new_flags &= ~(1 << 1)
                applied["italic"] = changes["italic"]

            new_font = _match_font(original_font, new_flags)
            new_size = float(changes.get("font_size", original_size))
            if "font_size" in changes:
                applied["font_size"] = new_size

            new_color = original_color
            if "color" in changes:
                new_color = _hex_to_rgb(changes["color"])
                applied["color"] = changes["color"]

            # Check if size change would overflow
            new_width = fitz.get_text_length(target_text, fontname=new_font, fontsize=new_size)
            if rect.width > 0 and new_width > rect.width * OVERFLOW_RATIO:
                elapsed = int((time.monotonic() - t0) * 1000)
                return StyleChangeResult(
                    success=False, target_text=target_text,
                    changes_applied={}, escalate=True,
                    error_message=f"Style change would overflow: {new_width:.0f} vs {rect.width:.0f}",
                    time_ms=elapsed,
                )

            # Redact and re-insert
            bg_color = self._detect_background_color(page, rect)
            annot = page.add_redact_annot(rect)
            annot.set_colors(fill=bg_color)
            page.apply_redactions()

            text_point = fitz.Point(origin[0], origin[1]) if origin else \
                fitz.Point(rect.x0, rect.y1 - rect.height * 0.15)

            page.insert_text(
                text_point, target_text,
                fontname=new_font, fontsize=new_size, color=new_color,
            )

            doc.save(str(working_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()

            # Re-render
            session_path = self.sessions.get_session_path(session_id)
            metadata = self.sessions.get_metadata(session_id)
            current_version = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            new_version = current_version + 1
            pdf_service.render_page(
                working_path, page_num,
                session_path / "pages", version=new_version,
            )
            metadata["current_page_versions"][str(page_num)] = new_version
            self.sessions.update_metadata(session_id, metadata)

            elapsed = int((time.monotonic() - t0) * 1000)
            return StyleChangeResult(
                success=True, target_text=target_text,
                changes_applied=applied, time_ms=elapsed,
            )

        except Exception as e:
            logger.error("Style change failed: %s", e, exc_info=True)
            elapsed = int((time.monotonic() - t0) * 1000)
            return StyleChangeResult(
                success=False, target_text=target_text,
                changes_applied={}, escalate=True,
                error_message=f"PDF editing error: {e}", time_ms=elapsed,
            )
        finally:
            if not doc.is_closed:
                doc.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_text_properties(
        page: fitz.Page, rect: fitz.Rect, target_text: str,
    ) -> dict | None:
        """Extract font, size, color, flags, and origin for text in a rect."""
        blocks = page.get_text("dict", clip=rect, flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if target_text in span_text or span_text.strip() in target_text:
                        return {
                            "font": span["font"],
                            "size": span["size"],
                            "color": span["color"],
                            "flags": span["flags"],
                            "origin": span["origin"],
                        }
        return None

    @staticmethod
    def _detect_background_color(
        page: fitz.Page, rect: fitz.Rect,
    ) -> tuple[float, float, float]:
        """Sample background color around a text rect.

        Renders a small clip and samples corner pixels, which should be
        background rather than text.
        """
        pad = 2
        clip = fitz.Rect(
            rect.x0 - pad, rect.y0 - pad,
            rect.x1 + pad, rect.y1 + pad,
        )
        try:
            pix = page.get_pixmap(clip=clip, dpi=72)
            samples = []
            for x, y in [(0, 0), (pix.width - 1, 0),
                         (0, pix.height - 1), (pix.width - 1, pix.height - 1)]:
                x = max(0, min(x, pix.width - 1))
                y = max(0, min(y, pix.height - 1))
                pixel = pix.pixel(x, y)
                samples.append(pixel[:3])
            most_common = Counter(samples).most_common(1)[0][0]
            return tuple(c / 255.0 for c in most_common)
        except Exception:
            return (1.0, 1.0, 1.0)
