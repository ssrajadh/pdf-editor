from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class TextBlock(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_name: str = ""
    font_size: float = 0.0


class FontInfo(BaseModel):
    """Font metadata for layout-aware planning."""

    name: str
    is_standard: bool
    is_cid: bool
    usage_count: int
    sample_text: str = ""


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int


class PageTextResponse(BaseModel):
    session_id: str
    page_number: int
    full_text: str
    blocks: list[TextBlock]


class SessionInfoResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int
    created_at: str
    current_page_versions: dict[int, int]


class SessionListItem(BaseModel):
    session_id: str
    filename: str
    page_count: int
    created_at: str
    last_edit_at: str
    total_edits: int


class SessionStatePage(BaseModel):
    page_num: int
    current_step: int
    total_steps: int
    image_url: str
    has_edits: bool
    edit_types: list[Literal["programmatic", "visual"]]


class SessionStateResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int
    current_page: int
    pages: list[SessionStatePage]
    conversations: dict[str, list[dict]]


class PageInfo(BaseModel):
    page_number: int
    width: float
    height: float


class EditRequest(BaseModel):
    session_id: str
    page_num: int
    prompt: str
    force_visual: bool = False


class EditResult(BaseModel):
    session_id: str
    page_num: int
    version: int
    processing_time_ms: float
    text_layer_preserved: bool


class EditProgress(BaseModel):
    stage: str
    message: str
    timestamp: datetime


class EditVersion(BaseModel):
    version: int
    prompt: str
    created_at: str
    text_layer_preserved: bool
    plan_summary: str = ""
    operations: list["OperationResult"] = []
    base_source: str = ""
    text_layer_source: str = ""
    working_pdf_modified: bool = False


class PlanPreviewRequest(BaseModel):
    prompt: str


class TextLayerResponse(BaseModel):
    session_id: str
    page_num: int
    version: int
    text_layer_preserved: bool
    full_text: str


class ExportResponse(BaseModel):
    session_id: str
    filename: str
    pages_modified: int


# ---------------------------------------------------------------------------
# Orchestrator execution plan models
# ---------------------------------------------------------------------------


class OperationType(str, Enum):
    TEXT_REPLACE = "text_replace"
    STYLE_CHANGE = "style_change"
    VISUAL_REGENERATE = "visual_regenerate"


class TextReplaceOp(BaseModel):
    """Swap specific text content in the PDF structure. Used when the replacement
    fits in the same space or is shorter. If the replacement is significantly longer
    and would break layout, the planner should use visual_regenerate instead."""

    type: Literal["text_replace"] = "text_replace"
    original_text: str = Field(description="Exact text to find in the PDF")
    replacement_text: str = Field(description="What to replace it with")
    context_before: str | None = Field(
        default=None,
        description="~20 chars of text immediately before original_text for disambiguation",
    )
    context_after: str | None = Field(
        default=None,
        description="~20 chars of text immediately after original_text for disambiguation",
    )
    match_strategy: Literal["exact", "contains", "first_occurrence"] = Field(
        description="How to locate the text on the page"
    )
    confidence: float = Field(ge=0, le=1, description="Planner's confidence this can be done programmatically (0-1)")
    reasoning: str = Field(description="Why this operation path was chosen")


class StyleChangeOp(BaseModel):
    """Modify visual properties of existing text without changing content.
    Font, size, color, bold/italic. Only for changes that don't affect layout."""

    type: Literal["style_change"] = "style_change"
    target_text: str = Field(description="Text element to modify")
    changes: dict = Field(description='Visual property changes, e.g. {"font_size": 24, "color": "#FF0000", "bold": true}')
    confidence: float = Field(ge=0, le=1, description="Planner's confidence this can be done programmatically (0-1)")
    reasoning: str = Field(description="Why this operation path was chosen")


class VisualRegenerateOp(BaseModel):
    """Send the page (or a region of it) to the image generation model.
    Used for changes that can't be done programmatically: layout changes,
    chart edits, image additions, complex visual redesigns, or text replacements
    that would break layout."""

    type: Literal["visual_regenerate"] = "visual_regenerate"
    prompt: str = Field(description="The instruction to send to the image model")
    region: str | None = Field(
        default=None,
        description='Target region: "full_page" or a description like "bottom_half", "chart_area"',
    )
    confidence: float = Field(ge=0, le=1, description="Planner's confidence this edit will produce the desired result (0-1)")
    reasoning: str = Field(description="Why this operation path was chosen")


class ExecutionPlan(BaseModel):
    """The planner's decomposition of a user's edit instruction."""

    operations: list[TextReplaceOp | StyleChangeOp | VisualRegenerateOp] = Field(
        description="List of operations to execute"
    )
    execution_order: list[int] = Field(
        description="Indices into operations list; programmatic ops first"
    )
    summary: str = Field(description="Human-readable description of the plan")
    all_programmatic: bool = Field(
        description="True if no visual_regenerate ops (fast path)"
    )
    page_analysis: str | None = Field(
        default=None,
        description="Page layout analysis summary (complexity, columns, fonts)",
    )


# ---------------------------------------------------------------------------
# Orchestrator execution result models
# ---------------------------------------------------------------------------


class RegenRiskAssessment(BaseModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    text_density: float
    text_block_count: int
    recommendation: str
    safe_to_proceed: bool
    override_available: bool


class OperationResult(BaseModel):
    """Result of executing a single operation from the plan."""

    op_index: int
    op_type: OperationType
    success: bool
    time_ms: int
    path: Literal["programmatic", "visual", "fallback_visual", "blocked"]
    detail: str
    error: str | None = None
    risk_assessment: RegenRiskAssessment | None = None


class TextReplaceResult(BaseModel):
    """Result of a programmatic text replacement in the PDF."""

    success: bool
    original_text: str
    new_text: str
    escalate: bool = False
    error_message: str | None = None
    time_ms: int = 0
    characters_changed: int = 0


class StyleChangeResult(BaseModel):
    """Result of a programmatic style change in the PDF."""

    success: bool
    target_text: str
    changes_applied: dict
    escalate: bool = False
    error_message: str | None = None
    time_ms: int = 0


class ExecutionResult(BaseModel):
    """Full result of executing an edit plan."""

    session_id: str
    page_num: int
    version: int
    plan_summary: str
    operations: list[OperationResult]
    total_time_ms: int
    programmatic_count: int
    visual_count: int
    blocked_count: int
    text_layer_source: Literal["original", "programmatic_edit", "mixed", "ocr"]


# ---------------------------------------------------------------------------
# State stack / history response models
# ---------------------------------------------------------------------------


class PageSnapshotResponse(BaseModel):
    """API response for a snapshot — excludes internal fields."""

    step: int
    timestamp: datetime
    prompt: str | None
    plan_summary: str | None
    operations_summary: list[OperationResult] | None
    image_url: str
    text_layer_source: str
    is_current: bool


class PageHistoryResponse(BaseModel):
    session_id: str
    page_num: int
    current_step: int
    total_steps: int
    snapshots: list[PageSnapshotResponse]


class RevertRequest(BaseModel):
    step: int


# Resolve forward references
EditVersion.model_rebuild()
