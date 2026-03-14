"""Tests for the page state stack (snapshot and restore).

Usage:
    cd backend
    python -m pytest tests/test_state_manager.py -v
"""

import json
import shutil
from datetime import datetime, timezone
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
from app.services import pdf_service
from app.services.state_manager import PageSnapshot, PageStateStack, StateManager
from app.storage.session import SessionManager

STORAGE = Path(__file__).parent / "test_data_state"


def _make_pdf(pages: int = 2) -> bytes:
    """Generate a simple multi-page PDF in memory and return bytes."""
    path = STORAGE / "_tmp_fixture.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, f"Page {i} original content")
        c.drawString(72, 680, f"Version 0 — this is page {i}")
        c.showPage()
    c.save()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


@pytest.fixture(autouse=True)
def clean_storage():
    """Ensure clean test storage before and after each test."""
    if STORAGE.exists():
        shutil.rmtree(STORAGE)
    STORAGE.mkdir(parents=True, exist_ok=True)
    yield
    if STORAGE.exists():
        shutil.rmtree(STORAGE)


@pytest.fixture
def session_mgr() -> SessionManager:
    return SessionManager(STORAGE)


@pytest.fixture
def state_mgr(session_mgr) -> StateManager:
    return StateManager(session_mgr)


@pytest.fixture
def session_with_pages(session_mgr):
    """Create a session with a 2-page PDF, rendered pages, and return (session_id, session_path)."""
    pdf_bytes = _make_pdf(pages=2)
    session_id = session_mgr.create_session(pdf_bytes, "test.pdf", 2)
    session_path = session_mgr.get_session_path(session_id)
    pdf_path = session_path / "original.pdf"
    pages_dir = session_path / "pages"

    # Render pages
    pdf_service.render_all_pages(pdf_path, pages_dir)

    return session_id, session_path


def _make_execution_result(session_id: str, page_num: int, version: int) -> ExecutionResult:
    """Build a mock ExecutionResult."""
    return ExecutionResult(
        session_id=session_id,
        page_num=page_num,
        version=version,
        plan_summary=f"Edit step {version}",
        operations=[
            OperationResult(
                op_index=0,
                op_type=OperationType.TEXT_REPLACE,
                success=True,
                time_ms=100,
                path="programmatic",
                detail="Replaced text",
            )
        ],
        total_time_ms=100,
        programmatic_count=1,
        visual_count=0,
        text_layer_source="programmatic_edit",
    )


# -----------------------------------------------------------------------
# Test 1: Upload → step 0 snapshots created for all pages
# -----------------------------------------------------------------------


