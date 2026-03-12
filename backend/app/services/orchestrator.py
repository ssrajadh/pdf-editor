"""Orchestrator — assembles page context, drives the planning LLM,
and coordinates execution of the resulting plan."""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from PIL import Image

from app.models.schemas import (
    ExecutionPlan,
    ExecutionResult,
    OperationResult,
    OperationType,
    TextBlock,
    TextReplaceOp,
    StyleChangeOp,
    VisualRegenerateOp,
)
from app.prompts.orchestrator_plan import (
    ORCHESTRATOR_SYSTEM_PROMPT,
)
from app.services.model_provider import ModelProvider
from app.services import pdf_service
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, dict | None], Awaitable[None]]

VISUAL_DESCRIPTION_PROMPT = """\
Describe the non-text visual elements on this PDF page. Focus on:
- Charts and graphs (type, approximate position, what data they show)
- Images and photos (subject, position)
- Logos and icons (what they are, position)
- Decorative elements (borders, backgrounds, color blocks, dividers)
- Layout structure (columns, sections, headers/footers)
Do NOT describe the text content — I already have that. Be concise. Use positions like \
'top-left', 'center', 'bottom-right', 'left column', etc."""


# ---------------------------------------------------------------------------
# PageContext — everything the planner needs about a page
# ---------------------------------------------------------------------------


@dataclass
class PageContext:
    """All the context the planner needs about the current page."""

    page_num: int
    page_width: float
    page_height: float
    full_text: str
    text_blocks: list[TextBlock] = field(default_factory=list)
    visual_description: str = ""


async def describe_visual_elements(
    image: Image.Image,
    text_content: str,
    provider: ModelProvider,
) -> str:
    """Send the page image to a vision model to describe non-text visual elements."""
    prompt = VISUAL_DESCRIPTION_PROMPT
    if text_content:
        prompt += (
            "\n\nFor reference, here is the text already extracted from this page "
            "(do NOT repeat it):\n"
            f'"""\n{text_content[:2000]}\n"""'
        )

    description = await provider.analyze_image(image, prompt)
    logger.info(
        "Visual description for page (%d chars): %s",
        len(description),
        description[:120],
    )
    return description


async def build_page_context(
    session_id: str,
    page_num: int,
    provider: ModelProvider,
    session_mgr: SessionManager,
) -> PageContext:
    """Assemble all context the planner needs for a page."""
    session_path = session_mgr.get_session_path(session_id)
    pdf_path = session_path / "original.pdf"
    metadata = session_mgr.get_metadata(session_id)

    text_data = pdf_service.extract_text(pdf_path, page_num)
    full_text: str = text_data["full_text"]
    text_blocks = [TextBlock(**b) for b in text_data["blocks"]]

    dims = pdf_service.get_page_dimensions(pdf_path)
    page_width, page_height = dims[page_num - 1]

    current_version = int(
        metadata.get("current_page_versions", {}).get(str(page_num), 0)
    )
    image_path = pdf_service.get_page_image_path(session_path, page_num)

    cache_path = (
        session_path / "edits" / f"page_{page_num}_v{current_version}_vis_desc.txt"
    )
    if cache_path.exists():
        visual_description = cache_path.read_text()
        logger.info("Using cached visual description for page %d v%d", page_num, current_version)
    else:
        image = Image.open(image_path)
        visual_description = await describe_visual_elements(image, full_text, provider)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(visual_description)

    return PageContext(
        page_num=page_num,
        page_width=page_width,
        page_height=page_height,
        full_text=full_text,
        text_blocks=text_blocks,
        visual_description=visual_description,
    )


def page_context_to_text_blocks_json(ctx: PageContext) -> str:
    """Serialize text blocks to the JSON format the planner prompt expects."""
    blocks = [
        {
            "text": b.text, "x0": b.x0, "y0": b.y0,
            "x1": b.x1, "y1": b.y1,
            "font_name": b.font_name, "font_size": b.font_size,
        }
        for b in ctx.text_blocks
    ]
    return json.dumps(blocks, indent=2)


