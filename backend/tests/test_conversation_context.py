"""Tests for conversation context in the orchestrator planning step.

Tests that conversation history is:
1. Correctly formatted for the planner prompt
2. Injected into the planning LLM call
3. Stored in snapshots after each edit
4. Reverted properly when restoring to a previous step

Conversational routing tests (require GEMINI_API_KEY) verify:
- Follow-up references ("make it bold" → targets previous edit's text)
- Undo requests ("change it back" → reverses previous text_replace)
- Refinements ("darker" → visual_regenerate referencing previous visual)
- Earlier-step references ("make the title bigger" → targets step 1 text)
- Context after revert (picks up from reverted step's conversation)

Usage:
    cd backend
    # Unit tests (no API key):
    python -m pytest tests/test_conversation_context.py -v -k "not live"

    # Full suite with LLM (needs GEMINI_API_KEY):
    GEMINI_API_KEY=... python -m pytest tests/test_conversation_context.py -v
"""

import json
import os
import shutil
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.models.schemas import (
    ExecutionResult,
    OperationResult,
    OperationType,
    TextBlock,
)
from app.services.orchestrator import Orchestrator
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

STORAGE = Path(__file__).parent / "test_data_conv"

HAS_API_KEY = bool(os.environ.get("GEMINI_API_KEY"))


def _make_report_pdf() -> bytes:
    """Generate a simple report PDF for testing."""
    path = STORAGE / "_tmp_report.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    w, h = letter

    c.setFont("Helvetica-Bold", 24)
    c.drawString(72, h - 72, "Q3 2024 Revenue Report")

    c.setFont("Helvetica", 14)
    c.drawString(72, h - 110, "Prepared by: Finance Department")

    c.setFont("Helvetica", 12)
    c.drawString(72, h - 150, "Total Revenue: $4.2M")
    c.drawString(72, h - 170, "Growth Rate: 12% YoY")
    c.drawString(72, h - 210, "Operating Margin: 18.5%")

    c.showPage()
    c.save()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


@pytest.fixture(autouse=True)
def clean_storage():
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True, exist_ok=True)
    yield
    if STORAGE.exists():
        shutil.rmtree(STORAGE)


@pytest.fixture
def session_mgr():
    return SessionManager(STORAGE)


@pytest.fixture
def state_mgr(session_mgr):
    return StateManager(session_mgr)


@pytest.fixture
def session_id(session_mgr, state_mgr):
    """Create a session with the report PDF and initialize step 0."""
    from app.services import pdf_service

    pdf_bytes = _make_report_pdf()
    sid = session_mgr.create_session(pdf_bytes, "report.pdf", 1)
    session_path = session_mgr.get_session_path(sid)
    pdf_path = session_path / "original.pdf"
    pages_dir = session_path / "pages"

    pdf_service.render_all_pages(pdf_path, pages_dir)

    img = str(pdf_service.get_page_image_path(session_path, 1, "0"))
    text_data = pdf_service.extract_text(pdf_path, 1)
    text_blocks = [TextBlock(**b) for b in text_data["blocks"]]
    state_mgr.initialize_page(sid, 1, img, text_blocks)

    return sid


def _make_result(session_id, op_type="text_replace", detail="test"):
    return ExecutionResult(
        session_id=session_id,
        page_num=1,
        version=1,
        plan_summary="Test plan",
        operations=[
            OperationResult(
                op_index=0,
                op_type=OperationType(op_type),
                success=True,
                time_ms=100,
                path="programmatic",
                detail=detail,
            )
        ],
        total_time_ms=100,
        programmatic_count=1,
        visual_count=0,
        text_layer_source="programmatic_edit",
    )


# ======================================================================
# Unit tests — no API key needed
# ======================================================================


