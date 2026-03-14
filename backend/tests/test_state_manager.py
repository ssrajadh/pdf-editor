"""Tests for the page state stack — snapshot, restore, and revert-to-any-step.

Usage:
    cd backend
    python -m pytest tests/test_state_manager.py -v
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF
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


def _get_page_text(pdf_path: Path, page_num: int) -> str:
    """Extract text from a single page using PyMuPDF."""
    doc = fitz.open(str(pdf_path))
    text = doc[page_num - 1].get_text()
    doc.close()
    return text


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


def _make_execution_result(
    session_id: str, page_num: int, version: int,
) -> ExecutionResult:
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


def _init_and_edit(state_mgr, session_id, session_path, page_num, count):
    """Initialize a page and perform `count` simulated edits.

    Returns the list of image paths created (including step 0).
    """
    img0 = str(pdf_service.get_page_image_path(session_path, page_num, "0"))
    state_mgr.initialize_page(session_id, page_num, img0, None)

    for i in range(1, count + 1):
        # Simulate a programmatic edit: modify working PDF text
        sm = state_mgr.session_manager
        working = sm.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        page = doc[page_num - 1]
        # Add a text annotation so the page content changes
        page.insert_text(
            (72, 660 - i * 15),
            f"Edit {i} on page {page_num}",
            fontsize=11,
        )
        # PyMuPDF can't save non-incrementally to the same open file
        tmp = working.with_suffix(".tmp.pdf")
        doc.save(str(tmp), deflate=True)
        doc.close()
        shutil.move(str(tmp), str(working))

        # Re-render
        pages_dir = session_path / "pages"
        img_path = pdf_service.render_page(working, page_num, pages_dir, version=i)

        # Update metadata version
        metadata = sm.get_metadata(session_id)
        metadata["current_page_versions"][str(page_num)] = i
        sm.update_metadata(session_id, metadata)

        result = _make_execution_result(session_id, page_num, i)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=page_num,
            prompt=f"Edit {i}",
            plan_summary=f"Plan {i}",
            result=result,
            image_path=str(img_path),
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": f"Edit {j}"}
                for j in range(1, i + 1)
            ],
        )


# ======================================================================
# Test 1: Upload → step 0 snapshots created for all pages
# ======================================================================


class TestInitializePages:
    def test_step0_snapshots_created(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        for page_num in (1, 2):
            text_data = pdf_service.extract_text(
                session_path / "original.pdf", page_num,
            )
            text_blocks = [TextBlock(**b) for b in text_data["blocks"]]
            image_path = str(
                pdf_service.get_page_image_path(session_path, page_num, "0"),
            )
            state_mgr.initialize_page(session_id, page_num, image_path, text_blocks)

        for page_num in (1, 2):
            stack = state_mgr.get_stack(session_id, page_num)
            assert len(stack.snapshots) == 1
            snap = stack.current
            assert snap.step == 0
            assert snap.prompt is None
            assert snap.execution_result is None
            assert snap.text_layer_source == "original"
            assert snap.pdf_page_hash  # non-empty

    def test_step0_persisted_to_disk(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        state_mgr.initialize_page(
            session_id, 1,
            str(pdf_service.get_page_image_path(session_path, 1, "0")),
            None,
        )
        history_file = session_path / "history" / "page_1" / "snapshots.json"
        assert history_file.exists()
        data = json.loads(history_file.read_text())
        assert len(data["snapshots"]) == 1
        assert data["snapshots"][0]["step"] == 0

    def test_per_page_pdf_saved_for_step0(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        state_mgr.initialize_page(session_id, 1, "img.png", None)

        page_pdf = session_path / "history" / "page_1" / "step_0_page.pdf"
        assert page_pdf.exists()
        # Should be a valid single-page PDF
        doc = fitz.open(str(page_pdf))
        assert len(doc) == 1
        doc.close()


# ======================================================================
# Test 2: Make an edit → step 1 pushed with correct metadata
# ======================================================================


class TestSnapshotAfterEdit:
    def test_step1_pushed(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=1)

        stack = state_mgr.get_stack(session_id, 1)
        assert len(stack.snapshots) == 2
        assert stack.current.step == 1
        assert stack.current.prompt == "Edit 1"
        assert stack.current.plan_summary == "Plan 1"
        assert stack.current.execution_result is not None
        assert stack.current.text_layer_source == "programmatic_edit"

    def test_per_page_pdf_saved_for_step1(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=1)

        page_pdf = session_path / "history" / "page_1" / "step_1_page.pdf"
        assert page_pdf.exists()
        doc = fitz.open(str(page_pdf))
        assert len(doc) == 1
        text = doc[0].get_text()
        assert "Edit 1" in text
        doc.close()


# ======================================================================
# Test 3: Second edit → step 2 pushed
# ======================================================================


class TestMultipleEdits:
    def test_step2_pushed(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=2)

        stack = state_mgr.get_stack(session_id, 1)
        assert len(stack.snapshots) == 3
        assert stack.current.step == 2
        assert stack.current.prompt == "Edit 2"


# ======================================================================
# Test 4: History returns all snapshots in order
# ======================================================================


class TestHistory:
    def test_history_returns_all_ordered(self, state_mgr, session_with_pages):
        """Upload PDF, make 3 edits → verify 4 snapshots (0,1,2,3)."""
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=3)

        stack = state_mgr.get_stack(session_id, 1)
        history = stack.history
        assert len(history) == 4
        assert [s.step for s in history] == [0, 1, 2, 3]
        assert history[0].prompt is None
        assert history[1].prompt == "Edit 1"
        assert history[2].prompt == "Edit 2"
        assert history[3].prompt == "Edit 3"


# ======================================================================
# Test 5: Per-page PDFs exist on disk for each step
# ======================================================================


class TestPerPagePdfStorage:
    def test_page_pdfs_exist_for_all_steps(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=3)

        history_dir = session_path / "history" / "page_1"
        for step in range(4):
            page_pdf = history_dir / f"step_{step}_page.pdf"
            assert page_pdf.exists(), f"Missing step_{step}_page.pdf"
            doc = fitz.open(str(page_pdf))
            assert len(doc) == 1  # single-page PDF
            doc.close()


# ======================================================================
# Test: Revert to step 1 → page image and working PDF match step 1
# ======================================================================


class TestRevertToStep:
    def test_revert_to_step1(self, state_mgr, session_with_pages):
        """Make 3 edits (steps 0-3), revert to step 1."""
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=3)

        # Capture step 1's page text for comparison
        step1_pdf = (
            session_path / "history" / "page_1" / "step_1_page.pdf"
        )
        step1_text = _get_page_text(step1_pdf, 1)

        # Revert
        restored = state_mgr.restore_to_step(session_id, 1, 1)
        assert restored.step == 1
        assert restored.prompt == "Edit 1"

        # Current step should be 1
        stack = state_mgr.get_stack(session_id, 1)
        assert stack.current_step == 1

        # Future snapshots kept (steps 2,3 still exist)
        assert len(stack.snapshots) == 4

        # Working PDF page 1 should match step 1's content
        working = session_path / "working.pdf"
        working_text = _get_page_text(working, 1)
        assert "Edit 1" in working_text
        # Step 2/3 text should NOT be in the working PDF for this page
        assert "Edit 3 on page 1" not in working_text

    def test_revert_updates_metadata(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=3)

        state_mgr.restore_to_step(session_id, 1, 1)

        metadata = state_mgr.session_manager.get_metadata(session_id)
        assert metadata["current_page_versions"]["1"] == 1

    def test_revert_image_matches_step(self, state_mgr, session_with_pages):
        """After revert, the snapshot's image_filename points to the correct image."""
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=2)

        # Get step 1 image path before revert
        stack = state_mgr.get_stack(session_id, 1)
        step1_image = stack.get(1).image_filename

        # Revert to step 1
        restored = state_mgr.restore_to_step(session_id, 1, 1)
        assert restored.image_filename == step1_image
        assert Path(step1_image).exists()