# ---------------------------------------------------------------------------
# Plan JSON parsing
# ---------------------------------------------------------------------------


def _parse_plan_json(raw: str) -> ExecutionPlan:
    """Parse raw LLM output into an ExecutionPlan, handling markdown fences."""
    text = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response: {raw[:300]}")
    json_str = text[start : end + 1]
    data = json.loads(json_str)
    return ExecutionPlan.model_validate(data)


def _make_fallback_plan(instruction: str) -> ExecutionPlan:
    """Graceful degradation: a single visual_regenerate for the full page."""
    return ExecutionPlan(
        operations=[
            VisualRegenerateOp(
                prompt=instruction, region="full_page", confidence=0.7,
                reasoning="Fallback: planner failed to produce a valid plan. "
                "Routing entire instruction to visual editing.",
            )
        ],
        execution_order=[0],
        summary=f"Fallback visual edit: {instruction}",
        all_programmatic=False,
    )


# ---------------------------------------------------------------------------
# Orchestrator — plan + execute
# ---------------------------------------------------------------------------


class Orchestrator:
    """The brain of Nano PDF Studio. Takes a user's natural language edit
    instruction, produces a structured execution plan, and coordinates
    execution across the programmatic and visual editing engines."""

    def __init__(
        self,
        model_provider: ModelProvider,
        session_manager: SessionManager,
    ):
        self.provider = model_provider
        self.sessions = session_manager

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------

    async def plan(
        self,
        session_id: str,
        page_num: int,
        instruction: str,
        on_progress: ProgressCallback,
    ) -> ExecutionPlan:
        """Build page context, call the planning LLM, parse into ExecutionPlan."""
        await on_progress("planning", "Analyzing edit instruction...", None)

        ctx = await build_page_context(
            session_id, page_num, self.provider, self.sessions,
        )

        text_blocks_json = page_context_to_text_blocks_json(ctx)
        from app.prompts.orchestrator_plan import ORCHESTRATOR_USER_TEMPLATE
        user_content = ORCHESTRATOR_USER_TEMPLATE.format(
            user_instruction=instruction,
            page_text=ctx.full_text,
            text_blocks=text_blocks_json,
            page_width=ctx.page_width,
            page_height=ctx.page_height,
            visual_description=ctx.visual_description,
        )

        raw = await self.provider.plan_edit(
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            user_message=user_content,
        )
        logger.info("Planner raw response (%d chars): %s", len(raw), raw[:200])

        plan = await self._parse_with_retry(raw, instruction)

        op_types = [op.type for op in plan.operations]
        prog_count = sum(1 for t in op_types if t != "visual_regenerate")
        vis_count = sum(1 for t in op_types if t == "visual_regenerate")
        logger.info(
            "Plan: %d ops (%d programmatic, %d visual) — %s",
            len(plan.operations), prog_count, vis_count, plan.summary,
        )

        await on_progress(
            "planned",
            f"Plan: {len(plan.operations)} operations — "
            f"{prog_count} programmatic, {vis_count} visual",
            {"plan": plan.model_dump()},
        )
        return plan

    async def _parse_with_retry(
        self, raw: str, instruction: str,
    ) -> ExecutionPlan:
        """Parse plan JSON with one retry on failure, then fallback."""
        try:
            return _parse_plan_json(raw)
        except Exception as first_err:
            logger.warning("Plan parse failed: %s — retrying with repair prompt", first_err)

        repair_msg = (
            "Your previous response was not valid JSON. "
            f"Here was the error: {first_err}\n\n"
            "Please return ONLY valid JSON matching the ExecutionPlan schema."
        )
        try:
            raw2 = await self.provider.plan_edit(
                system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
                user_message=repair_msg,
            )
            return _parse_plan_json(raw2)
        except Exception as retry_err:
            logger.error("Plan parse retry also failed: %s — using fallback", retry_err)
            return _make_fallback_plan(instruction)

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def execute(
        self,
        session_id: str,
        page_num: int,
        plan: ExecutionPlan,
        instruction: str,
        on_progress: ProgressCallback,
    ) -> ExecutionResult:
        """Execute a plan's operations in order.

        Programmatic ops modify the working PDF and re-render from it.
        Visual ops always get a fresh PDF-rendered base image (never an
        AI-generated one) via get_current_base_image().
        """
        t_start = time.monotonic()
        session_path = self.sessions.get_session_path(session_id)
        op_results: list[OperationResult] = []

        programmatic_ran = False
        visual_ran = False

        for exec_pos, op_idx in enumerate(plan.execution_order):
            if op_idx < 0 or op_idx >= len(plan.operations):
                logger.warning("Skipping invalid execution_order index %d", op_idx)
                continue

            op = plan.operations[op_idx]
            t_op = time.monotonic()

            if isinstance(op, (TextReplaceOp, StyleChangeOp)):
                result = await self._execute_programmatic(
                    op, op_idx, session_id, page_num, instruction, on_progress,
                )
                if result.path == "programmatic" and result.success:
                    programmatic_ran = True
                elif result.path == "fallback_visual" and result.success:
                    visual_ran = True

            elif isinstance(op, VisualRegenerateOp):
                # Read current version (may have been bumped by preceding
                # programmatic ops).
                metadata = self.sessions.get_metadata(session_id)
                cur_v = int(
                    metadata.get("current_page_versions", {}).get(str(page_num), 0)
                )
                result = await self._execute_visual(
                    op, op_idx, session_id, page_num, cur_v + 1,
                    on_progress, programmatic_ran,
                )
                if result.success:
                    visual_ran = True
            else:
                continue

            result.time_ms = int((time.monotonic() - t_op) * 1000)
            op_results.append(result)

        # If nothing succeeded, full-page visual fallback
        if not op_results or not any(r.success for r in op_results):
            logger.warning("No operations succeeded, running full-page visual fallback")
            await on_progress("generating", "Falling back to full-page AI edit...", None)
            metadata = self.sessions.get_metadata(session_id)
            cur_v = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            fallback_result = await self._execute_visual(
                VisualRegenerateOp(
                    prompt=instruction, region="full_page", confidence=0.7,
                    reasoning="Full fallback — no operations succeeded.",
                ),
                op_idx=-1, session_id=session_id, page_num=page_num,
                version=cur_v + 1, on_progress=on_progress,
                programmatic_preceded=False,
            )
            fallback_result.time_ms = int((time.monotonic() - t_start) * 1000)
            op_results.append(fallback_result)
            visual_ran = True

        # Read final version
        metadata = self.sessions.get_metadata(session_id)
        final_version = int(
            metadata.get("current_page_versions", {}).get(str(page_num), 0)
        )

        # --- Text layer handling ---
        if programmatic_ran and not visual_ran:
            # All programmatic — extract perfect text layer from working PDF
            text_layer_source = "programmatic_edit"
            await self._save_text_layer_from_working_pdf(
                session_id, page_num, final_version,
            )
        elif programmatic_ran and visual_ran:
            text_layer_source = "mixed"
            # Mark text layer as stale — visual ops changed the rendered output
            self._save_stale_text_layer(session_path, page_num, final_version)
        elif visual_ran:
            text_layer_source = "ocr"
            self._save_stale_text_layer(session_path, page_num, final_version)
        else:
            text_layer_source = "original"

        # Save edit record
        self._save_edit_record(
            session_path, page_num, final_version, instruction,
            text_layer_preserved=(text_layer_source in ("programmatic_edit", "original")),
        )

        total_ms = int((time.monotonic() - t_start) * 1000)
        prog_count = sum(1 for r in op_results if r.path == "programmatic")
        vis_count = sum(
            1 for r in op_results if r.path in ("visual", "fallback_visual")
        )

        return ExecutionResult(
            session_id=session_id,
            page_num=page_num,
            version=final_version,
            plan_summary=plan.summary,
            operations=op_results,
            total_time_ms=total_ms,
            programmatic_count=prog_count,
            visual_count=vis_count,
            text_layer_source=text_layer_source,
        )

    # ------------------------------------------------------------------
    # Programmatic execution (PdfEditor)
    # ------------------------------------------------------------------

    async def _execute_programmatic(
        self,
        op: TextReplaceOp | StyleChangeOp,
        op_idx: int,
        session_id: str,
        page_num: int,
        instruction: str,
        on_progress: ProgressCallback,
    ) -> OperationResult:
        """Execute a programmatic op via PdfEditor. Falls back to visual on failure."""
        from app.services.pdf_editor import PdfEditor
        editor = PdfEditor(session_manager=self.sessions)

        if isinstance(op, TextReplaceOp):
            desc = f"'{op.original_text}' -> '{op.replacement_text}'"
            await on_progress(
                "programmatic", f"Text replacement: {desc}", {"op_index": op_idx},
            )

            # Skip low-confidence ops (planner flagged them for visual fallback)
            if op.confidence < 0.5:
                logger.info(
                    "Skipping low-confidence text_replace (%.2f): %s",
                    op.confidence, desc,
                )
                return OperationResult(
                    op_index=op_idx,
                    op_type=OperationType(op.type),
                    success=False, time_ms=0, path="programmatic",
                    detail=f"Skipped: planner confidence {op.confidence:.2f} < 0.5",
                    error="Low confidence — visual fallback expected",
                )

            result = await asyncio.to_thread(
                editor.apply_text_replace,
                session_id, page_num,
                op.original_text, op.replacement_text, op.match_strategy,
            )

            if result.success:
                await on_progress(
                    "programmatic",
                    f"Text replaced: {desc} ({result.time_ms}ms)",
                    {"op_index": op_idx},
                )
                return OperationResult(
                    op_index=op_idx,
                    op_type=OperationType.TEXT_REPLACE,
                    success=True, time_ms=result.time_ms, path="programmatic",
                    detail=f"Text replaced: {desc} ({result.characters_changed} chars changed)",
                )

            if result.escalate:
                logger.warning(
                    "Programmatic text_replace failed for op %d, escalating: %s",
                    op_idx, result.error_message,
                )
                await on_progress(
                    "generating",
                    f"Text replacement failed ({result.error_message}), "
                    f"falling back to AI visual edit",
                    {"op_index": op_idx},
                )
                return await self._visual_fallback_for_programmatic(
                    op, op_idx, session_id, page_num, on_progress,
                )

            return OperationResult(
                op_index=op_idx, op_type=OperationType.TEXT_REPLACE,
                success=False, time_ms=result.time_ms, path="programmatic",
                detail=f"Text replace failed: {result.error_message}",
                error=result.error_message,
            )

        else:  # StyleChangeOp
            desc = f"style on '{op.target_text}': {op.changes}"
            await on_progress(
                "programmatic", f"Style change: {desc}", {"op_index": op_idx},
            )

            result = await asyncio.to_thread(
                editor.apply_style_change,
                session_id, page_num, op.target_text, op.changes,
            )

            if result.success:
                await on_progress(
                    "programmatic",
                    f"Style changed: {result.changes_applied} ({result.time_ms}ms)",
                    {"op_index": op_idx},
                )
                return OperationResult(
                    op_index=op_idx, op_type=OperationType.STYLE_CHANGE,
                    success=True, time_ms=result.time_ms, path="programmatic",
                    detail=f"Style changed: {result.changes_applied}",
                )

            if result.escalate:
                logger.warning(
                    "Programmatic style_change failed for op %d, escalating: %s",
                    op_idx, result.error_message,
                )
                await on_progress(
                    "generating",
                    f"Style change failed ({result.error_message}), "
                    f"falling back to AI visual edit",
                    {"op_index": op_idx},
                )
                return await self._visual_fallback_for_programmatic(
                    op, op_idx, session_id, page_num, on_progress,
                )

            return OperationResult(
                op_index=op_idx, op_type=OperationType.STYLE_CHANGE,
                success=False, time_ms=result.time_ms, path="programmatic",
                detail=f"Style change failed: {result.error_message}",
                error=result.error_message,
            )

    # ------------------------------------------------------------------
    # Visual execution
    # ------------------------------------------------------------------

    async def _execute_visual(
        self,
        op: VisualRegenerateOp,
        op_idx: int,
        session_id: str,
        page_num: int,
        version: int,
        on_progress: ProgressCallback,
        programmatic_preceded: bool = False,
    ) -> OperationResult:
        """Execute a visual_regenerate operation.

        CRITICAL: The base image is always rendered from the PDF (working or
        original), never from a previously AI-generated image. This prevents
        compound quality degradation.

        If programmatic edits ran before this visual op, the working PDF
        already contains those changes, so the rendered base image will
        reflect them — giving the visual model the correct starting point.
        """
        prompt_preview = op.prompt[:80] + ("..." if len(op.prompt) > 80 else "")
        await on_progress(
            "generating", f"AI editing: {prompt_preview}", {"op_index": op_idx},
        )

        try:
            session_path = self.sessions.get_session_path(session_id)

            # COMPOUND DEGRADATION PREVENTION: render from PDF, not from
            # the page image cache (which might be AI-generated)
            base_image = await asyncio.to_thread(
                pdf_service.get_current_base_image, session_path, page_num,
            )
            logger.info(
                "Visual op %d: base image from %s PDF (%dx%d)",
                op_idx,
                "working" if (session_path / "working.pdf").exists() else "original",
                base_image.size[0], base_image.size[1],
            )

            result_image = await self.provider.edit_image(base_image, op.prompt)

            new_path = session_path / "pages" / f"page_{page_num}_v{version}.png"
            await asyncio.to_thread(result_image.save, new_path, "PNG")
            logger.info("Visual op %d saved: %s", op_idx, new_path.name)

            metadata = self.sessions.get_metadata(session_id)
            metadata["current_page_versions"][str(page_num)] = version
            self.sessions.update_metadata(session_id, metadata)

            return OperationResult(
                op_index=op_idx,
                op_type=OperationType.VISUAL_REGENERATE,
                success=True, time_ms=0, path="visual",
                detail=f"Visual regenerate ({op.region or 'full_page'}): {op.prompt[:100]}",
            )
        except Exception as e:
            logger.error("Visual op %d failed: %s", op_idx, e, exc_info=True)
            return OperationResult(
                op_index=op_idx,
                op_type=OperationType.VISUAL_REGENERATE,
                success=False, time_ms=0, path="visual",
                detail="Visual regenerate failed", error=str(e),
            )

    async def _visual_fallback_for_programmatic(
        self,
        op: TextReplaceOp | StyleChangeOp,
        op_idx: int,
        session_id: str,
        page_num: int,
        on_progress: ProgressCallback,
    ) -> OperationResult:
        """Fall back to visual editing when a programmatic op fails.

        Uses get_current_base_image() to avoid compound degradation.
        """
        if isinstance(op, TextReplaceOp):
            visual_prompt = (
                f"In this PDF page, find the text '{op.original_text}' and "
                f"change it to '{op.replacement_text}'. Keep everything else "
                f"exactly the same."
            )
        else:
            changes_desc = ", ".join(f"{k}: {v}" for k, v in op.changes.items())
            visual_prompt = (
                f"In this PDF page, change the style of the text "
                f"'{op.target_text}' to have these properties: {changes_desc}. "
                f"Keep everything else exactly the same."
            )

        try:
            session_path = self.sessions.get_session_path(session_id)

            # COMPOUND DEGRADATION PREVENTION
            base_image = await asyncio.to_thread(
                pdf_service.get_current_base_image, session_path, page_num,
            )

            result_image = await self.provider.edit_image(base_image, visual_prompt)

            metadata = self.sessions.get_metadata(session_id)
            current_version = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            new_version = current_version + 1

            new_path = session_path / "pages" / f"page_{page_num}_v{new_version}.png"
            await asyncio.to_thread(result_image.save, new_path, "PNG")

            metadata["current_page_versions"][str(page_num)] = new_version
            self.sessions.update_metadata(session_id, metadata)

            return OperationResult(
                op_index=op_idx,
                op_type=OperationType(op.type),
                success=True, time_ms=0, path="fallback_visual",
                detail=f"Programmatic {op.type} failed, visual fallback succeeded",
            )
        except Exception as e:
            logger.error("Visual fallback for op %d failed: %s", op_idx, e)
            return OperationResult(
                op_index=op_idx,
                op_type=OperationType(op.type),
                success=False, time_ms=0, path="fallback_visual",
                detail=f"Programmatic {op.type} failed, visual fallback also failed",
                error=str(e),
            )

    # ------------------------------------------------------------------
    # Text layer handling
    # ------------------------------------------------------------------

    async def _save_text_layer_from_working_pdf(
        self,
        session_id: str,
        page_num: int,
        version: int,
    ) -> None:
        """Extract text layer from the working PDF after programmatic edits.

        This gives PERFECT text accuracy — extracted directly from the
        modified PDF structure, no OCR or AI inference needed.
        """
        session_path = self.sessions.get_session_path(session_id)
        working_pdf = session_path / "working.pdf"
        if not working_pdf.exists():
            return

        text_data = await asyncio.to_thread(
            pdf_service.extract_text, working_pdf, page_num,
        )

        layer_path = session_path / "edits" / f"page_{page_num}_v{version}_text.json"
        layer_path.write_text(json.dumps(text_data))
        logger.info(
            "Saved text layer from working PDF for page %d v%d (%d blocks)",
            page_num, version, len(text_data.get("blocks", [])),
        )

    @staticmethod
    def _save_stale_text_layer(
        session_path: Path, page_num: int, version: int,
    ) -> None:
        """Mark the text layer as stale after visual edits."""
        layer_path = session_path / "edits" / f"page_{page_num}_v{version}_text.json"
        layer_path.write_text(json.dumps({
            "full_text": "", "blocks": [], "stale": True,
        }))

    # ------------------------------------------------------------------
    # Top-level entry point
    # ------------------------------------------------------------------

    async def execute_edit(
        self,
        session_id: str,
        page_num: int,
        instruction: str,
        on_progress: ProgressCallback,
    ) -> ExecutionResult:
        """Plan then execute. Replaces direct edit_engine call from Phase 1."""
        plan = await self.plan(session_id, page_num, instruction, on_progress)
        return await self.execute(
            session_id, page_num, plan, instruction, on_progress,
        )

    # ------------------------------------------------------------------
    # Edit history
    # ------------------------------------------------------------------

    @staticmethod
    def _save_edit_record(
        session_path: Path,
        page_num: int,
        version: int,
        prompt: str,
        text_layer_preserved: bool,
    ) -> None:
        """Append an entry to the page's edit history file."""
        history_path = session_path / "edits" / f"page_{page_num}_history.json"
        history: list[dict] = []
        if history_path.exists():
            history = json.loads(history_path.read_text())

        from datetime import datetime, timezone
        history.append({
            "version": version,
            "prompt": prompt,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "text_layer_preserved": text_layer_preserved,
        })
        history_path.write_text(json.dumps(history))
