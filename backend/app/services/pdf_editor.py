"""Programmatic PDF text editing engine using pikepdf.

Directly edits text content in the PDF content stream without AI involvement.
This is the fast, lossless path for text replacements and style changes.
"""

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf

from app.models.schemas import StyleChangeResult, TextReplaceResult
from app.services import pdf_service
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

# Overflow threshold — replacement > 120% of original triggers escalation
OVERFLOW_RATIO = 1.2


# ---------------------------------------------------------------------------
# Data structures for content-stream text operations
# ---------------------------------------------------------------------------


@dataclass
class TextOperation:
    """A single text-showing operation in the PDF content stream."""

    stream_index: int          # position in the parsed content stream list
    operator: str              # Tj, TJ, ', "
    text: str                  # decoded string content
    font_name: str = ""        # current font resource name (e.g. /F1)
    font_size: float = 0.0    # current font size
    font_subtype: str = ""     # Type1, TrueType, Type0 (CID), etc.
    encoding: str = ""         # WinAnsiEncoding, Identity-H, etc.


@dataclass
class TextMatch:
    """A match of target text within the content stream operations."""

    op_index: int              # index into the TextOperation list
    stream_index: int          # index into the raw content stream
    char_start: int            # character offset within the operation's text
    char_end: int              # exclusive end offset
    full_op_text: str          # the full text of the matched operation


# ---------------------------------------------------------------------------
# PdfEditor
# ---------------------------------------------------------------------------


