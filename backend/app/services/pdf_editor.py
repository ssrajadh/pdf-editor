"""Programmatic PDF text editing engine using PyMuPDF (fitz).

Uses the redact-and-overlay technique:
1. Search for text and get its bounding box
2. Redact (cover) the original text with a filled rect matching the background
3. Insert replacement text at the same position with matched styling

Handles edge cases: multi-match disambiguation via context, multi-line text,
protected/encrypted PDFs, adjacent text artifacts, font size calibration,
and batch replacements.
"""

import logging
import time
from collections import Counter
from dataclasses import dataclass

import fitz  # PyMuPDF

from app.models.schemas import StyleChangeResult, TextReplaceOp, TextReplaceResult
from app.services import pdf_service
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

OVERFLOW_RATIO = 1.15
VERT_EXPAND_PX = 1.5   # vertical expansion — safe, no adjacent text above/below a line
HORIZ_EXPAND_PX = 0.3  # horizontal expansion — minimal to avoid eating adjacent chars/spaces
FONT_SIZE_CLAMP = 0.15


def _match_font(original_font_name: str, flags: int) -> str:
    """Map an arbitrary PDF font name to the closest standard PyMuPDF font.

    Detects bold/italic from both the flags bitmask AND the font name itself,
    since many PDFs encode weight in the font name (e.g. "Arial-Bold",
    "TimesNewRoman,BoldItalic") rather than in the flags field.
    """
    name = original_font_name.lower()

    # Detect bold/italic from flags — check both PyMuPDF simplified span
    # flags (bit 4 = bold, bit 1 = italic) AND raw PDF font descriptor
    # flags (bit 17 = bold, bit 5 = italic) for maximum compatibility
    # across different PDF generators and PyMuPDF versions.
    is_bold = bool(flags & (1 << 4)) or bool(flags & (1 << 17))
    is_italic = bool(flags & (1 << 1)) or bool(flags & (1 << 5))

    # Also detect from font name — many PDFs only encode weight here
    bold_indicators = ("bold", "black", "heavy", "semibold", "demibold", "demi")
    italic_indicators = ("italic", "oblique", "slant", "incline")
    if not is_bold and any(ind in name for ind in bold_indicators):
        is_bold = True
    if not is_italic and any(ind in name for ind in italic_indicators):
        is_italic = True

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


def _match_case(original_text: str, replacement_text: str) -> str:
    """Apply the case pattern of original_text to replacement_text.

    Handles ALL CAPS, Title Case, lowercase, and first-letter-upper patterns.
    Falls back to returning replacement_text unchanged if the pattern is mixed
    or unrecognizable.
    """
    if not original_text or not replacement_text:
        return replacement_text

    # Strip to compare only alphabetic characters
    alpha_orig = [c for c in original_text if c.isalpha()]
    if not alpha_orig:
        return replacement_text

    # ALL CAPS: "HELLO WORLD" → "GOODBYE WORLD"
    if all(c.isupper() for c in alpha_orig):
        return replacement_text.upper()

    # all lowercase: "hello world" → "goodbye world"
    if all(c.islower() for c in alpha_orig):
        return replacement_text.lower()

    # Title Case: "Hello World" → "Goodbye World"
    words_orig = original_text.split()
    if len(words_orig) > 1 and all(
        w[0].isupper() and w[1:].islower() for w in words_orig if len(w) > 1 and w[0].isalpha()
    ):
        return replacement_text.title()

    # First letter uppercase only: "Hello" → "Goodbye"
    if alpha_orig[0].isupper() and all(c.islower() for c in alpha_orig[1:]):
        return replacement_text[0].upper() + replacement_text[1:].lower() if len(replacement_text) > 1 else replacement_text.upper()

    # Mixed or unknown pattern — don't alter
    return replacement_text


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


def _calibrate_font_size(
    replacement_text: str, fontname: str, target_width: float, base_size: float,
) -> float:
    """Adjust font size so replacement text width matches the original bounding box.

    Measures rendered width at base_size, then scales proportionally.
    Clamps adjustment to ±15% to avoid visually jarring size changes.
    """
    rendered_width = fitz.get_text_length(replacement_text, fontname=fontname, fontsize=base_size)
    if rendered_width <= 0 or target_width <= 0:
        return base_size

    ratio = target_width / rendered_width
    if abs(ratio - 1.0) < 0.03:
        return base_size

    adjusted = base_size * ratio
    min_size = base_size * (1.0 - FONT_SIZE_CLAMP)
    max_size = base_size * (1.0 + FONT_SIZE_CLAMP)
    return max(min_size, min(adjusted, max_size))