# ======================================================================
# Test: Edit after revert → becomes step N+1, old future truncated
# ======================================================================


class TestEditAfterRevert:
    def test_new_edit_becomes_step2_after_revert_to_1(
        self, state_mgr, session_with_pages,
    ):
        """Steps 0,1,2,3 → revert to 1 → new edit → becomes step 2, old 2,3 gone."""
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=3)

        # Revert to step 1
        state_mgr.restore_to_step(session_id, 1, 1)

        # Make a new edit (step 2, replacing the old step 2)
        sm = state_mgr.session_manager
        working = sm.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        page = doc[0]
        page.insert_text((72, 600), "New branch edit", fontsize=11)
        tmp = working.with_suffix(".tmp.pdf")
        doc.save(str(tmp), deflate=True)
        doc.close()
        shutil.move(str(tmp), str(working))

        pages_dir = session_path / "pages"
        img_path = pdf_service.render_page(working, 1, pages_dir, version=2)

        metadata = sm.get_metadata(session_id)
        metadata["current_page_versions"]["1"] = 2
        sm.update_metadata(session_id, metadata)

        result = _make_execution_result(session_id, 1, 2)
        state_mgr.snapshot_after_edit(
            session_id=session_id,
            page_num=1,
            prompt="New branch edit",
            plan_summary="New branch plan",
            result=result,
            image_path=str(img_path),
            text_layer=None,
            text_layer_source="programmatic_edit",
            conversation_messages=[
                {"role": "user", "content": "Edit 1"},
                {"role": "user", "content": "New branch edit"},
            ],
        )

        stack = state_mgr.get_stack(session_id, 1)
        # Old steps 2,3 should be truncated
        assert len(stack.snapshots) == 3  # 0, 1, 2(new)
        assert [s.step for s in stack.history] == [0, 1, 2]
        assert stack.current.step == 2
        assert stack.current.prompt == "New branch edit"

        # Working PDF should have the new branch text
        working_text = _get_page_text(working, 1)
        assert "New branch edit" in working_text