class PdfEditor:
    """Directly edits text content in the PDF structure using pikepdf."""

    def __init__(self, session_manager: SessionManager):
        self.sessions = session_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def apply_text_replace(
        self,
        session_id: str,
        page_num: int,
        original_text: str,
        replacement_text: str,
        match_strategy: str = "exact",
    ) -> TextReplaceResult:
        """Replace text in the PDF structure without AI model involvement."""
        t0 = time.monotonic()

        # Check overflow threshold
        if len(original_text) > 0 and len(replacement_text) > OVERFLOW_RATIO * len(original_text):
            elapsed = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "Text replacement would overflow: %d -> %d chars (%.0f%%)",
                len(original_text), len(replacement_text),
                100 * len(replacement_text) / len(original_text),
            )
            return TextReplaceResult(
                success=False,
                original_text=original_text,
                new_text=replacement_text,
                escalate=True,
                error_message=(
                    f"Replacement text is {len(replacement_text)} chars vs "
                    f"original {len(original_text)} chars (>{OVERFLOW_RATIO:.0%}). "
                    f"Would likely overflow bounding box."
                ),
                time_ms=elapsed,
                characters_changed=0,
            )

        working_pdf = self.sessions.get_working_pdf_path(session_id)

        try:
            pdf = pikepdf.open(working_pdf, allow_overwriting_input=True)
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return TextReplaceResult(
                success=False, original_text=original_text,
                new_text=replacement_text, escalate=True,
                error_message=f"Failed to open PDF: {e}", time_ms=elapsed,
                characters_changed=0,
            )

        try:
            if page_num < 1 or page_num > len(pdf.pages):
                elapsed = int((time.monotonic() - t0) * 1000)
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text,
                    error_message=f"Page {page_num} out of range",
                    time_ms=elapsed, characters_changed=0,
                )

            page = pdf.pages[page_num - 1]
            content = pikepdf.parse_content_stream(page)

            # 1. Parse text operations
            operations = self._parse_text_operations(page, content)
            logger.info(
                "Page %d: found %d text operations, combined text: %d chars",
                page_num, len(operations),
                sum(len(op.text) for op in operations),
            )

            # Check for CID/complex fonts
            cid_ops = [op for op in operations if op.font_subtype == "Type0"]
            if cid_ops:
                # Check if the target text is in a CID font region
                for op in cid_ops:
                    if original_text in op.text:
                        elapsed = int((time.monotonic() - t0) * 1000)
                        return TextReplaceResult(
                            success=False, original_text=original_text,
                            new_text=replacement_text, escalate=True,
                            error_message=(
                                "Text uses CID font encoding "
                                f"(font: {op.font_name}), "
                                "falling back to visual edit"
                            ),
                            time_ms=elapsed, characters_changed=0,
                        )

            # 2. Find the text
            matches = self._find_text_in_operations(
                operations, original_text, match_strategy,
            )

            if not matches:
                elapsed = int((time.monotonic() - t0) * 1000)
                # Log available text for debugging
                all_text = " | ".join(op.text for op in operations)
                logger.warning(
                    "Text not found: %r (strategy=%s). Available: %s",
                    original_text, match_strategy, all_text[:300],
                )
                return TextReplaceResult(
                    success=False, original_text=original_text,
                    new_text=replacement_text, escalate=True,
                    error_message=f"Text '{original_text}' not found on page {page_num}",
                    time_ms=elapsed, characters_changed=0,
                )

            # For first_occurrence, use only the first match
            if match_strategy == "first_occurrence":
                matches = matches[:1]

            # 3. Apply replacements (reverse order to preserve stream indices)
            chars_changed = 0
            for match in reversed(matches):
                op = operations[match.op_index]
                self._apply_replacement(
                    content, match, op, original_text, replacement_text,
                )
                chars_changed += abs(len(replacement_text) - len(original_text))

            # 4. Write back to PDF
            new_stream = pikepdf.unparse_content_stream(content)
            page.Contents = pdf.make_stream(new_stream)
            pdf.save(str(working_pdf))
            logger.info(
                "Saved text replacement to working PDF: %s -> %s (%d matches)",
                original_text, replacement_text, len(matches),
            )

            # 5. Re-render the page
            session_path = self.sessions.get_session_path(session_id)
            metadata = self.sessions.get_metadata(session_id)
            current_version = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            new_version = current_version + 1

            pdf_service.render_page(
                working_pdf, page_num,
                session_path / "pages", version=new_version,
            )

            # Update metadata version
            metadata["current_page_versions"][str(page_num)] = new_version
            self.sessions.update_metadata(session_id, metadata)

            elapsed = int((time.monotonic() - t0) * 1000)
            return TextReplaceResult(
                success=True,
                original_text=original_text,
                new_text=replacement_text,
                time_ms=elapsed,
                characters_changed=chars_changed,
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
            pdf.close()

    def apply_style_change(
        self,
        session_id: str,
        page_num: int,
        target_text: str,
        changes: dict,
    ) -> StyleChangeResult:
        """Modify visual properties of text in the PDF content stream.

        Supports: color (hex string), font_size (float).
        Bold/italic require font switching and are escalated if the font
        isn't available in the page resources.
        """
        t0 = time.monotonic()
        working_pdf = self.sessions.get_working_pdf_path(session_id)

        try:
            pdf = pikepdf.open(working_pdf, allow_overwriting_input=True)
        except Exception as e:
            elapsed = int((time.monotonic() - t0) * 1000)
            return StyleChangeResult(
                success=False, target_text=target_text,
                changes_applied={}, escalate=True,
                error_message=f"Failed to open PDF: {e}", time_ms=elapsed,
            )

        try:
            page = pdf.pages[page_num - 1]
            content = pikepdf.parse_content_stream(page)
            operations = self._parse_text_operations(page, content)

            matches = self._find_text_in_operations(
                operations, target_text, "first_occurrence",
            )
            if not matches:
                elapsed = int((time.monotonic() - t0) * 1000)
                return StyleChangeResult(
                    success=False, target_text=target_text,
                    changes_applied={}, escalate=True,
                    error_message=f"Text '{target_text}' not found on page",
                    time_ms=elapsed,
                )

            match = matches[0]
            op = operations[match.op_index]
            applied: dict = {}
            stream_idx = match.stream_index

            # Find the BT that starts this text object (scan backwards)
            bt_idx = self._find_preceding_bt(content, stream_idx)

            # --- Color change ---
            color_hex = changes.get("color")
            if color_hex:
                r, g, b = self._hex_to_rgb(color_hex)
                color_operands = [
                    pikepdf.Object.parse(f"{r:.3f}".encode()),
                    pikepdf.Object.parse(f"{g:.3f}".encode()),
                    pikepdf.Object.parse(f"{b:.3f}".encode()),
                ]
                color_op = pikepdf.Operator("rg")
                # Insert color op right after BT (or before the Tj/TJ)
                insert_at = bt_idx + 1 if bt_idx >= 0 else stream_idx
                content.insert(insert_at, pikepdf.ContentStreamInstruction(
                    color_operands, color_op,
                ))
                # Shift indices for subsequent operations
                if insert_at <= stream_idx:
                    stream_idx += 1
                applied["color"] = color_hex

            # --- Font size change ---
            new_size = changes.get("font_size")
            if new_size is not None:
                new_size = float(new_size)
                # Find the Tf operator for this text object
                tf_idx = self._find_tf_for_text(content, stream_idx)
                if tf_idx >= 0:
                    old_operands, old_op = content[tf_idx]
                    font_ref = old_operands[0]  # keep same font
                    content[tf_idx] = pikepdf.ContentStreamInstruction(
                        [font_ref, pikepdf.Object.parse(f"{new_size:.1f}".encode())],
                        old_op,
                    )
                    applied["font_size"] = new_size

            # --- Bold/italic (font switch) ---
            bold = changes.get("bold")
            italic = changes.get("italic")
            font_name_req = changes.get("font_name")

            if bold is not None or italic is not None or font_name_req:
                result = self._apply_font_switch(
                    pdf, page, content, stream_idx, op,
                    bold=bold, italic=italic, font_name=font_name_req,
                )
                if result is None:
                    elapsed = int((time.monotonic() - t0) * 1000)
                    return StyleChangeResult(
                        success=False, target_text=target_text,
                        changes_applied=applied, escalate=True,
                        error_message="Required font not available in PDF resources",
                        time_ms=elapsed,
                    )
                applied.update(result)

            # Save
            new_stream = pikepdf.unparse_content_stream(content)
            page.Contents = pdf.make_stream(new_stream)
            pdf.save(str(working_pdf))

            # Re-render
            session_path = self.sessions.get_session_path(session_id)
            metadata = self.sessions.get_metadata(session_id)
            current_version = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            new_version = current_version + 1
            pdf_service.render_page(
                working_pdf, page_num,
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
            pdf.close()

    # ------------------------------------------------------------------
    # Content stream parsing
    # ------------------------------------------------------------------

    def _parse_text_operations(
        self, page, content: list,
    ) -> list[TextOperation]:
        """Parse the content stream and extract all text-showing operations.

        Tracks graphics state (font, size) as we walk the stream.
        """
        # Build font info lookup from page resources
        font_info = self._get_font_info(page)

        operations: list[TextOperation] = []
        current_font = ""
        current_size = 0.0

        for i, (operands, operator) in enumerate(content):
            op_str = str(operator)

            # Track font state
            if op_str == "Tf" and len(operands) >= 2:
                current_font = str(operands[0])
                try:
                    current_size = float(str(operands[1]))
                except (ValueError, TypeError):
                    pass

            # Text-showing operators
            elif op_str == "Tj" and operands:
                text = self._decode_pdf_string(operands[0])
                if text:
                    info = font_info.get(current_font, {})
                    operations.append(TextOperation(
                        stream_index=i,
                        operator="Tj",
                        text=text,
                        font_name=current_font,
                        font_size=current_size,
                        font_subtype=info.get("subtype", ""),
                        encoding=info.get("encoding", ""),
                    ))

            elif op_str == "TJ" and operands:
                # TJ takes an array: [(string) kern (string) kern ...]
                arr = operands[0] if len(operands) == 1 else operands
                text = self._extract_tj_text(arr)
                if text:
                    info = font_info.get(current_font, {})
                    operations.append(TextOperation(
                        stream_index=i,
                        operator="TJ",
                        text=text,
                        font_name=current_font,
                        font_size=current_size,
                        font_subtype=info.get("subtype", ""),
                        encoding=info.get("encoding", ""),
                    ))

            elif op_str in ("'", '"') and operands:
                # ' shows string with newline; " adds word/char spacing
                last_op = operands[-1] if operands else None
                text = self._decode_pdf_string(last_op) if last_op else ""
                if text:
                    info = font_info.get(current_font, {})
                    operations.append(TextOperation(
                        stream_index=i,
                        operator=op_str,
                        text=text,
                        font_name=current_font,
                        font_size=current_size,
                        font_subtype=info.get("subtype", ""),
                        encoding=info.get("encoding", ""),
                    ))

        return operations

    def _find_text_in_operations(
        self,
        operations: list[TextOperation],
        target: str,
        strategy: str,
    ) -> list[TextMatch]:
        """Find target text across potentially multiple operations.

        Handles:
        - Target fully within a single operation (common case)
        - Target spanning adjacent operations (rare but possible)
        """
        matches: list[TextMatch] = []

        # First try: single-operation matches
        for idx, op in enumerate(operations):
            if strategy == "contains":
                if target in op.text:
                    start = op.text.index(target)
                    matches.append(TextMatch(
                        op_index=idx,
                        stream_index=op.stream_index,
                        char_start=start,
                        char_end=start + len(target),
                        full_op_text=op.text,
                    ))
            elif strategy == "exact":
                if op.text == target or target in op.text:
                    start = op.text.index(target)
                    matches.append(TextMatch(
                        op_index=idx,
                        stream_index=op.stream_index,
                        char_start=start,
                        char_end=start + len(target),
                        full_op_text=op.text,
                    ))
            elif strategy == "first_occurrence":
                if target in op.text:
                    start = op.text.index(target)
                    matches.append(TextMatch(
                        op_index=idx,
                        stream_index=op.stream_index,
                        char_start=start,
                        char_end=start + len(target),
                        full_op_text=op.text,
                    ))
                    return matches  # first only

        if matches:
            return matches

        # Second try: cross-operation matching (concatenate adjacent ops)
        if len(operations) >= 2:
            for idx in range(len(operations) - 1):
                combined = operations[idx].text + operations[idx + 1].text
                if target in combined:
                    start = combined.index(target)
                    # Check if it actually spans both ops
                    if start < len(operations[idx].text) and start + len(target) > len(operations[idx].text):
                        # Spans two operations — match the first one, we'll
                        # handle the split in _apply_replacement
                        matches.append(TextMatch(
                            op_index=idx,
                            stream_index=operations[idx].stream_index,
                            char_start=start,
                            char_end=len(operations[idx].text),
                            full_op_text=combined,
                        ))
                        if strategy == "first_occurrence":
                            return matches

        return matches

    # ------------------------------------------------------------------
    # Replacement application
    # ------------------------------------------------------------------

    def _apply_replacement(
        self,
        content: list,
        match: TextMatch,
        op: TextOperation,
        original_text: str,
        replacement_text: str,
    ) -> None:
        """Apply a text replacement to the content stream."""
        idx = match.stream_index
        operands, operator = content[idx]
        op_str = str(operator)

        if op_str == "Tj":
            old_text = self._decode_pdf_string(operands[0])
            new_text = old_text[:match.char_start] + replacement_text + old_text[match.char_end:]
            content[idx] = pikepdf.ContentStreamInstruction(
                [pikepdf.String(new_text.encode("latin-1", errors="replace"))],
                operator,
            )
            logger.debug("Tj replace at [%d]: %r -> %r", idx, old_text, new_text)

        elif op_str == "TJ":
            arr = operands[0] if len(operands) == 1 else operands
            self._replace_in_tj_array(content, idx, arr, operator, original_text, replacement_text)

        elif op_str in ("'", '"'):
            # Last operand is the string
            old_text = self._decode_pdf_string(operands[-1])
            new_text = old_text.replace(original_text, replacement_text, 1)
            new_operands = list(operands)
            new_operands[-1] = pikepdf.String(new_text.encode("latin-1", errors="replace"))
            content[idx] = pikepdf.ContentStreamInstruction(new_operands, operator)

    def _replace_in_tj_array(
        self,
        content: list,
        stream_idx: int,
        arr,
        operator,
        old_text: str,
        new_text: str,
    ) -> None:
        """Replace text within a TJ array, preserving kerning structure."""
        # Extract text parts and kerning values
        parts: list[tuple[str, object]] = []  # ("text", str) or ("kern", value)
        for item in arr:
            if isinstance(item, pikepdf.String):
                parts.append(("text", self._decode_pdf_string(item)))
            else:
                parts.append(("kern", item))

        # Build full text and find the target
        full = "".join(p[1] for p in parts if p[0] == "text")
        if old_text not in full:
            return

        new_full = full.replace(old_text, new_text, 1)

        # Redistribute text across the same kerning structure
        new_items = []
        text_pos = 0
        for ptype, pval in parts:
            if ptype == "kern":
                new_items.append(pval)
            else:
                chunk_len = len(pval)
                end = min(text_pos + chunk_len, len(new_full))
                chunk = new_full[text_pos:end]
                if chunk:
                    new_items.append(
                        pikepdf.String(chunk.encode("latin-1", errors="replace"))
                    )
                text_pos = end

        # Append any remaining text
        if text_pos < len(new_full):
            remainder = new_full[text_pos:]
            new_items.append(
                pikepdf.String(remainder.encode("latin-1", errors="replace"))
            )

        new_arr = pikepdf.Array(new_items)
        content[stream_idx] = pikepdf.ContentStreamInstruction(
            [new_arr], operator,
        )
        logger.debug("TJ replace at [%d]: %r -> %r", stream_idx, full, new_full)

    # ------------------------------------------------------------------
    # Style change helpers
    # ------------------------------------------------------------------

    def _find_preceding_bt(self, content: list, stream_idx: int) -> int:
        """Scan backwards from stream_idx to find the BT operator."""
        for i in range(stream_idx - 1, -1, -1):
            if str(content[i][1]) == "BT":
                return i
        return -1

    def _find_tf_for_text(self, content: list, stream_idx: int) -> int:
        """Find the Tf (font) operator that applies to the text at stream_idx.

        Scans backwards from the text op to find the most recent Tf.
        """
        for i in range(stream_idx - 1, -1, -1):
            if str(content[i][1]) == "Tf":
                return i
        return -1

    def _apply_font_switch(
        self,
        pdf: pikepdf.Pdf,
        page,
        content: list,
        stream_idx: int,
        op: TextOperation,
        bold: bool | None = None,
        italic: bool | None = None,
        font_name: str | None = None,
    ) -> dict | None:
        """Try to switch the font for a text operation.

        Returns dict of applied changes, or None if the required font isn't available.
        """
        resources = page.get("/Resources", {})
        fonts = resources.get("/Font", {})

        # Build available font map: resource_name -> base_font_name
        available: dict[str, str] = {}
        for res_name in fonts:
            try:
                base = str(fonts[res_name].get("/BaseFont", ""))
                available[res_name] = base.lstrip("/")
            except Exception:
                continue

        if font_name:
            # Direct font name request
            target_base = font_name
        else:
            # Derive target from current font + bold/italic
            current_base = ""
            for res_name, base in available.items():
                if res_name == op.font_name:
                    current_base = base
                    break

            target_base = self._derive_font_variant(current_base, bold, italic)

        # Find the resource name for the target font
        target_ref = None
        for res_name, base in available.items():
            if base == target_base or target_base in base:
                target_ref = res_name
                break

        if target_ref is None:
            logger.warning(
                "Font '%s' not in page resources: %s",
                target_base, list(available.values()),
            )
            return None

        # Apply: find the Tf and change the font reference
        tf_idx = self._find_tf_for_text(content, stream_idx)
        if tf_idx >= 0:
            old_operands, old_op = content[tf_idx]
            size = old_operands[1] if len(old_operands) >= 2 else pikepdf.Object.parse(b"12")
            content[tf_idx] = pikepdf.ContentStreamInstruction(
                [pikepdf.Name(target_ref), size], old_op,
            )

            applied = {}
            if bold is not None:
                applied["bold"] = bold
            if italic is not None:
                applied["italic"] = italic
            if font_name:
                applied["font_name"] = font_name
            return applied

        return None

    @staticmethod
    def _derive_font_variant(
        base_font: str, bold: bool | None, italic: bool | None,
    ) -> str:
        """Derive the target font name from a base font + style flags.

        e.g. Helvetica + bold=True -> Helvetica-Bold
             Helvetica-Bold + italic=True -> Helvetica-BoldOblique
        """
        # Normalize: strip existing style suffixes
        root = base_font
        for suffix in ("-Bold", "-Italic", "-Oblique", "-BoldOblique", "-BoldItalic"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break

        is_bold = bold if bold is not None else ("Bold" in base_font)
        is_italic = italic if italic is not None else (
            "Italic" in base_font or "Oblique" in base_font
        )

        if is_bold and is_italic:
            return f"{root}-BoldOblique"
        elif is_bold:
            return f"{root}-Bold"
        elif is_italic:
            return f"{root}-Oblique"
        return root

    # ------------------------------------------------------------------
    # PDF string / font helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_pdf_string(obj) -> str:
        """Decode a pikepdf.String or similar object to a Python string."""
        if isinstance(obj, pikepdf.String):
            raw = bytes(obj)
            # Try UTF-16BE (BOM), then latin-1 as fallback
            if raw[:2] == b"\xfe\xff":
                return raw[2:].decode("utf-16-be", errors="replace")
            return raw.decode("latin-1", errors="replace")
        return str(obj) if obj else ""

    @staticmethod
    def _extract_tj_text(arr) -> str:
        """Extract concatenated text from a TJ array."""
        parts = []
        try:
            for item in arr:
                if isinstance(item, pikepdf.String):
                    raw = bytes(item)
                    if raw[:2] == b"\xfe\xff":
                        parts.append(raw[2:].decode("utf-16-be", errors="replace"))
                    else:
                        parts.append(raw.decode("latin-1", errors="replace"))
        except TypeError:
            pass
        return "".join(parts)

    @staticmethod
    def _get_font_info(page) -> dict[str, dict]:
        """Extract font metadata from the page resources."""
        info: dict[str, dict] = {}
        try:
            resources = page.get("/Resources")
            if not resources:
                return info
            fonts = resources.get("/Font")
            if not fonts:
                return info
            for name in fonts:
                try:
                    font = fonts[name]
                    subtype = str(font.get("/Subtype", "")).lstrip("/")
                    encoding = str(font.get("/Encoding", "")).lstrip("/")
                    base_font = str(font.get("/BaseFont", "")).lstrip("/")
                    info[str(name)] = {
                        "subtype": subtype,
                        "encoding": encoding,
                        "base_font": base_font,
                    }
                except Exception:
                    continue
        except Exception:
            pass
        return info

    @staticmethod
    def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
        """Convert '#RRGGBB' to (r, g, b) floats in 0-1 range."""
        h = hex_color.lstrip("#")
        if len(h) != 6:
            return (0.0, 0.0, 0.0)
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        return (r, g, b)