def _expand_rect_safe(
    rect: fitz.Rect, page: fitz.Page, target_text: str,
) -> fitz.Rect:
    """Expand a redaction rect by a small margin to avoid clipping artifacts,
    but don't overlap with adjacent text bounding boxes.

    Uses asymmetric expansion: generous vertical padding (glyph ascenders/
    descenders), minimal horizontal padding (to avoid eating into adjacent
    characters or spaces — the root cause of lost-space bugs like
    'GPA: 5.0' becoming 'GPA:5.0').
    """
    expanded = fitz.Rect(
        rect.x0 - HORIZ_EXPAND_PX, rect.y0 - VERT_EXPAND_PX,
        rect.x1 + HORIZ_EXPAND_PX, rect.y1 + VERT_EXPAND_PX,
    )
    expanded &= page.rect

    blocks = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                span_text = span.get("text", "").strip()
                if not span_text or span_text in target_text or target_text in span_text:
                    continue
                span_rect = fitz.Rect(span["bbox"])
                if expanded.intersects(span_rect) and not rect.intersects(span_rect):
                    expanded = rect
                    return expanded

    return expanded


def _try_reuse_embedded_font(
    doc: fitz.Document,
    page: fitz.Page,
    original_font_name: str,
    replacement_text: str,
) -> tuple[str, bytes] | None:
    """Try to reuse the document's embedded font for replacement text.

    Returns (fontname, fontbuffer) if the font can be extracted and contains
    glyphs for the replacement text.  Returns None to fall back to base-14.
    """
    try:
        fonts = page.get_fonts()
        for font_entry in fonts:
            xref = font_entry[0]
            name = font_entry[3] if len(font_entry) > 3 else ""
            if not (name == original_font_name
                    or original_font_name in name
                    or name in original_font_name):
                continue

            basename, ext, subtype, content = doc.extract_font(xref)
            if not content or len(content) < 100:
                continue  # empty or stub font

            # Quick glyph check: try to create a Font object and measure
            try:
                test_font = fitz.Font(fontbuffer=content)
                # Check that all characters in replacement_text have glyphs
                has_all = all(test_font.has_glyph(ord(ch)) for ch in replacement_text)
                if not has_all:
                    logger.debug(
                        "Embedded font %s missing glyphs for %r, skipping reuse",
                        name, replacement_text,
                    )
                    continue
            except Exception:
                continue  # font buffer not usable

            logger.info(
                "Reusing embedded font: %s (xref=%d, type=%s, %d bytes)",
                name, xref, subtype, len(content),
            )
            return name, content

    except Exception as e:
        logger.debug("Could not reuse embedded font %s: %s", original_font_name, e)

    return None


@dataclass
class _PreparedReplacement:
    """Pre-validated replacement ready for batch application."""
    op_index: int
    rect: fitz.Rect
    expanded_rect: fitz.Rect
    replacement_text: str
    fontname: str
    fontsize: float
    color: tuple[float, float, float]
    bg_color: tuple[float, float, float]
    origin: tuple[float, float]
    original_text: str
    fontbuffer: bytes | None = None  # embedded font data, if reusing