class TestInitializePages:
    def test_step0_snapshots_created(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        for page_num in (1, 2):
            text_data = pdf_service.extract_text(
                session_path / "original.pdf", page_num
            )
            text_blocks = [TextBlock(**b) for b in text_data["blocks"]]
            image_path = str(
                pdf_service.get_page_image_path(session_path, page_num, "0")
            )

            state_mgr.initialize_page(session_id, page_num, image_path, text_blocks)

        # Verify both pages have step 0
        for page_num in (1, 2):
            stack = state_mgr.get_stack(session_id, page_num)
            assert len(stack.snapshots) == 1
            snap = stack.current
            assert snap.step == 0
            assert snap.prompt is None
            assert snap.plan_summary is None
            assert snap.execution_result is None
            assert snap.text_layer_source == "original"
            assert snap.conversation_messages == []
            assert snap.pdf_page_hash  # non-empty hash
            assert snap.image_filename  # non-empty path

    def test_step0_snapshot_persisted_to_disk(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        text_data = pdf_service.extract_text(session_path / "original.pdf", 1)
        text_blocks = [TextBlock(**b) for b in text_data["blocks"]]
        image_path = str(
            pdf_service.get_page_image_path(session_path, 1, "0")
        )
        state_mgr.initialize_page(session_id, 1, image_path, text_blocks)

        # Verify JSON file exists
        history_path = session_path / "history" / "page_1" / "snapshots.json"
        assert history_path.exists()

        data = json.loads(history_path.read_text())
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["step"] == 0

    def test_working_pdf_copy_saved_for_step0(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        # working_step_0.pdf should exist
        step0_pdf = session_path / "history" / "working_step_0.pdf"
        assert step0_pdf.exists()
        assert step0_pdf.stat().st_size > 0


# -----------------------------------------------------------------------
# Test 2: Edit → step 1 snapshot pushed
# -----------------------------------------------------------------------


class TestSnapshotAfterEdit:
    def test_step1_snapshot_pushed(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        # Initialize page 1
        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        # Simulate an edit
        result = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change title to Q4",
            plan_summary="Replace Q3 with Q4 in title",
            result=result,
            image_path="page_1_v1.png",
            text_layer=[TextBlock(text="Q4", x0=72, y0=700, x1=100, y1=714)],
            text_layer_source="programmatic_edit",
            conversation_messages=[{"role": "user", "content": "Change title to Q4"}],
        )

        stack = state_mgr.get_stack(session_id, 1)
        assert len(stack.snapshots) == 2
        assert stack.current.step == 1
        assert stack.current.prompt == "Change title to Q4"
        assert stack.current.plan_summary == "Replace Q3 with Q4 in title"
        assert stack.current.execution_result is not None
        assert stack.current.text_layer_source == "programmatic_edit"
        assert len(stack.current.conversation_messages) == 1

    def test_step1_working_pdf_copy_saved(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        result = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 1",
            plan_summary="Plan 1",
            result=result,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[],
        )

        step1_pdf = session_path / "history" / "working_step_1.pdf"
        assert step1_pdf.exists()


# -----------------------------------------------------------------------
# Test 3: Second edit → step 2 snapshot pushed
# -----------------------------------------------------------------------


class TestMultipleEdits:
    def test_step2_snapshot_pushed(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        # Edit 1
        result1 = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 1",
            plan_summary="Plan 1",
            result=result1,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[{"role": "user", "content": "Edit 1"}],
        )

        # Edit 2
        result2 = _make_execution_result(session_id, 1, 2)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 2",
            plan_summary="Plan 2",
            result=result2,
            image_path="page_1_v2.png",
            text_layer=None,
            text_layer_source="ocr",
            conversation_messages=[
                {"role": "user", "content": "Edit 1"},
                {"role": "user", "content": "Edit 2"},
            ],
        )

        stack = state_mgr.get_stack(session_id, 1)
        assert len(stack.snapshots) == 3
        assert stack.current.step == 2
        assert stack.current.prompt == "Edit 2"
        assert stack.current.text_layer_source == "ocr"


# -----------------------------------------------------------------------
# Test 4: History returns all snapshots in order
# -----------------------------------------------------------------------


class TestHistory:
    def test_history_returns_all_snapshots_ordered(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        for i in range(1, 3):
            result = _make_execution_result(session_id, 1, i)
            state_mgr.snapshot_after_edit(
                session_id=session_id,
                page_num=1,
                prompt=f"Edit {i}",
                plan_summary=f"Plan {i}",
                result=result,
                image_path=f"page_1_v{i}.png",
                text_layer=None,
                text_layer_source="programmatic_edit",
                conversation_messages=[],
            )

        stack = state_mgr.get_stack(session_id, 1)
        history = stack.history
        assert len(history) == 3
        assert [s.step for s in history] == [0, 1, 2]
        assert history[0].prompt is None
        assert history[1].prompt == "Edit 1"
        assert history[2].prompt == "Edit 2"


# -----------------------------------------------------------------------
# Test 5: Working PDF copies exist on disk for each step
# -----------------------------------------------------------------------


class TestWorkingPdfCopies:
    def test_pdf_copies_exist_for_all_steps(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        for i in range(1, 4):
            result = _make_execution_result(session_id, 1, i)
            state_mgr.snapshot_after_edit(
                session_id=session_id,
                page_num=1,
                prompt=f"Edit {i}",
                plan_summary=f"Plan {i}",
                result=result,
                image_path=f"page_1_v{i}.png",
                text_layer=None,
                text_layer_source="programmatic_edit",
                conversation_messages=[],
            )

        history_dir = session_path / "history"
        for step in range(4):
            pdf_copy = history_dir / f"working_step_{step}.pdf"
            assert pdf_copy.exists(), f"Missing working_step_{step}.pdf"
            assert pdf_copy.stat().st_size > 0


# -----------------------------------------------------------------------
# Test: Restore to a previous step
# -----------------------------------------------------------------------


class TestRestore:
    def test_restore_to_step0(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        result = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 1",
            plan_summary="Plan 1",
            result=result,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[],
        )

        restored = state_mgr.restore_to_step(session_id, 1, 0)
        assert restored.step == 0
        assert restored.prompt is None

        # Current should now point to step 0
        stack = state_mgr.get_stack(session_id, 1)
        assert stack.current_step == 0

        # But step 1 snapshot should still exist (not deleted)
        assert len(stack.snapshots) == 2

    def test_restore_replaces_working_pdf(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        sm = state_mgr.session_manager

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        # Get original PDF hash
        original_hash = (session_path / "original.pdf").read_bytes()

        # Simulate modifying the working PDF
        working = sm.get_working_pdf_path(session_id)
        working.write_bytes(original_hash + b"modified")

        result = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 1",
            plan_summary="Plan 1",
            result=result,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[],
        )

        # Restore to step 0 — working.pdf should be replaced with original
        state_mgr.restore_to_step(session_id, 1, 0)

        restored_bytes = (session_path / "working.pdf").read_bytes()
        assert restored_bytes == original_hash  # matches the step 0 copy

    def test_restore_invalid_step_raises(self, state_mgr, session_with_pages):
        session_id, _ = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        with pytest.raises(ValueError, match="Step 99 not found"):
            state_mgr.restore_to_step(session_id, 1, 99)


# -----------------------------------------------------------------------
# Test: Conversation context retrieval
# -----------------------------------------------------------------------


class TestConversationContext:
    def test_get_conversation_context(self, state_mgr, session_with_pages):
        session_id, _ = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        msgs = [
            {"role": "user", "content": "Change Q3 to Q4"},
            {"role": "assistant", "content": "Done"},
        ]
        result = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Change Q3 to Q4",
            plan_summary="Plan",
            result=result,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=msgs,
        )

        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 2
        assert context[0]["role"] == "user"

    def test_empty_context_for_step0(self, state_mgr, session_with_pages):
        session_id, _ = session_with_pages

        state_mgr.initialize_page(session_id, 1, "page_1_v0.png", None)

        context = state_mgr.get_conversation_context(session_id, 1)
        assert context == []


# -----------------------------------------------------------------------
# Test: PageStateStack disk persistence
# -----------------------------------------------------------------------


class TestDiskPersistence:
    def test_reload_from_disk(self, session_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        # Create a state manager, add data, then discard it
        mgr1 = StateManager(session_mgr)
        mgr1.initialize_page(session_id, 1, "page_1_v0.png", None)

        result = _make_execution_result(session_id, 1, 1)
        mgr1.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="Edit 1",
            plan_summary="Plan 1",
            result=result,
            image_path="page_1_v1.png",
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[{"role": "user", "content": "hi"}],
        )

        # Create a new state manager (simulating server restart)
        mgr2 = StateManager(session_mgr)
        stack = mgr2.get_stack(session_id, 1)

        assert len(stack.snapshots) == 2
        assert stack.current.step == 1
        assert stack.current.prompt == "Edit 1"
        assert stack.current.conversation_messages == [{"role": "user", "content": "hi"}]


# -----------------------------------------------------------------------
# Test: SessionManager new methods
# -----------------------------------------------------------------------


class TestSessionManagerHistory:
    def test_get_history_path_creates_dir(self, session_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        path = session_mgr.get_history_path(session_id, 1)
        assert path.exists()
        assert path == session_path / "history" / "page_1"

    def test_save_and_restore_working_pdf(self, session_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        # Save step 0 (from original since no working.pdf yet)
        session_mgr.save_working_pdf_copy(session_id, 0)

        original_bytes = (session_path / "original.pdf").read_bytes()
        step0_bytes = (session_path / "history" / "working_step_0.pdf").read_bytes()
        assert step0_bytes == original_bytes

        # Create a modified working.pdf
        working = session_mgr.get_working_pdf_path(session_id)
        modified = original_bytes + b"EXTRA"
        working.write_bytes(modified)

        # Save step 1
        session_mgr.save_working_pdf_copy(session_id, 1)
        step1_bytes = (session_path / "history" / "working_step_1.pdf").read_bytes()
        assert step1_bytes == modified

        # Restore step 0
        session_mgr.restore_working_pdf_from_step(session_id, 0)
        restored = (session_path / "working.pdf").read_bytes()
        assert restored == original_bytes

    def test_restore_missing_step_raises(self, session_mgr, session_with_pages):
        session_id, _ = session_with_pages

        with pytest.raises(FileNotFoundError):
            session_mgr.restore_working_pdf_from_step(session_id, 99)
