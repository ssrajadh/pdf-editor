"""Orchestrator — assembles page context, drives the planning LLM,
and coordinates execution of the resulting plan."""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Literal

from PIL import Image

from app.models.schemas import (
    ExecutionPlan,
    ExecutionResult,
    FontInfo,
    OperationResult,
    OperationType,
    RegenRiskAssessment,
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
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, dict | None], Awaitable[None]]


# ---------------------------------------------------------------------------
# In-memory visual description cache — survives across edits, invalidated
# only when a visual_regenerate operation changes the page appearance.
# ---------------------------------------------------------------------------

@dataclass
class CachedDescription:
    description: str
    generated_at_step: int


class VisualDescriptionCache:
    """Cache visual descriptions. Only invalidate when the page undergoes
    a visual_regenerate operation, not on programmatic edits."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], CachedDescription] = {}
        # key: (session_id, page_num)

    def get(self, session_id: str, page_num: int) -> str | None:
        key = (session_id, page_num)
        entry = self._cache.get(key)
        return entry.description if entry else None

    def set(self, session_id: str, page_num: int, description: str, step: int) -> None:
        self._cache[(session_id, page_num)] = CachedDescription(
            description=description, generated_at_step=step,
        )

    def invalidate(self, session_id: str, page_num: int) -> None:
        """Call this ONLY after a visual_regenerate operation completes."""
        self._cache.pop((session_id, page_num), None)


# ---------------------------------------------------------------------------
# Pre-planner heuristic: skip visual description for text-only instructions
# ---------------------------------------------------------------------------

_VISUAL_KEYWORDS = {
    "chart", "graph", "image", "photo", "logo", "icon", "picture",
    "background", "gradient", "border", "shadow", "watermark",
    "diagram", "illustration", "drawing", "figure", "table",
    "layout", "rearrange", "redesign", "move section", "swap",
    "add a", "insert a", "place a", "put a",
}

_TEXT_PATTERNS = [
    re.compile(r"change .+ to .+"),
    re.compile(r"replace .+ with .+"),
    re.compile(r"update .+ to .+"),
    re.compile(r"fix .+ to .+"),
    re.compile(r"make .+ bold"),
    re.compile(r"make .+ italic"),
    re.compile(r"change .+ color"),
]


def instruction_needs_visual_context(instruction: str) -> bool:
    """Fast heuristic (<1 ms): does this instruction reference visual elements?

    Returns True if visual context is needed, False if text-only context suffices.
    """
    instruction_lower = instruction.lower()

    for keyword in _VISUAL_KEYWORDS:
        if keyword in instruction_lower:
            return True

    # If it clearly looks like a text replacement, skip visual context
    for pattern in _TEXT_PATTERNS:
        if pattern.search(instruction_lower):
            return False

    # Ambiguous — fetch visual context to be safe
    return True


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
    layout_complexity: str = "simple"
    font_summary: list[FontInfo] = field(default_factory=list)
    has_cid_fonts: bool = False
    column_count: int = 1
    text_density: float = 0.0


# Standard base-14 font family roots for matching
_STANDARD_ROOTS = {
    "helvetica", "arial", "courier", "times", "symbol", "zapfdingbats",
    "calibri", "cambria", "georgia", "verdana", "tahoma", "trebuchet",
    "consolas", "lucida", "palatino", "garamond",
}


def _is_standard_font(font_name: str) -> bool:
    """Check if a font name maps cleanly to a standard base-14 family."""
    lower = font_name.lower()
    for prefix_part in lower.replace("+", "-").replace("_", "-").split("-"):
        if any(root in prefix_part for root in _STANDARD_ROOTS):
            return True
    return False


def analyze_layout_complexity(pdf_path: Path, page_num: int) -> dict:
    """Analyze page layout using PyMuPDF to determine complexity.

    Returns dict with: layout_complexity, font_summary, has_cid_fonts,
                       column_count, text_density
    """
    import fitz

    doc = fitz.open(str(pdf_path))
    page = doc[page_num - 1]

    # 1. Collect font info and text spans
    font_usage: dict[str, dict] = {}  # font_name -> {count, sample, is_cid}
    span_x_positions: list[float] = []
    total_text_area = 0.0

    blocks_data = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
    for block in blocks_data.get("blocks", []):
        if block.get("type") != 0:  # type 0 = text
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                font_name = span.get("font", "unknown")
                text = span.get("text", "").strip()
                if not text:
                    continue

                if font_name not in font_usage:
                    font_usage[font_name] = {
                        "count": 0,
                        "sample": "",
                        "is_cid": False,
                    }
                font_usage[font_name]["count"] += 1
                if len(font_usage[font_name]["sample"]) < 30:
                    font_usage[font_name]["sample"] += text[:30]

                bbox = span.get("bbox", (0, 0, 0, 0))
                span_x_positions.append(bbox[0])

                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                if w > 0 and h > 0:
                    total_text_area += w * h

    # Check which fonts are CID via the page font list
    page_fonts = page.get_fonts()
    cid_font_names = set()
    for font_tuple in page_fonts:
        ftype = font_tuple[2] if len(font_tuple) > 2 else ""
        fname = font_tuple[3] if len(font_tuple) > 3 else ""
        encoding = font_tuple[5] if len(font_tuple) > 5 else ""
        if ftype == "Type0" or "Identity" in str(encoding):
            clean_name = fname.split("+")[-1] if "+" in fname else fname
            cid_font_names.add(clean_name)

    for fname, info in font_usage.items():
        clean = fname.split("+")[-1] if "+" in fname else fname
        if clean in cid_font_names or any(c in fname for c in cid_font_names):
            info["is_cid"] = True

    has_cid = any(v["is_cid"] for v in font_usage.values())

    # 2. Build FontInfo list
    font_summary = []
    for fname, info in sorted(font_usage.items(), key=lambda x: -x[1]["count"]):
        font_summary.append(FontInfo(
            name=fname,
            is_standard=_is_standard_font(fname),
            is_cid=info["is_cid"],
            usage_count=info["count"],
            sample_text=info["sample"][:30],
        ))

    # 3. Estimate column count by clustering x-positions
    column_count = 1
    if span_x_positions:
        rounded = sorted(set(round(x / 20) * 20 for x in span_x_positions))
        if len(rounded) >= 2:
            gaps = [rounded[i + 1] - rounded[i] for i in range(len(rounded) - 1)]
            significant_gaps = sum(1 for g in gaps if g > 80)
            column_count = significant_gaps + 1

    # 4. Text density
    page_area = page.rect.width * page.rect.height
    text_density = total_text_area / page_area if page_area > 0 else 0

    # 5. Check for images
    image_count = len(page.get_images())

    doc.close()

    # 6. Complexity scoring
    score = 0
    if has_cid:
        score += 2
    if column_count > 1:
        score += column_count - 1
    if len(font_usage) > 3:
        score += 1
    if text_density > 0.6:
        score += 1
    if image_count > 0:
        score += 1

    if score <= 1:
        complexity = "simple"
    elif score <= 3:
        complexity = "moderate"
    else:
        complexity = "complex"

    logger.info(
        "Layout analysis page %d: complexity=%s (score=%d), columns=%d, "
        "fonts=%d (cid=%s), density=%.2f, images=%d",
        page_num, complexity, score, column_count,
        len(font_usage), has_cid, text_density, image_count,
    )

    return {
        "layout_complexity": complexity,
        "font_summary": font_summary,
        "has_cid_fonts": has_cid,
        "column_count": column_count,
        "text_density": round(text_density, 3),
    }


async def describe_visual_elements(
    image: Image.Image,
    text_content: str,
    provider: ModelProvider,
) -> str:
    """Send the page image to a vision model to describe non-text visual elements.

    Downscales the image to 800px on the long side to reduce input tokens
    (~75% reduction). Full resolution is only needed for edit_image calls.
    """
    # Downscale for description — 800px on the long side is plenty
    max_dim = 800
    if max(image.size) > max_dim:
        ratio = max_dim / max(image.size)
        new_size = (int(image.width * ratio), int(image.height * ratio))
        image_for_description = image.resize(new_size, Image.LANCZOS)
        logger.info(
            "Downscaled image for description: %dx%d → %dx%d",
            image.width, image.height, *new_size,
        )
    else:
        image_for_description = image

    prompt = VISUAL_DESCRIPTION_PROMPT
    if text_content:
        prompt += (
            "\n\nFor reference, here is the text already extracted from this page "
            "(do NOT repeat it):\n"
            f'"""\n{text_content[:2000]}\n"""'
        )

    description = await provider.analyze_image(image_for_description, prompt)
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
    *,
    skip_visual: bool = False,
    visual_cache: VisualDescriptionCache | None = None,
) -> PageContext:
    """Assemble all context the planner needs for a page.

    Args:
        skip_visual: When True, skip the analyze_image call entirely.
            Used for instructions that clearly target text-only content.
        visual_cache: In-memory cache that persists across programmatic
            edits and is only invalidated on visual_regenerate operations.
    """
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

    if skip_visual:
        visual_description = "Visual description skipped — text-only edit instruction."
        logger.info("Skipped visual description for page %d (text-only instruction)", page_num)
    else:
        # Check in-memory cache first (survives across programmatic edits)
        cached_desc = visual_cache.get(session_id, page_num) if visual_cache else None
        if cached_desc:
            visual_description = cached_desc
            logger.info("Using in-memory cached visual description for page %d", page_num)
        else:
            # Fall back to file-based cache
            cache_path = (
                session_path / "edits" / f"page_{page_num}_v{current_version}_vis_desc.txt"
            )
            if cache_path.exists():
                visual_description = cache_path.read_text()
                logger.info("Using file-cached visual description for page %d v%d", page_num, current_version)
            else:
                image = Image.open(image_path)
                visual_description = await describe_visual_elements(image, full_text, provider)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(visual_description)
            # Populate in-memory cache
            if visual_cache:
                visual_cache.set(session_id, page_num, visual_description, current_version)

    # Layout analysis (cached per page version — only changes on working PDF edits)
    layout_cache_path = (
        session_path / "edits" / f"page_{page_num}_v{current_version}_layout.json"
    )
    if layout_cache_path.exists():
        layout_info = json.loads(layout_cache_path.read_text())
        layout_info["font_summary"] = [
            FontInfo(**f) for f in layout_info["font_summary"]
        ]
        logger.info("Using cached layout analysis for page %d v%d", page_num, current_version)
    else:
        working_pdf = session_path / "working.pdf"
        analyze_pdf = working_pdf if working_pdf.exists() else pdf_path
        layout_info = await asyncio.to_thread(
            analyze_layout_complexity, analyze_pdf, page_num,
        )
        layout_cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_data = dict(layout_info)
        cache_data["font_summary"] = [f.model_dump() for f in cache_data["font_summary"]]
        layout_cache_path.write_text(json.dumps(cache_data))

    return PageContext(
        page_num=page_num,
        page_width=page_width,
        page_height=page_height,
        full_text=full_text,
        text_blocks=text_blocks,
        visual_description=visual_description,
        layout_complexity=layout_info["layout_complexity"],
        font_summary=layout_info["font_summary"],
        has_cid_fonts=layout_info["has_cid_fonts"],
        column_count=layout_info["column_count"],
        text_density=layout_info["text_density"],
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


def format_font_summary(fonts: list[FontInfo]) -> str:
    """Format font summary list for the planner prompt."""
    if not fonts:
        return "  (no fonts detected)"
    lines = []
    for f in fonts:
        flags = []
        if f.is_cid:
            flags.append("CID")
        if f.is_standard:
            flags.append("standard")
        else:
            flags.append("non-standard")
        flag_str = ", ".join(flags)
        lines.append(
            f"  - {f.name} [{flag_str}] — {f.usage_count} spans — "
            f'sample: "{f.sample_text}"'
        )
    return "\n".join(lines)


def _normalize_region(region: str | None) -> str:
    if not region:
        return "full_page"
    return region.strip().lower().replace(" ", "_")


def _resolve_region_bounds(
    region: str | None,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float, str]:
    """Resolve a region descriptor to page-space bounds (x0, y0, x1, y1)."""
    key = _normalize_region(region)

    full_keys = {"full_page", "full", "page", "entire_page", "whole_page"}
    if key in full_keys:
        return 0.0, 0.0, page_width, page_height, "full_page"

    if "header" in key or "title" in key:
        return 0.0, 0.0, page_width, page_height * 0.18, "header_area"

    if "footer" in key:
        return 0.0, page_height * 0.82, page_width, page_height, "footer_area"

    if "top" in key and "third" in key:
        return 0.0, 0.0, page_width, page_height / 3, "top_third"
    if "middle" in key and "third" in key:
        return 0.0, page_height / 3, page_width, page_height * 2 / 3, "middle_third"
    if "bottom" in key and "third" in key:
        return 0.0, page_height * 2 / 3, page_width, page_height, "bottom_third"

    if "top" in key and "half" in key:
        return 0.0, 0.0, page_width, page_height / 2, "top_half"
    if "bottom" in key and "half" in key:
        return 0.0, page_height / 2, page_width, page_height, "bottom_half"
    if "left" in key and "half" in key:
        return 0.0, 0.0, page_width / 2, page_height, "left_half"
    if "right" in key and "half" in key:
        return page_width / 2, 0.0, page_width, page_height, "right_half"

    if "left" in key and "third" in key:
        return 0.0, 0.0, page_width / 3, page_height, "left_third"
    if "center" in key and "third" in key:
        return page_width / 3, 0.0, page_width * 2 / 3, page_height, "center_third"
    if "right" in key and "third" in key:
        return page_width * 2 / 3, 0.0, page_width, page_height, "right_third"

    if "top" in key:
        return 0.0, 0.0, page_width, page_height / 3, "top_region"
    if "bottom" in key:
        return 0.0, page_height * 2 / 3, page_width, page_height, "bottom_region"
    if "left" in key:
        return 0.0, 0.0, page_width / 2, page_height, "left_region"
    if "right" in key:
        return page_width / 2, 0.0, page_width, page_height, "right_region"

    return 0.0, 0.0, page_width, page_height, "full_page"


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
# No-op progress callback for plan-only operations
# ---------------------------------------------------------------------------


async def _noop_progress(stage: str, message: str, extra: dict | None = None) -> None:
    """Silent progress callback used for plan preview."""
    pass


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
        state_manager: StateManager | None = None,
    ):
        self.provider = model_provider
        self.sessions = session_manager
        self.state_manager = state_manager or StateManager(session_manager)
        self.visual_cache = VisualDescriptionCache()

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

        needs_visual = instruction_needs_visual_context(instruction)
        logger.info(
            "Instruction visual heuristic: needs_visual=%s for %r",
            needs_visual, instruction[:80],
        )

        ctx = await build_page_context(
            session_id, page_num, self.provider, self.sessions,
            skip_visual=not needs_visual,
            visual_cache=self.visual_cache,
        )

        # Get conversation history for this page
        conversation = self.state_manager.get_conversation_context(
            session_id, page_num,
        )
        conversation_context = self._format_conversation_for_planner(conversation)

        text_blocks_json = page_context_to_text_blocks_json(ctx)
        font_summary_formatted = format_font_summary(ctx.font_summary)
        from app.prompts.orchestrator_plan import ORCHESTRATOR_USER_TEMPLATE

        user_content = ORCHESTRATOR_USER_TEMPLATE.format(
            user_instruction=instruction,
            page_text=ctx.full_text,
            text_blocks=text_blocks_json,
            page_width=ctx.page_width,
            page_height=ctx.page_height,
            visual_description=ctx.visual_description,
            layout_complexity=ctx.layout_complexity,
            column_count=ctx.column_count,
            has_cid_fonts=ctx.has_cid_fonts,
            text_density=ctx.text_density,
            font_summary_formatted=font_summary_formatted,
            conversation_context=conversation_context,
        )

        raw = await self.provider.plan_edit(
            system_prompt=ORCHESTRATOR_SYSTEM_PROMPT,
            user_message=user_content,
        )
        logger.info("Planner raw response (%d chars): %s", len(raw), raw[:200])

        plan = await self._parse_with_retry(raw, instruction)

        parts = [f"{ctx.layout_complexity} layout"]
        if ctx.column_count > 1:
            parts.append(f"{ctx.column_count} columns")
        if ctx.has_cid_fonts:
            parts.append("CID fonts detected")
        if ctx.text_density:
            parts.append(f"{ctx.text_density:.0%} text density")
        plan.page_analysis = "Page analysis: " + ", ".join(parts)

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

    async def plan_only(
        self,
        session_id: str,
        page_num: int,
        instruction: str,
    ) -> ExecutionPlan:
        """Plan without executing — used by the plan-preview endpoint."""
        return await self.plan(
            session_id, page_num, instruction, _noop_progress,
        )

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
    # Conversation context formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_conversation_for_planner(conversation: list[dict]) -> str:
        """Format conversation history for the planner prompt.

        Include the last 5 exchanges maximum (to keep context manageable).
        Each exchange shows the user's instruction and the operations that
        were executed with their results.
        """
        if not conversation:
            return "No previous edits on this page."

        # Pair up user/assistant messages
        exchanges: list[tuple[dict, dict | None]] = []
        i = 0
        while i < len(conversation):
            user_msg = conversation[i]
            assistant_msg = conversation[i + 1] if i + 1 < len(conversation) else None
            if user_msg.get("role") == "user":
                exchanges.append((user_msg, assistant_msg))
                i += 2
            else:
                i += 1

        # Take last 5
        exchanges = exchanges[-5:]

        lines = []
        for step_num, (user_msg, asst_msg) in enumerate(exchanges, start=1):
            user_text = user_msg.get("content", "")
            lines.append(f"  {step_num}. User: \"{user_text}\"")

            if asst_msg:
                ops = asst_msg.get("operations", [])
                if ops:
                    for op in ops:
                        op_type = op.get("op_type", op.get("type", "unknown"))
                        path = op.get("path", "unknown")
                        success = op.get("success", False)
                        detail = op.get("detail", "")
                        time_ms = op.get("time_ms", 0)
                        status = "success" if success else "failed"
                        lines.append(
                            f"     → {op_type}: {detail} "
                            f"({path}, {time_ms}ms, {status})"
                        )
                else:
                    summary = asst_msg.get("content", "")
                    if summary:
                        lines.append(f"     → {summary}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Base image selection — compound degradation prevention
    # ------------------------------------------------------------------

    def _get_visual_edit_base_image(
        self,
        session_id: str,
        page_num: int,
        programmatic_ops_applied: bool,
    ) -> tuple[Image.Image, Literal["original_pdf", "working_pdf"]]:
        """Determines the correct base image for a visual regeneration operation.

        Rules:
        1. If programmatic edits were applied earlier in THIS plan:
           → Render from working PDF (includes the text changes).
           The visual model sees "Q4" not "Q3" if we already swapped it.

        2. If programmatic edits were applied in PREVIOUS plans (earlier in
           the session) but not in the current plan:
           → Still render from working PDF (it accumulates all programmatic edits).

        3. NEVER use a previously AI-generated image as the base.
           The chain is always:
             original.pdf → (programmatic edits) → working.pdf → render → model
           NOT:
             previous_model_output.png → model again

        4. If the user makes a second visual edit to the same page (e.g., first
           changed the chart, now wants to change the background):
           The base is STILL the working PDF render, not the previous visual
           output. This means the first visual edit's changes are lost — the
           second visual edit starts fresh from the PDF state.

           This is a deliberate tradeoff: we lose visual edit stacking but
           prevent compound degradation.

        5. Exception for Phase 3 (future): conversational refinement of a
           visual edit ("make it more blue") will need the previous output.
        """
        session_path = self.sessions.get_session_path(session_id)
        working_pdf = session_path / "working.pdf"

        if working_pdf.exists():
            source: Literal["original_pdf", "working_pdf"] = "working_pdf"
            image = pdf_service.render_page_to_image(working_pdf, page_num)
            logger.info(
                "Visual base image for page %d: rendered from working.pdf "
                "(programmatic_in_plan=%s, size=%dx%d)",
                page_num, programmatic_ops_applied,
                image.size[0], image.size[1],
            )
        else:
            source = "original_pdf"
            image = pdf_service.render_page_to_image(
                session_path / "original.pdf", page_num,
            )
            logger.info(
                "Visual base image for page %d: rendered from original.pdf "
                "(no working.pdf exists, size=%dx%d)",
                page_num, image.size[0], image.size[1],
            )

        return image, source

    # ------------------------------------------------------------------
    # Visual regeneration safety
    # ------------------------------------------------------------------

    def _assess_visual_regen_risk(
        self,
        session_id: str,
        page_num: int,
        operation: VisualRegenerateOp,
    ) -> RegenRiskAssessment:
        """Analyze the risk of visual regeneration on this page/region."""
        import pdfplumber

        session_path = self.sessions.get_session_path(session_id)
        working_pdf = session_path / "working.pdf"
        pdf_path = working_pdf if working_pdf.exists() else session_path / "original.pdf"

        with pdfplumber.open(pdf_path) as pdf:
            page = pdf.pages[page_num - 1]
            page_width = float(page.width)
            page_height = float(page.height)

            x0, y0, x1, y1, region_label = _resolve_region_bounds(
                operation.region, page_width, page_height,
            )
            region_area = max(0.0, (x1 - x0) * (y1 - y0))

            words = page.extract_words() or []

        text_area = 0.0
        text_block_count = 0

        for word in words:
            wx0 = float(word.get("x0", 0))
            wx1 = float(word.get("x1", 0))
            wy0 = float(word.get("top", 0))
            wy1 = float(word.get("bottom", 0))

            ix0 = max(x0, wx0)
            iy0 = max(y0, wy0)
            ix1 = min(x1, wx1)
            iy1 = min(y1, wy1)

            if ix1 <= ix0 or iy1 <= iy0:
                continue

            text_block_count += 1
            text_area += (ix1 - ix0) * (iy1 - iy0)

        text_density = text_area / region_area if region_area > 0 else 0.0

        if text_density > 0.60:
            risk_level: Literal["low", "medium", "high", "critical"] = "critical"
        elif text_density >= 0.35:
            risk_level = "high"
        elif text_density >= 0.15:
            risk_level = "medium"
        else:
            risk_level = "low"

        if text_block_count >= 50 and risk_level in ("low", "medium"):
            risk_level = "high"

        if risk_level == "low":
            recommendation = "Low text density — visual regeneration is likely safe."
            safe_to_proceed = True
            override_available = False
        elif risk_level == "medium":
            recommendation = "Mixed content — minor text artifacts are possible."
            safe_to_proceed = True
            override_available = True
        elif risk_level == "high":
            recommendation = (
                "Text-heavy region — visual regeneration is likely to degrade text. "
                "Prefer text_replace or style_change, or use override if necessary."
            )
            safe_to_proceed = False
            override_available = True
        else:
            recommendation = (
                "Critical text density — visual regeneration would destroy text. "
                "Use programmatic edits instead."
            )
            safe_to_proceed = False
            override_available = False

        logger.info(
            "Visual regen risk page %d (%s): density=%.2f, blocks=%d, level=%s",
            page_num, region_label, text_density, text_block_count, risk_level,
        )

        return RegenRiskAssessment(
            risk_level=risk_level,
            text_density=round(text_density, 3),
            text_block_count=text_block_count,
            recommendation=recommendation,
            safe_to_proceed=safe_to_proceed,
            override_available=override_available,
        )

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
        force_visual: bool = False,
    ) -> ExecutionResult:
        """Execute a plan's operations in order.

        Text_replace ops on the same page are batched into a single
        apply_text_replacements_batch() call for atomicity and performance.
        Visual ops always get a fresh PDF-rendered base image (never an
        AI-generated one) via _get_visual_edit_base_image().
        """
        t_start = time.monotonic()
        session_path = self.sessions.get_session_path(session_id)
        op_results: list[OperationResult] = []

        programmatic_ran = False
        visual_ran = False
        working_pdf_modified = False
        base_source = ""

        # Collect text_replace ops for batching (they run first per execution_order)
        text_replace_batch: list[tuple[int, TextReplaceOp]] = []  # (op_idx, op)
        remaining_ops: list[tuple[int, TextReplaceOp | StyleChangeOp | VisualRegenerateOp]] = []

        for op_idx in plan.execution_order:
            if op_idx < 0 or op_idx >= len(plan.operations):
                logger.warning("Skipping invalid execution_order index %d", op_idx)
                continue
            op = plan.operations[op_idx]
            if isinstance(op, TextReplaceOp) and op.confidence >= 0.5:
                text_replace_batch.append((op_idx, op))
            else:
                remaining_ops.append((op_idx, op))

        # Execute text_replace batch
        if text_replace_batch:
            batch_results = await self._execute_text_replace_batch(
                text_replace_batch,
                session_id,
                page_num,
                instruction,
                on_progress,
                force_visual,
            )
            for result in batch_results:
                if result.path == "programmatic" and result.success:
                    programmatic_ran = True
                    working_pdf_modified = True
                elif result.path == "fallback_visual" and result.success:
                    visual_ran = True
                op_results.append(result)

        # Execute remaining ops (style_change, visual_regenerate, low-conf text_replace)
        for op_idx, op in remaining_ops:
            t_op = time.monotonic()

            if isinstance(op, (TextReplaceOp, StyleChangeOp)):
                result = await self._execute_programmatic(
                    op,
                    op_idx,
                    session_id,
                    page_num,
                    instruction,
                    on_progress,
                    force_visual,
                )
                if result.path == "programmatic" and result.success:
                    programmatic_ran = True
                    working_pdf_modified = True
                elif result.path == "fallback_visual" and result.success:
                    visual_ran = True

            elif isinstance(op, VisualRegenerateOp):
                if not force_visual:
                    risk = self._assess_visual_regen_risk(session_id, page_num, op)
                    if risk.risk_level in ("critical", "high"):
                        await on_progress(
                            "blocked",
                            (
                                f"Visual edit blocked: this page is {risk.text_density:.0%} text. "
                                "AI regeneration would degrade the text content. "
                                "Try rephrasing as a text or style change instead."
                            ),
                            {"op_index": op_idx},
                        )
                        result = OperationResult(
                            op_index=op_idx,
                            op_type=OperationType.VISUAL_REGENERATE,
                            success=False,
                            time_ms=0,
                            path="blocked",
                            detail=(
                                f"Blocked: text density {risk.text_density:.0%} "
                                f"({risk.risk_level} risk)"
                            ),
                            error=risk.recommendation,
                            risk_assessment=risk,
                        )
                        op_results.append(result)
                        continue

                    if risk.risk_level == "medium":
                        await on_progress(
                            "caution",
                            (
                                f"Note: this region contains some text ({risk.text_density:.0%}). "
                                "Minor text artifacts are possible."
                            ),
                            {"op_index": op_idx},
                        )
                else:
                    await on_progress(
                        "warning",
                        "Override enabled — proceeding with visual regeneration without text safety checks.",
                        {"op_index": op_idx},
                    )

                metadata = self.sessions.get_metadata(session_id)
                cur_v = int(
                    metadata.get("current_page_versions", {}).get(str(page_num), 0)
                )
                result = await self._execute_visual(
                    op, op_idx, session_id, page_num, cur_v + 1,
                    on_progress, programmatic_ran,
                    override_applied=force_visual,
                )
                if result.success:
                    visual_ran = True
            else:
                continue

            result.time_ms = int((time.monotonic() - t_op) * 1000)
            op_results.append(result)

        # If nothing succeeded, full-page visual fallback (unless blocked for safety)
        has_blocked = any(r.path == "blocked" for r in op_results)
        if (not op_results or not any(r.success for r in op_results)) and not has_blocked:
            logger.warning("No operations succeeded, running full-page visual fallback")
            await on_progress("generating", "Falling back to full-page AI edit...", None)
            metadata = self.sessions.get_metadata(session_id)
            cur_v = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            fallback_op = VisualRegenerateOp(
                prompt=instruction, region="full_page", confidence=0.7,
                reasoning="Full fallback — no operations succeeded.",
            )
            if not force_visual:
                risk = self._assess_visual_regen_risk(session_id, page_num, fallback_op)
                if risk.risk_level in ("critical", "high"):
                    await on_progress(
                        "blocked",
                        (
                            f"Visual edit blocked: this page is {risk.text_density:.0%} text. "
                            "AI regeneration would degrade the text content. "
                            "Try rephrasing as a text or style change instead."
                        ),
                        None,
                    )
                    op_results.append(OperationResult(
                        op_index=-1,
                        op_type=OperationType.VISUAL_REGENERATE,
                        success=False,
                        time_ms=0,
                        path="blocked",
                        detail=(
                            f"Blocked: text density {risk.text_density:.0%} "
                            f"({risk.risk_level} risk)"
                        ),
                        error=risk.recommendation,
                        risk_assessment=risk,
                    ))
                else:
                    fallback_result = await self._execute_visual(
                        fallback_op,
                        op_idx=-1, session_id=session_id, page_num=page_num,
                        version=cur_v + 1, on_progress=on_progress,
                        programmatic_preceded=False,
                        override_applied=False,
                    )
                    fallback_result.time_ms = int((time.monotonic() - t_start) * 1000)
                    op_results.append(fallback_result)
                    visual_ran = True
            else:
                fallback_result = await self._execute_visual(
                    fallback_op,
                    op_idx=-1, session_id=session_id, page_num=page_num,
                    version=cur_v + 1, on_progress=on_progress,
                    programmatic_preceded=False,
                    override_applied=True,
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
            text_layer_source = "programmatic_edit"
            await self._save_text_layer_from_working_pdf(
                session_id, page_num, final_version,
            )
        elif programmatic_ran and visual_ran:
            text_layer_source = "mixed"
            self._save_stale_text_layer(session_path, page_num, final_version)
        elif visual_ran:
            text_layer_source = "ocr"
            self._save_stale_text_layer(session_path, page_num, final_version)
        else:
            text_layer_source = "original"

        # Determine base_source for this edit
        if visual_ran:
            working_pdf = session_path / "working.pdf"
            base_source = "working_pdf" if working_pdf.exists() else "original_pdf"
        elif programmatic_ran:
            base_source = "working_pdf"
        else:
            base_source = "original_pdf"

        # Save rich edit record
        self._save_edit_record(
            session_path=session_path,
            page_num=page_num,
            version=final_version,
            prompt=instruction,
            plan_summary=plan.summary,
            operations=op_results,
            base_source=base_source,
            text_layer_source=text_layer_source,
            working_pdf_modified=working_pdf_modified,
        )

        total_ms = int((time.monotonic() - t_start) * 1000)
        prog_count = sum(1 for r in op_results if r.path == "programmatic")
        vis_count = sum(
            1 for r in op_results if r.path in ("visual", "fallback_visual")
        )
        blocked_count = sum(1 for r in op_results if r.path == "blocked")

        return ExecutionResult(
            session_id=session_id,
            page_num=page_num,
            version=final_version,
            plan_summary=plan.summary,
            operations=op_results,
            total_time_ms=total_ms,
            programmatic_count=prog_count,
            visual_count=vis_count,
            blocked_count=blocked_count,
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
        force_visual: bool = False,
    ) -> OperationResult:
        """Execute a programmatic op via PdfEditor. Falls back to visual on failure."""
        from app.services.pdf_editor import PdfEditor
        editor = PdfEditor(session_manager=self.sessions)

        if isinstance(op, TextReplaceOp):
            desc = f"'{op.original_text}' -> '{op.replacement_text}'"
            await on_progress(
                "programmatic", f"Text replacement: {desc}", {"op_index": op_idx},
            )

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

            if getattr(op, "reflow_line", False):
                result = await asyncio.to_thread(
                    editor.apply_text_replace_with_reflow,
                    session_id, page_num,
                    op.original_text, op.replacement_text, op.match_strategy,
                    op.context_before, op.context_after,
                )
            else:
                result = await asyncio.to_thread(
                    editor.apply_text_replace,
                    session_id, page_num,
                    op.original_text, op.replacement_text, op.match_strategy,
                    op.context_before, op.context_after,
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
                    detail=f"Text replaced: {desc}",
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
                    op, op_idx, session_id, page_num, on_progress, force_visual,
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
                    op, op_idx, session_id, page_num, on_progress, force_visual,
                )

            return OperationResult(
                op_index=op_idx, op_type=OperationType.STYLE_CHANGE,
                success=False, time_ms=result.time_ms, path="programmatic",
                detail=f"Style change failed: {result.error_message}",
                error=result.error_message,
            )

    # ------------------------------------------------------------------
    # Batched text_replace execution
    # ------------------------------------------------------------------

    async def _execute_text_replace_batch(
        self,
        ops: list[tuple[int, TextReplaceOp]],
        session_id: str,
        page_num: int,
        instruction: str,
        on_progress: ProgressCallback,
        force_visual: bool = False,
    ) -> list[OperationResult]:
        """Execute multiple text_replace ops as a single batch."""
        from app.services.pdf_editor import PdfEditor
        editor = PdfEditor(session_manager=self.sessions)

        descs = [f"'{op.original_text}' -> '{op.replacement_text}'" for _, op in ops]
        await on_progress(
            "programmatic",
            f"Batch text replacement: {len(ops)} operations",
            {"op_count": len(ops)},
        )

        batch_ops = [op for _, op in ops]
        batch_results = await asyncio.to_thread(
            editor.apply_text_replacements_batch,
            session_id, page_num, batch_ops,
        )

        results: list[OperationResult] = []
        for i, (op_idx, op) in enumerate(ops):
            if i < len(batch_results):
                br = batch_results[i]
            else:
                br = None

            if br and br.success:
                desc = descs[i]
                await on_progress(
                    "programmatic",
                    f"Text replaced: {desc} ({br.time_ms}ms)",
                    {"op_index": op_idx},
                )
                results.append(OperationResult(
                    op_index=op_idx,
                    op_type=OperationType.TEXT_REPLACE,
                    success=True, time_ms=br.time_ms, path="programmatic",
                    detail=f"Text replaced: {desc} ({br.characters_changed} chars changed)",
                ))
            elif br and br.escalate:
                logger.warning(
                    "Batch text_replace failed for op %d, escalating: %s",
                    op_idx, br.error_message,
                )
                await on_progress(
                    "generating",
                    f"Text replacement failed ({br.error_message}), "
                    f"falling back to AI visual edit",
                    {"op_index": op_idx},
                )
                fallback = await self._visual_fallback_for_programmatic(
                    op, op_idx, session_id, page_num, on_progress, force_visual,
                )
                results.append(fallback)
            else:
                error_msg = br.error_message if br else "Batch processing error"
                results.append(OperationResult(
                    op_index=op_idx,
                    op_type=OperationType.TEXT_REPLACE,
                    success=False, time_ms=br.time_ms if br else 0,
                    path="programmatic",
                    detail=f"Text replace failed: {error_msg}",
                    error=error_msg,
                ))

        return results

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
        override_applied: bool = False,
    ) -> OperationResult:
        """Execute a visual_regenerate operation.

        Uses _get_visual_edit_base_image() to ensure we NEVER use a
        previously AI-generated image as the base. This prevents compound
        quality degradation across multiple visual edits.
        """
        prompt_preview = op.prompt[:80] + ("..." if len(op.prompt) > 80 else "")
        await on_progress(
            "generating", f"AI editing: {prompt_preview}", {"op_index": op_idx},
        )

        try:
            base_image, base_source = await asyncio.to_thread(
                self._get_visual_edit_base_image,
                session_id, page_num, programmatic_preceded,
            )
            logger.info(
                "Visual op %d: base from %s (%dx%d)",
                op_idx, base_source, base_image.size[0], base_image.size[1],
            )

            result_image = await self.provider.edit_image(base_image, op.prompt)

            session_path = self.sessions.get_session_path(session_id)
            new_path = session_path / "pages" / f"page_{page_num}_v{version}.png"
            await asyncio.to_thread(result_image.save, new_path, "PNG")
            logger.info("Visual op %d saved: %s", op_idx, new_path.name)

            metadata = self.sessions.get_metadata(session_id)
            metadata["current_page_versions"][str(page_num)] = version
            self.sessions.update_metadata(session_id, metadata)

            # Invalidate in-memory visual description cache — the page
            # appearance has changed, so the next edit needs a fresh description.
            self.visual_cache.invalidate(session_id, page_num)
            logger.info("Invalidated visual description cache for %s page %d", session_id, page_num)

            detail_prefix = ""
            if override_applied:
                detail_prefix = "⚠️ Visual edit applied with override — review for text artifacts. "

            return OperationResult(
                op_index=op_idx,
                op_type=OperationType.VISUAL_REGENERATE,
                success=True, time_ms=0, path="visual",
                detail=(
                    f"{detail_prefix}Visual regenerate ({op.region or 'full_page'}): {op.prompt[:100]}"
                ),
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
        force_visual: bool = False,
    ) -> OperationResult:
        """Fall back to visual editing when a programmatic op fails.

        Uses _get_visual_edit_base_image() to avoid compound degradation.
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

        visual_op = VisualRegenerateOp(
            prompt=visual_prompt,
            region="full_page",
            confidence=0.4,
            reasoning="Programmatic edit failed; visual fallback required.",
        )

        if not force_visual:
            risk = self._assess_visual_regen_risk(session_id, page_num, visual_op)
            if risk.risk_level in ("critical", "high"):
                await on_progress(
                    "blocked",
                    (
                        f"Visual edit blocked: this page is {risk.text_density:.0%} text. "
                        "AI regeneration would degrade the text content. "
                        "Try rephrasing as a text or style change instead."
                    ),
                    {"op_index": op_idx},
                )
                return OperationResult(
                    op_index=op_idx,
                    op_type=OperationType(op.type),
                    success=False,
                    time_ms=0,
                    path="blocked",
                    detail=(
                        f"Blocked visual fallback: text density {risk.text_density:.0%} "
                        f"({risk.risk_level} risk)"
                    ),
                    error=risk.recommendation,
                    risk_assessment=risk,
                )

            if risk.risk_level == "medium":
                await on_progress(
                    "caution",
                    (
                        f"Note: this region contains some text ({risk.text_density:.0%}). "
                        "Minor text artifacts are possible."
                    ),
                    {"op_index": op_idx},
                )
        else:
            await on_progress(
                "warning",
                "Override enabled — proceeding with visual regeneration without text safety checks.",
                {"op_index": op_idx},
            )

        try:
            base_image, base_source = await asyncio.to_thread(
                self._get_visual_edit_base_image,
                session_id, page_num, False,
            )

            result_image = await self.provider.edit_image(base_image, visual_prompt)

            session_path = self.sessions.get_session_path(session_id)
            metadata = self.sessions.get_metadata(session_id)
            current_version = int(
                metadata.get("current_page_versions", {}).get(str(page_num), 0)
            )
            new_version = current_version + 1

            new_path = session_path / "pages" / f"page_{page_num}_v{new_version}.png"
            await asyncio.to_thread(result_image.save, new_path, "PNG")

            metadata["current_page_versions"][str(page_num)] = new_version
            self.sessions.update_metadata(session_id, metadata)

            detail_prefix = ""
            if force_visual:
                detail_prefix = "⚠️ Visual edit applied with override — review for text artifacts. "

            return OperationResult(
                op_index=op_idx,
                op_type=OperationType(op.type),
                success=True, time_ms=0, path="fallback_visual",
                detail=(
                    f"{detail_prefix}Programmatic {op.type} failed, visual fallback succeeded"
                ),
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
        force_visual: bool = False,
    ) -> ExecutionResult:
        """Plan then execute, and store conversation context in the state stack."""
        from datetime import datetime, timezone as _tz

        plan = await self.plan(session_id, page_num, instruction, on_progress)
        result = await self.execute(
            session_id, page_num, plan, instruction, on_progress, force_visual,
        )

        # Build conversation messages for this edit
        now = datetime.now(_tz.utc).isoformat()
        user_message = {
            "role": "user",
            "content": instruction,
            "timestamp": now,
        }
        assistant_message = {
            "role": "assistant",
            "content": result.plan_summary,
            "operations": [op.model_dump() for op in result.operations],
            "timestamp": now,
        }

        # Append to the running conversation for this page
        current_conversation = list(
            self.state_manager.get_conversation_context(session_id, page_num)
        )
        current_conversation.extend([user_message, assistant_message])

        # Get the image path and text layer for the snapshot
        session_path = self.sessions.get_session_path(session_id)
        image_path = str(
            pdf_service.get_page_image_path(
                session_path, page_num, version=str(result.version)
            )
        )

        text_layer = None
        if result.text_layer_source in ("programmatic_edit", "original"):
            working_pdf = session_path / "working.pdf"
            pdf_for_text = (
                working_pdf if working_pdf.exists()
                else session_path / "original.pdf"
            )
            try:
                text_data = pdf_service.extract_text(pdf_for_text, page_num)
                text_layer = [
                    TextBlock(**b) for b in text_data["blocks"]
                ]
            except Exception:
                pass

        # Push snapshot to the state stack
        self.state_manager.snapshot_after_edit(
            session_id=session_id,
            page_num=page_num,
            prompt=instruction,
            plan_summary=result.plan_summary,
            result=result,
            image_path=image_path,
            text_layer=text_layer,
            text_layer_source=result.text_layer_source,
            conversation_messages=current_conversation,
        )

        stack = self.state_manager.get_stack(session_id, page_num)
        result.step = stack.current_step

        try:
            metadata = self.sessions.get_metadata(session_id)
            if any(op.success for op in result.operations):
                metadata["last_edit_at"] = now
                metadata["total_edits"] = int(metadata.get("total_edits", 0)) + 1
            metadata["last_active_page"] = page_num
            metadata["last_active_at"] = now
            self.sessions.update_metadata(session_id, metadata)
        except Exception:
            pass

        return result

    # ------------------------------------------------------------------
    # Edit history
    # ------------------------------------------------------------------

    @staticmethod
    def _save_edit_record(
        session_path: Path,
        page_num: int,
        version: int,
        prompt: str,
        plan_summary: str,
        operations: list[OperationResult],
        base_source: str,
        text_layer_source: str,
        working_pdf_modified: bool,
    ) -> None:
        """Append a rich entry to the page's edit history file."""
        history_path = session_path / "edits" / f"page_{page_num}_history.json"
        history: list[dict] = []
        if history_path.exists():
            history = json.loads(history_path.read_text())

        text_layer_preserved = text_layer_source in ("programmatic_edit", "original")

        from datetime import datetime, timezone
        history.append({
            "version": version,
            "prompt": prompt,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "text_layer_preserved": text_layer_preserved,
            "plan_summary": plan_summary,
            "operations": [op.model_dump() for op in operations],
            "base_source": base_source,
            "text_layer_source": text_layer_source,
            "working_pdf_modified": working_pdf_modified,
        })
        history_path.write_text(json.dumps(history))