class PdfEditor:
    """Edits text in PDFs using the redact-and-overlay technique.

    Supports single replacements and batch replacements (multiple ops on
    the same page applied atomically with a single apply_redactions() call).
    """

    def __init__(self, session_manager: SessionManager):
        self.sessions = session_manager

    # ------------------------------------------------------------------
    # Single text replacement (backward-compatible API)
    # ------------------------------------------------------------------

    def apply_text_replace(
        self,
        session_id: str,
        page_num: int,
        original_text: str,
        replacement_text: str,
        match_strategy: str = "exact",
        context_before: str | None = None,
        context_after: str | None = None,
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
            # Protected PDF check
            check = self._check_pdf_access(doc)
            if check:
                elapsed = int((time.monotonic() - t0) * 1000)
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text, escalate=True,
                    error_message=check, time_ms=elapsed, characters_changed=0,
                )

            if page_num < 1 or page_num > len(doc):
                elapsed = int((time.monotonic() - t0) * 1000)
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text,
                    error_message=f"Page {page_num} out of range (1-{len(doc)})",
                    time_ms=elapsed, characters_changed=0,
                )

            page = doc[page_num - 1]

            # Find target rect with disambiguation
            target_rect = self._find_target_rect(
                page, original_text, match_strategy,
                context_before, context_after,
            )

            if isinstance(target_rect, str):
                elapsed = int((time.monotonic() - t0) * 1000)
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text, escalate=True,
                    error_message=target_rect, time_ms=elapsed,
                    characters_changed=0,
                )

            rects = [target_rect] if not isinstance(target_rect, list) else target_rect

            # Match the case pattern of the original text
            case_matched_text = _match_case(original_text, replacement_text)

            for rect in rects:
                props = self._get_text_properties(page, rect, original_text)

                # Diagnostic logging
                logger.info(
                    "[DIAG] Replacing %r -> %r | rect=%s (%.1f x %.1f)",
                    original_text, case_matched_text, rect,
                    rect.width, rect.height,
                )
                if props:
                    logger.info(
                        "[DIAG]   props: font=%r size=%.1f flags=%d "
                        "color=%s origin=%s",
                        props["font"], props["size"], props["flags"],
                        props["color"], props.get("origin"),
                    )

                orig_font_name = props["font"] if props else "Helvetica"
                orig_flags = props["flags"] if props else 0
                font_size = props["size"] if props else 11.0

                # Try to reuse the document's embedded font first
                fontbuffer = None
                reused = _try_reuse_embedded_font(
                    doc, page, orig_font_name, case_matched_text,
                )
                if reused:
                    matched_font, fontbuffer = reused
                else:
                    matched_font = _match_font(orig_font_name, orig_flags)

                # Overflow check
                replacement_width = fitz.get_text_length(
                    case_matched_text, fontname=matched_font, fontsize=font_size,
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

                # Font size calibration
                calibrated_size = _calibrate_font_size(
                    case_matched_text, matched_font, original_width, font_size,
                )

                bg_color = self._detect_background_color(page, rect)
                expanded = _expand_rect_safe(page=page, rect=rect, target_text=original_text)

                annot = page.add_redact_annot(expanded)
                annot.set_colors(fill=bg_color)
                page.apply_redactions()

                color_rgb = _color_int_to_rgb(props["color"]) if props else (0, 0, 0)

                # Baseline alignment: use span origin (= exact baseline position)
                # Fallback estimates baseline at 80% down from the top of the rect
                # (ascenders reach ~80% of line height, descenders below).
                if props and props.get("origin"):
                    text_point = fitz.Point(props["origin"][0], props["origin"][1])
                else:
                    text_point = fitz.Point(rect.x0, rect.y0 + rect.height * 0.80)

                insert_kwargs: dict = dict(
                    fontname=matched_font,
                    fontsize=calibrated_size,
                    color=color_rgb,
                )
                if fontbuffer:
                    insert_kwargs["fontbuffer"] = fontbuffer

                rc = page.insert_text(
                    text_point,
                    case_matched_text,
                    **insert_kwargs,
                )
                if rc < 0:
                    elapsed = int((time.monotonic() - t0) * 1000)
                    return TextReplaceResult(
                        success=False, original_text=original_text,
                        new_text=case_matched_text, escalate=True,
                        error_message="Text insertion failed — overflow",
                        time_ms=elapsed, characters_changed=0,
                    )

                logger.info(
                    "Redact-and-overlay: %r -> %r (case: %r) at rect=%s, "
                    "font=%s/%.1f(cal:%.1f), bg=%s, reused=%s",
                    original_text, replacement_text, case_matched_text, rect,
                    matched_font, font_size, calibrated_size, bg_color,
                    fontbuffer is not None,
                )

            doc.save(str(working_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()

            self._bump_version_and_render(session_id, page_num, working_path)

            elapsed = int((time.monotonic() - t0) * 1000)
            return TextReplaceResult(
                success=True,
                original_text=original_text,
                new_text=replacement_text,
                time_ms=elapsed,
                characters_changed=sum(a != b for a, b in zip(original_text, case_matched_text)) + abs(len(case_matched_text) - len(original_text)),
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

    # ------------------------------------------------------------------
    # Batch text replacement
    # ------------------------------------------------------------------

    def apply_text_replacements_batch(
        self,
        session_id: str,
        page_num: int,
        operations: list[TextReplaceOp],
    ) -> list[TextReplaceResult]:
        """Apply multiple text replacements on the same page atomically.

        Opens the doc once, collects all redactions, applies them in a single
        apply_redactions() call, then inserts all replacement text and saves once.
        """
        t0 = time.monotonic()
        working_path = self.sessions.get_working_pdf_path(session_id)
        results: list[TextReplaceResult] = []

        try:
            doc = fitz.open(str(working_path))
        except Exception as e:
            return [TextReplaceResult(
                success=False, original_text=op.original_text,
                new_text=op.replacement_text, escalate=True,
                error_message=f"Failed to open PDF: {e}",
                time_ms=0, characters_changed=0,
            ) for op in operations]

        try:
            check = self._check_pdf_access(doc)
            if check:
                return [TextReplaceResult(
                    success=False, original_text=op.original_text,
                    new_text=op.replacement_text, escalate=True,
                    error_message=check, time_ms=0, characters_changed=0,
                ) for op in operations]

            page = doc[page_num - 1]

            # Phase 1: Validate all operations and collect prepared replacements
            prepared: list[_PreparedReplacement] = []
            for i, op in enumerate(operations):
                t_op = time.monotonic()

                target_rect = self._find_target_rect(
                    page, op.original_text, op.match_strategy,
                    op.context_before, op.context_after,
                )

                if isinstance(target_rect, str):
                    elapsed_op = int((time.monotonic() - t_op) * 1000)
                    results.append(TextReplaceResult(
                        success=False, original_text=op.original_text,
                        new_text=op.replacement_text, escalate=True,
                        error_message=target_rect, time_ms=elapsed_op,
                        characters_changed=0,
                    ))
                    continue

                rect = target_rect if not isinstance(target_rect, list) else target_rect[0]
                props = self._get_text_properties(page, rect, op.original_text)

                logger.info(
                    "[DIAG] Batch[%d] %r -> %r | rect=%s (%.1f x %.1f) | props=%s",
                    i, op.original_text, op.replacement_text, rect,
                    rect.width, rect.height,
                    {k: v for k, v in props.items() if k != "origin"} if props else None,
                )

                orig_font_name = props["font"] if props else "Helvetica"
                orig_flags = props["flags"] if props else 0
                font_size = props["size"] if props else 11.0

                # Try embedded font reuse, then fall back to base-14
                fontbuffer = None
                reused = _try_reuse_embedded_font(
                    doc, page, orig_font_name, op.replacement_text,
                )
                if reused:
                    matched_font, fontbuffer = reused
                else:
                    matched_font = _match_font(orig_font_name, orig_flags)

                # Match the case pattern of the original text
                case_matched = _match_case(op.original_text, op.replacement_text)

                replacement_width = fitz.get_text_length(
                    case_matched, fontname=matched_font, fontsize=font_size,
                )
                if rect.width > 0 and replacement_width > rect.width * OVERFLOW_RATIO:
                    elapsed_op = int((time.monotonic() - t_op) * 1000)
                    results.append(TextReplaceResult(
                        success=False, original_text=op.original_text,
                        new_text=case_matched, escalate=True,
                        error_message=(
                            f"Replacement text too wide: {replacement_width:.0f}px "
                            f"vs {rect.width:.0f}px available"
                        ),
                        time_ms=elapsed_op, characters_changed=0,
                    ))
                    continue

                calibrated_size = _calibrate_font_size(
                    case_matched, matched_font, rect.width, font_size,
                )
                bg_color = self._detect_background_color(page, rect)
                expanded = _expand_rect_safe(page=page, rect=rect, target_text=op.original_text)
                color_rgb = _color_int_to_rgb(props["color"]) if props else (0, 0, 0)

                # Baseline: use span origin if available, otherwise estimate
                if props and props.get("origin"):
                    origin = (props["origin"][0], props["origin"][1])
                else:
                    origin = (rect.x0, rect.y0 + rect.height * 0.80)

                prepared.append(_PreparedReplacement(
                    op_index=i,
                    rect=rect,
                    expanded_rect=expanded,
                    replacement_text=case_matched,
                    fontname=matched_font,
                    fontsize=calibrated_size,
                    color=color_rgb,
                    bg_color=bg_color,
                    origin=origin,
                    original_text=op.original_text,
                    fontbuffer=fontbuffer,
                ))

            if not prepared:
                doc.close()
                return results

            # Phase 2: Add all redaction annotations
            for prep in prepared:
                annot = page.add_redact_annot(prep.expanded_rect)
                annot.set_colors(fill=prep.bg_color)

            # Phase 3: Single apply_redactions() call
            page.apply_redactions()

            # Phase 4: Insert all replacement texts
            any_insert_failed = False
            for prep in prepared:
                text_point = fitz.Point(prep.origin[0], prep.origin[1])
                insert_kwargs: dict = dict(
                    fontname=prep.fontname,
                    fontsize=prep.fontsize,
                    color=prep.color,
                )
                if prep.fontbuffer:
                    insert_kwargs["fontbuffer"] = prep.fontbuffer
                rc = page.insert_text(
                    text_point,
                    prep.replacement_text,
                    **insert_kwargs,
                )

                op = operations[prep.op_index]
                elapsed_op = int((time.monotonic() - t0) * 1000)

                if rc < 0:
                    any_insert_failed = True
                    results.append(TextReplaceResult(
                        success=False, original_text=prep.original_text,
                        new_text=prep.replacement_text, escalate=True,
                        error_message="Text insertion failed — overflow",
                        time_ms=elapsed_op, characters_changed=0,
                    ))
                else:
                    results.append(TextReplaceResult(
                        success=True,
                        original_text=prep.original_text,
                        new_text=prep.replacement_text,
                        time_ms=elapsed_op,
                        characters_changed=sum(a != b for a, b in zip(prep.original_text, prep.replacement_text)) + abs(len(prep.replacement_text) - len(prep.original_text)),
                    ))
                    logger.info(
                        "Batch redact-and-overlay [%d]: %r -> %r at rect=%s, "
                        "font=%s/%.1f, bg=%s",
                        prep.op_index, prep.original_text, prep.replacement_text,
                        prep.rect, prep.fontname, prep.fontsize, prep.bg_color,
                    )

            # Phase 5: Save once
            doc.save(str(working_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()

            # Phase 6: Re-render once
            if any(r.success for r in results):
                self._bump_version_and_render(session_id, page_num, working_path)

            return results

        except Exception as e:
            logger.error("Batch text replacement failed: %s", e, exc_info=True)
            elapsed = int((time.monotonic() - t0) * 1000)
            remaining = len(operations) - len(results)
            for _ in range(remaining):
                results.append(TextReplaceResult(
                    success=False, original_text="", new_text="",
                    escalate=True, error_message=f"Batch error: {e}",
                    time_ms=elapsed, characters_changed=0,
                ))
            return results
        finally:
            if not doc.is_closed:
                doc.close()

    # ------------------------------------------------------------------
    # Style change
    # ------------------------------------------------------------------

    def apply_style_change(
        self,
        session_id: str,
        page_num: int,
        target_text: str,
        changes: dict,
    ) -> StyleChangeResult:
        """Modify visual properties of text using redact-and-overlay."""
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
            check = self._check_pdf_access(doc)
            if check:
                elapsed = int((time.monotonic() - t0) * 1000)
                return StyleChangeResult(
                    success=False, target_text=target_text,
                    changes_applied={}, escalate=True,
                    error_message=check, time_ms=elapsed,
                )

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
                    new_flags |= (1 << 4) | (1 << 17)
                else:
                    new_flags &= ~((1 << 4) | (1 << 17))
                applied["bold"] = changes["bold"]

            if "italic" in changes:
                if changes["italic"]:
                    new_flags |= (1 << 1) | (1 << 5)
                else:
                    new_flags &= ~((1 << 1) | (1 << 5))
                applied["italic"] = changes["italic"]

            new_font = _match_font(original_font, new_flags)
            new_size = float(changes.get("font_size", original_size))
            if "font_size" in changes:
                applied["font_size"] = new_size

            new_color = original_color
            if "color" in changes:
                new_color = _hex_to_rgb(changes["color"])
                applied["color"] = changes["color"]

            new_width = fitz.get_text_length(target_text, fontname=new_font, fontsize=new_size)
            if rect.width > 0 and new_width > rect.width * OVERFLOW_RATIO:
                elapsed = int((time.monotonic() - t0) * 1000)
                return StyleChangeResult(
                    success=False, target_text=target_text,
                    changes_applied={}, escalate=True,
                    error_message=f"Style change would overflow: {new_width:.0f} vs {rect.width:.0f}",
                    time_ms=elapsed,
                )

            bg_color = self._detect_background_color(page, rect)
            expanded = _expand_rect_safe(page=page, rect=rect, target_text=target_text)
            annot = page.add_redact_annot(expanded)
            annot.set_colors(fill=bg_color)
            page.apply_redactions()

            text_point = fitz.Point(origin[0], origin[1]) if origin else \
                fitz.Point(rect.x0, rect.y0 + rect.height * 0.80)

            page.insert_text(
                text_point, target_text,
                fontname=new_font, fontsize=new_size, color=new_color,
            )

            doc.save(str(working_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
            doc.close()

            self._bump_version_and_render(session_id, page_num, working_path)

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
    def _check_pdf_access(doc: fitz.Document) -> str | None:
        """Check if the PDF allows modifications. Returns error message or None."""
        if doc.is_encrypted or doc.needs_pass:
            if not doc.authenticate(""):
                return "PDF is password-protected and cannot be edited programmatically"

        # Check permissions even if auto-authenticated (user-pw="" still
        # enforces owner-password restrictions on modification)
        perms = doc.permissions
        if perms is not None and perms != 0:
            if not (perms & fitz.PDF_PERM_MODIFY):
                return "PDF restricts modifications — cannot edit programmatically"
        return None

    @staticmethod
    def _find_target_rect(
        page: fitz.Page,
        original_text: str,
        match_strategy: str,
        context_before: str | None = None,
        context_after: str | None = None,
    ) -> fitz.Rect | list[fitz.Rect] | str:
        """Find the bounding rect(s) for target text with disambiguation.

        Returns:
          - fitz.Rect for a single match
          - list[fitz.Rect] if match_strategy is not first_occurrence and multiple
            unambiguous matches exist
          - str error message on failure
        """
        rects = page.search_for(original_text)

        if not rects:
            logger.warning("Text not found via search_for: %r", original_text)
            return f"Text '{original_text}' not found on page"

        # Multi-line detection: check if search returned adjacent rects for one hit
        # by using quads. If we get quads that span multiple lines for one hit, it's multi-line.
        quads = page.search_for(original_text, quads=True)
        if quads and len(quads) > len(rects):
            # Multi-line text wrapping detected
            logger.info(
                "Multi-line text detected: %d quads for %r (rects=%d)",
                len(quads), original_text, len(rects),
            )

        if len(rects) == 1 or match_strategy == "first_occurrence":
            return rects[0]

        # Multiple matches found — try context disambiguation
        if context_before or context_after:
            full_search = (context_before or "") + original_text + (context_after or "")
            context_rects = page.search_for(full_search)

            if len(context_rects) == 1:
                # Compute sub-rect for just the original_text portion
                full_rect = context_rects[0]

                prefix_len = len(context_before) if context_before else 0
                if prefix_len > 0:
                    prefix_text = full_search[:prefix_len]
                    props = None
                    blocks = page.get_text("dict", clip=full_rect,
                                           flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
                    for block in blocks:
                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                if original_text in span.get("text", ""):
                                    props = span
                                    break
                            if props:
                                break
                        if props:
                            break

                    if props:
                        fontname = _match_font(props["font"], props["flags"])
                        prefix_width = fitz.get_text_length(
                            prefix_text, fontname=fontname, fontsize=props["size"],
                        )
                        target_width = fitz.get_text_length(
                            original_text, fontname=fontname, fontsize=props["size"],
                        )
                        sub_rect = fitz.Rect(
                            full_rect.x0 + prefix_width,
                            full_rect.y0,
                            full_rect.x0 + prefix_width + target_width,
                            full_rect.y1,
                        )
                        logger.info(
                            "Context disambiguation: %r found via context %r...%r, "
                            "sub_rect=%s",
                            original_text, context_before, context_after, sub_rect,
                        )
                        return sub_rect

                # Fallback: just use the rect from context search directly
                # and search within it for the original text
                inner_rects = page.search_for(original_text, clip=context_rects[0])
                if inner_rects:
                    return inner_rects[0]
                return context_rects[0]

            elif len(context_rects) > 1:
                logger.warning(
                    "Context disambiguation still ambiguous: %d matches for %r",
                    len(context_rects), full_search,
                )
            else:
                logger.warning(
                    "Context search found nothing for %r", full_search,
                )

        # No context or context didn't help — escalate
        if match_strategy == "exact" and len(rects) > 1:
            return (
                f"Ambiguous: '{original_text}' found {len(rects)} times on page. "
                f"Provide context_before/context_after for disambiguation or "
                f"use 'first_occurrence' match_strategy."
            )

        return rects[0]

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
        """Sample background color around a text rect by rendering corner pixels."""
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

    def _bump_version_and_render(self, session_id: str, page_num: int, working_path) -> None:
        """Increment page version and re-render from working PDF."""
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