# ======================================================================
# Test: Revert to step 0 → matches original
# ======================================================================


class TestRevertToOriginal:
    def test_revert_to_step0(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=2)

        original_text = _get_page_text(session_path / "original.pdf", 1)

        state_mgr.restore_to_step(session_id, 1, 0)

        stack = state_mgr.get_stack(session_id, 1)
        assert stack.current_step == 0

        # Page 1 in working PDF should match original
        working_text = _get_page_text(session_path / "working.pdf", 1)
        assert working_text.strip() == original_text.strip()

        metadata = state_mgr.session_manager.get_metadata(session_id)
        assert metadata["current_page_versions"]["1"] == 0


# ======================================================================
# Test: Edit page 2, then revert page 1 → page 2 unaffected
# ======================================================================


class TestCrossPageIsolation:
    def test_revert_page1_preserves_page2(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        # Initialize both pages
        img1 = str(pdf_service.get_page_image_path(session_path, 1, "0"))
        img2 = str(pdf_service.get_page_image_path(session_path, 2, "0"))
        state_mgr.initialize_page(session_id, 1, img1, None)
        state_mgr.initialize_page(session_id, 2, img2, None)

        # Edit page 1
        sm = state_mgr.session_manager
        working = sm.get_working_pdf_path(session_id)

        doc = fitz.open(str(working))
        doc[0].insert_text((72, 650), "Page1 edit A", fontsize=11)
        tmp = working.with_suffix(".tmp.pdf")
        doc.save(str(tmp), deflate=True)
        doc.close()
        shutil.move(str(tmp), str(working))

        pages_dir = session_path / "pages"
        img_p1 = pdf_service.render_page(working, 1, pages_dir, version=1)
        metadata = sm.get_metadata(session_id)
        metadata["current_page_versions"]["1"] = 1
        sm.update_metadata(session_id, metadata)

        r1 = _make_execution_result(session_id, 1, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id, page_num=1,
            prompt="Page1 edit A", plan_summary="Plan A", result=r1,
            image_path=str(img_p1), text_layer=None,
            text_layer_source="programmatic_edit", conversation_messages=[],
        )

        # Edit page 2
        doc = fitz.open(str(working))
        doc[1].insert_text((72, 650), "Page2 edit B", fontsize=11)
        tmp = working.with_suffix(".tmp.pdf")
        doc.save(str(tmp), deflate=True)
        doc.close()
        shutil.move(str(tmp), str(working))

        img_p2 = pdf_service.render_page(working, 2, pages_dir, version=1)
        metadata = sm.get_metadata(session_id)
        metadata["current_page_versions"]["2"] = 1
        sm.update_metadata(session_id, metadata)

        r2 = _make_execution_result(session_id, 2, 1)
        state_mgr.snapshot_after_edit(
            session_id=session_id, page_num=2,
            prompt="Page2 edit B", plan_summary="Plan B", result=r2,
            image_path=str(img_p2), text_layer=None,
            text_layer_source="programmatic_edit", conversation_messages=[],
        )

        # Verify page 2 text before revert
        p2_text_before = _get_page_text(working, 2)
        assert "Page2 edit B" in p2_text_before

        # Revert page 1 to step 0 — should NOT touch page 2
        state_mgr.restore_to_step(session_id, 1, 0)

        # Page 1 should be original
        p1_text = _get_page_text(session_path / "working.pdf", 1)
        assert "Page1 edit A" not in p1_text

        # Page 2 should still have its edit
        p2_text_after = _get_page_text(session_path / "working.pdf", 2)
        assert "Page2 edit B" in p2_text_after

        # Page 2 stack should be unaffected
        stack2 = state_mgr.get_stack(session_id, 2)
        assert stack2.current_step == 1


# ======================================================================
# Test: Invalid step raises ValueError
# ======================================================================


class TestRevertInvalid:
    def test_invalid_step(self, state_mgr, session_with_pages):
        session_id, _ = session_with_pages
        state_mgr.initialize_page(session_id, 1, "img.png", None)

        with pytest.raises(ValueError, match="Step 99 not found"):
            state_mgr.restore_to_step(session_id, 1, 99)


# ======================================================================
# Test: Conversation context retrieval
# ======================================================================


class TestConversationContext:
    def test_context_from_current_step(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=2)

        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 2
        assert context[0]["content"] == "Edit 1"
        assert context[1]["content"] == "Edit 2"

    def test_context_after_revert(self, state_mgr, session_with_pages):
        session_id, session_path = session_with_pages
        _init_and_edit(state_mgr, session_id, session_path, 1, count=3)

        state_mgr.restore_to_step(session_id, 1, 1)
        context = state_mgr.get_conversation_context(session_id, 1)
        assert len(context) == 1
        assert context[0]["content"] == "Edit 1"

    def test_empty_context_step0(self, state_mgr, session_with_pages):
        session_id, _ = session_with_pages
        state_mgr.initialize_page(session_id, 1, "img.png", None)
        assert state_mgr.get_conversation_context(session_id, 1) == []


# ======================================================================
# Test: Disk persistence survives "restart"
# ======================================================================


class TestDiskPersistence:
    def test_reload_from_disk(self, session_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        mgr1 = StateManager(session_mgr)
        _init_and_edit(mgr1, session_id, session_path, 1, count=2)

        # Simulate server restart
        mgr2 = StateManager(session_mgr)
        stack = mgr2.get_stack(session_id, 1)

        assert len(stack.snapshots) == 3
        assert stack.current.step == 2
        assert stack.current.prompt == "Edit 2"


# ======================================================================
# Test: SessionManager per-page methods
# ======================================================================


class TestSessionManagerPageMethods:
    def test_save_and_restore_page(self, session_mgr, session_with_pages):
        session_id, session_path = session_with_pages

        # Save page 1 as step 0
        session_mgr.save_page_pdf(session_id, 1, 0)
        step0_pdf = session_path / "history" / "page_1" / "step_0_page.pdf"
        assert step0_pdf.exists()

        # Verify it's a single-page PDF with correct content
        doc = fitz.open(str(step0_pdf))
        assert len(doc) == 1
        assert "Page 1 original content" in doc[0].get_text()
        doc.close()

    def test_restore_replaces_only_target_page(
        self, session_mgr, session_with_pages,
    ):
        session_id, session_path = session_with_pages

        # Save step 0 pages
        session_mgr.save_page_pdf(session_id, 1, 0)
        session_mgr.save_page_pdf(session_id, 2, 0)

        # Modify page 1 in working PDF
        working = session_mgr.get_working_pdf_path(session_id)
        doc = fitz.open(str(working))
        doc[0].insert_text((72, 600), "MODIFIED PAGE 1", fontsize=14)
        doc[1].insert_text((72, 600), "MODIFIED PAGE 2", fontsize=14)
        tmp = working.with_suffix(".tmp.pdf")
        doc.save(str(tmp), deflate=True)
        doc.close()
        shutil.move(str(tmp), str(working))

        # Verify both modifications exist
        assert "MODIFIED PAGE 1" in _get_page_text(working, 1)
        assert "MODIFIED PAGE 2" in _get_page_text(working, 2)

        # Restore page 1 only
        session_mgr.restore_page_in_working_pdf(session_id, 1, 0)

        # Page 1 should be restored to original
        assert "MODIFIED PAGE 1" not in _get_page_text(working, 1)
        assert "Page 1 original content" in _get_page_text(working, 1)

        # Page 2 should still have modification
        assert "MODIFIED PAGE 2" in _get_page_text(working, 2)

    def test_restore_missing_step_raises(
        self, session_mgr, session_with_pages,
    ):
        session_id, _ = session_with_pages

        with pytest.raises(FileNotFoundError):
            session_mgr.restore_page_in_working_pdf(session_id, 1, 99)