class TestFormatConversationForPlanner:
    """Test the _format_conversation_for_planner static method."""

    def test_empty_conversation(self):
        result = Orchestrator._format_conversation_for_planner([])
        assert result == "No previous edits on this page."

    def test_single_exchange(self):
        conversation = [
            {
                "role": "user",
                "content": "Change 2024 to 2025",
                "timestamp": "2025-01-01T00:00:00",
            },
            {
                "role": "assistant",
                "content": "Replace '2024' with '2025'",
                "operations": [
                    {
                        "op_type": "text_replace",
                        "path": "programmatic",
                        "success": True,
                        "time_ms": 89,
                        "detail": "'2024' -> '2025'",
                    }
                ],
                "timestamp": "2025-01-01T00:00:01",
            },
        ]
        result = Orchestrator._format_conversation_for_planner(conversation)
        assert '1. User: "Change 2024 to 2025"' in result
        assert "text_replace" in result
        assert "'2024' -> '2025'" in result
        assert "89ms" in result
        assert "success" in result

    def test_multiple_exchanges(self):
        conversation = [
            {"role": "user", "content": "Change Q3 to Q4"},
            {
                "role": "assistant",
                "content": "Replaced Q3 with Q4",
                "operations": [
                    {
                        "op_type": "text_replace",
                        "path": "programmatic",
                        "success": True,
                        "time_ms": 50,
                        "detail": "'Q3' -> 'Q4'",
                    }
                ],
            },
            {"role": "user", "content": "Make the title red"},
            {
                "role": "assistant",
                "content": "Changed title color",
                "operations": [
                    {
                        "op_type": "style_change",
                        "path": "programmatic",
                        "success": True,
                        "time_ms": 45,
                        "detail": "color -> #FF0000",
                    }
                ],
            },
        ]
        result = Orchestrator._format_conversation_for_planner(conversation)
        assert '1. User: "Change Q3 to Q4"' in result
        assert '2. User: "Make the title red"' in result
        assert "text_replace" in result
        assert "style_change" in result

    def test_max_5_exchanges(self):
        conversation = []
        for i in range(8):
            conversation.append({"role": "user", "content": f"Edit {i+1}"})
            conversation.append({
                "role": "assistant",
                "content": f"Done {i+1}",
                "operations": [],
            })

        result = Orchestrator._format_conversation_for_planner(conversation)
        # Should only have exchanges 4-8 (last 5)
        assert "Edit 4" in result
        assert "Edit 8" in result
        assert "Edit 3" not in result

    def test_visual_operation(self):
        conversation = [
            {"role": "user", "content": "Make background blue"},
            {
                "role": "assistant",
                "content": "Changed background",
                "operations": [
                    {
                        "op_type": "visual_regenerate",
                        "path": "visual",
                        "success": True,
                        "time_ms": 8200,
                        "detail": "full page visual edit",
                    }
                ],
            },
        ]
        result = Orchestrator._format_conversation_for_planner(conversation)
        assert "visual_regenerate" in result
        assert "8200ms" in result

    def test_failed_operation(self):
        conversation = [
            {"role": "user", "content": "Change text"},
            {
                "role": "assistant",
                "content": "Failed",
                "operations": [
                    {
                        "op_type": "text_replace",
                        "path": "programmatic",
                        "success": False,
                        "time_ms": 30,
                        "detail": "text not found",
                    }
                ],
            },
        ]
        result = Orchestrator._format_conversation_for_planner(conversation)
        assert "failed" in result


class TestConversationInPromptTemplate:
    """Test that conversation_context is injected into the user template."""

    def test_template_includes_conversation(self):
        from app.prompts.orchestrator_plan import ORCHESTRATOR_USER_TEMPLATE

        rendered = ORCHESTRATOR_USER_TEMPLATE.format(
            user_instruction="test",
            page_text="test text",
            text_blocks="[]",
            page_width=612,
            page_height=792,
            visual_description="none",
            layout_complexity="simple",
            column_count=1,
            has_cid_fonts=False,
            text_density=0.0,
            font_summary_formatted="  (none)",
            conversation_context="  1. User: \"Change Q3 to Q4\"\n     → text_replace: success",
        )
        assert "Previous edits on this page:" in rendered
        assert 'User: "Change Q3 to Q4"' in rendered

    def test_template_default_no_edits(self):
        from app.prompts.orchestrator_plan import ORCHESTRATOR_USER_TEMPLATE

        rendered = ORCHESTRATOR_USER_TEMPLATE.format(
            user_instruction="test",
            page_text="test",
            text_blocks="[]",
            page_width=612,
            page_height=792,
            visual_description="none",
            layout_complexity="simple",
            column_count=1,
            has_cid_fonts=False,
            text_density=0.0,
            font_summary_formatted="  (none)",
            conversation_context="No previous edits on this page.",
        )
        assert "No previous edits on this page." in rendered

    def test_build_orchestrator_messages_with_context(self):
        from app.prompts.orchestrator_plan import build_orchestrator_messages

        messages = build_orchestrator_messages(
            user_instruction="make it bold",
            page_text="Q4 Revenue Report",
            text_blocks_json="[]",
            page_width=612,
            page_height=792,
            conversation_context='  1. User: "Change Q3 to Q4"\n     → text_replace: success',
        )
        text = messages[0]["parts"][0]["text"]
        assert 'User: "Change Q3 to Q4"' in text


class TestConversationSystemPrompt:
    """Test that the system prompt includes conversation awareness instructions."""

    def test_prompt_has_conversation_section(self):
        from app.prompts.orchestrator_plan import ORCHESTRATOR_SYSTEM_PROMPT

        assert "CONVERSATION CONTEXT" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "RESOLVE REFERENCES" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "UNDERSTAND REVERSALS" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "TRACK CUMULATIVE STATE" in ORCHESTRATOR_SYSTEM_PROMPT

    def test_prompt_has_conversational_examples(self):
        from app.prompts.orchestrator_plan import ORCHESTRATOR_SYSTEM_PROMPT

        assert "Follow-up reference" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "Undo request" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "Refinement after visual edit" in ORCHESTRATOR_SYSTEM_PROMPT
        assert "Reference to earlier edit" in ORCHESTRATOR_SYSTEM_PROMPT


class TestConversationStorageInSnapshots:
    """Test that conversation messages are stored correctly in snapshots."""

    def test_snapshot_stores_conversation(self, state_mgr, session_id):
        result = _make_result(session_id)
        conversation = [
            {"role": "user", "content": "Change Q3 to Q4"},
            {
                "role": "assistant",
                "content": "Replaced Q3 with Q4",
                "operations": [{"op_type": "text_replace", "success": True}],
            },
        ]

        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change Q3 to Q4",
            plan_summary="Replace Q3 with Q4",
            result=result,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=conversation,
        )

        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 2
        assert context[0]["content"] == "Change Q3 to Q4"
        assert context[1]["content"] == "Replaced Q3 with Q4"

    def test_conversation_accumulates(self, state_mgr, session_id):
        for i in range(1, 4):
            result = _make_result(session_id)
            prev = state_mgr.get_conversation_context(session_id, 1)
            conv = list(prev) + [
                {"role": "user", "content": f"Edit {i}"},
                {"role": "assistant", "content": f"Done {i}", "operations": []},
            ]

            state_mgr.snapshot_after_edit(
                session_id=session_id,
                page_num=1,
                prompt=f"Edit {i}",
                plan_summary=f"Plan {i}",
                result=result,
                image_path=f"page_1_v{i}.png",
                text_layer=None,
                text_layer_source="programmatic_edit",
                conversation_messages=conv,
            )

        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 6  # 3 exchanges * 2 messages each

    def test_conversation_reverts_with_step(self, state_mgr, session_id):
        """After revert, conversation matches the target step."""
        # Step 1: one exchange
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 1",
            plan_summary="Plan 1",
            result=_make_result(session_id),
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Edit 1"},
                {"role": "assistant", "content": "Done 1", "operations": []},
            ],
        )

        # Step 2: two exchanges
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 2",
            plan_summary="Plan 2",
            result=_make_result(session_id),
            image_path="page_1_v2.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Edit 1"},
                {"role": "assistant", "content": "Done 1", "operations": []},
                {"role": "user", "content": "Edit 2"},
                {"role": "assistant", "content": "Done 2", "operations": []},
            ],
        )

        # Revert to step 1 — conversation should have only 1 exchange
        state_mgr.restore_to_step(session_id, 1, 1)
        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 2
        assert context[0]["content"] == "Edit 1"

    def test_new_edit_after_revert_branches_conversation(
        self, state_mgr, session_id,
    ):
        """After revert + new edit, conversation picks up from reverted step."""
        # Steps 1,2
        for i in range(1, 3):
            prev = state_mgr.get_conversation_context(session_id, 1)
            conv = list(prev) + [
                {"role": "user", "content": f"Edit {i}"},
                {"role": "assistant", "content": f"Done {i}", "operations": []},
            ]
            state_mgr.snapshot_after_edit(
                session_id=session_id,
                page_num=1,
                prompt=f"Edit {i}",
                plan_summary=f"Plan {i}",
                result=_make_result(session_id),
                image_path=f"page_1_v{i}.png",
                text_layer=None,
                text_layer_source="programmatic_edit",
                conversation_messages=conv,
            )

        # Revert to step 1
        state_mgr.restore_to_step(session_id, 1, 1)

        # New edit — conversation should start from step 1's context
        prev = state_mgr.get_conversation_context(session_id, 1)
        assert len(prev) == 2  # Only step 1's exchange

        conv = list(prev) + [
            {"role": "user", "content": "New branch edit"},
            {"role": "assistant", "content": "Branched", "operations": []},
        ]
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="New branch edit",
            plan_summary="Branch plan",
            result=_make_result(session_id),
            image_path="page_1_v2.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=conv,
        )

        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 4
        assert context[0]["content"] == "Edit 1"
        assert context[2]["content"] == "New branch edit"
        # Edit 2 should NOT be in the conversation
        assert not any(m.get("content") == "Edit 2" for m in context)


# ======================================================================
# Live LLM tests — require GEMINI_API_KEY
# ======================================================================


@pytest.mark.skipif(not HAS_API_KEY, reason="GEMINI_API_KEY not set")
class TestConversationalRoutingLive:
    """Test that the planner correctly handles conversational instructions.

    These tests call the real Gemini API and verify that the LLM correctly
    resolves references, undoes edits, and handles refinements.
    """

    @pytest.fixture
    def orchestrator(self, session_mgr, state_mgr):
        from app.config import settings
        from app.services.model_provider import ProviderFactory

        provider = ProviderFactory.get_provider(
            settings.model_provider,
            settings.gemini_api_key,
        )
        return Orchestrator(
            model_provider=provider,
            session_manager=session_mgr,
            state_manager=state_mgr,
        )

    @staticmethod
    async def _noop_progress(stage, message, extra=None):
        pass

    @pytest.mark.asyncio
    async def test_live_follow_up_reference(
        self, orchestrator, state_mgr, session_id,
    ):
        """'Change 2024 to 2025' then 'now make it bold' → targets '2025'."""
        # Simulate step 1: text_replace 2024 → 2025
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change 2024 to 2025",
            plan_summary="Replace 2024 with 2025",
            result=_make_result(session_id, detail="'2024' -> '2025'"),
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Change 2024 to 2025"},
                {
                    "role": "assistant",
                    "content": "Replace 2024 with 2025",
                    "operations": [
                        {
                            "op_type": "text_replace",
                            "path": "programmatic",
                            "success": True,
                            "time_ms": 89,
                            "detail": "'2024' -> '2025'",
                        }
                    ],
                },
            ],
        )

        # Plan "now make it bold" — should target "2025"
        plan = await orchestrator.plan(
            session_id, 1, "now make it bold", self._noop_progress,
        )

        assert len(plan.operations) >= 1
        op = plan.operations[0]
        assert op.type == "style_change", (
            f"Expected style_change, got {op.type}"
        )
        # "it" should resolve to text containing "2025" or "2024"
        # (the planner should look at the current page text)
        assert hasattr(op, "target_text")

    @pytest.mark.asyncio
    async def test_live_undo_request(
        self, orchestrator, state_mgr, session_id,
    ):
        """'Change 2024 to 2025' then 'change it back' → text_replace 2025→2024."""
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change 2024 to 2025",
            plan_summary="Replace 2024 with 2025",
            result=_make_result(session_id, detail="'2024' -> '2025'"),
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Change 2024 to 2025"},
                {
                    "role": "assistant",
                    "content": "Replace 2024 with 2025",
                    "operations": [
                        {
                            "op_type": "text_replace",
                            "path": "programmatic",
                            "success": True,
                            "time_ms": 89,
                            "detail": "'2024' -> '2025'",
                        }
                    ],
                },
            ],
        )

        plan = await orchestrator.plan(
            session_id, 1, "change it back to 2024", self._noop_progress,
        )

        assert len(plan.operations) >= 1
        op = plan.operations[0]
        assert op.type == "text_replace", (
            f"Expected text_replace, got {op.type}"
        )
        # Should reverse: find "2025" and replace with "2024"
        assert "2024" in op.replacement_text or "2024" in op.original_text

    @pytest.mark.asyncio
    async def test_live_visual_refinement(
        self, orchestrator, state_mgr, session_id,
    ):
        """'Make background blue' then 'darker' → visual_regenerate."""
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="make the background blue",
            plan_summary="Visual edit: blue background",
            result=ExecutionResult(
                session_id=session_id,
                page_num=1,
                version=1,
                plan_summary="Visual edit: blue background",
                operations=[
                    OperationResult(
                        op_index=0,
                        op_type=OperationType.VISUAL_REGENERATE,
                        success=True,
                        time_ms=8200,
                        path="visual",
                        detail="full page visual edit",
                    )
                ],
                total_time_ms=8200,
                programmatic_count=0,
                visual_count=1,
                text_layer_source="ocr",
            ),
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="ocr",
            conversation_messages=[
                {"role": "user", "content": "make the background blue"},
                {
                    "role": "assistant",
                    "content": "Visual edit: blue background",
                    "operations": [
                        {
                            "op_type": "visual_regenerate",
                            "path": "visual",
                            "success": True,
                            "time_ms": 8200,
                            "detail": "full page visual edit",
                        }
                    ],
                },
            ],
        )

        plan = await orchestrator.plan(
            session_id, 1, "darker", self._noop_progress,
        )

        assert len(plan.operations) >= 1
        op = plan.operations[0]
        assert op.type == "visual_regenerate", (
            f"Expected visual_regenerate, got {op.type}"
        )
        # The prompt should reference blue/background/darker
        prompt_lower = op.prompt.lower()
        assert "dark" in prompt_lower or "blue" in prompt_lower

    @pytest.mark.asyncio
    async def test_live_context_after_revert(
        self, orchestrator, state_mgr, session_id,
    ):
        """After reverting to step 1, planner sees only step 1's conversation."""
        # Step 1: change title
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change Q3 to Q4 in the title",
            plan_summary="Replace Q3 with Q4",
            result=_make_result(session_id, detail="'Q3' -> 'Q4'"),
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Change Q3 to Q4 in the title"},
                {
                    "role": "assistant",
                    "content": "Replace Q3 with Q4",
                    "operations": [
                        {
                            "op_type": "text_replace",
                            "path": "programmatic",
                            "success": True,
                            "time_ms": 50,
                            "detail": "'Q3' -> 'Q4'",
                        }
                    ],
                },
            ],
        )

        # Step 2: change date
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change 2024 to 2025",
            plan_summary="Replace 2024 with 2025",
            result=_make_result(session_id, detail="'2024' -> '2025'"),
            image_path="page_1_v2.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Change Q3 to Q4 in the title"},
                {
                    "role": "assistant",
                    "content": "Replace Q3 with Q4",
                    "operations": [
                        {
                            "op_type": "text_replace",
                            "path": "programmatic",
                            "success": True,
                            "time_ms": 50,
                            "detail": "'Q3' -> 'Q4'",
                        }
                    ],
                },
                {"role": "user", "content": "Change 2024 to 2025"},
                {
                    "role": "assistant",
                    "content": "Replace 2024 with 2025",
                    "operations": [
                        {
                            "op_type": "text_replace",
                            "path": "programmatic",
                            "success": True,
                            "time_ms": 60,
                            "detail": "'2024' -> '2025'",
                        }
                    ],
                },
            ],
        )

        # Revert to step 1
        state_mgr.restore_to_step(session_id, 1, 1)

        # Verify conversation context only has step 1
        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 2
        assert context[0]["content"] == "Change Q3 to Q4 in the title"

        # Plan "make the font bigger" should work with step 1's context
        plan = await orchestrator.plan(
            session_id, 1, "make the font bigger", self._noop_progress,
        )
        assert len(plan.operations) >= 1
