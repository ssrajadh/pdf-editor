"""Page state stack — snapshot and restore for undo/redo."""

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from app.models.schemas import ExecutionResult, TextBlock
from app.services import pdf_service
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)


class PageSnapshot(BaseModel):
    """Complete state of a single page at a point in time."""

    step: int  # 0 = original, 1 = first edit, etc.
    timestamp: datetime
    prompt: str | None  # null for step 0 (original)
    plan_summary: str | None
    execution_result: ExecutionResult | None
    image_filename: str  # filename of rendered PNG in session dir
    text_layer: list[TextBlock] | None  # extracted text with positions
    text_layer_source: str  # "original", "programmatic_edit", "ocr"
    pdf_page_hash: str  # hash of the working PDF page content at this point
    conversation_messages: list[dict]  # conversation history up to this point


class PageStateStack:
    """Manages the ordered history of snapshots for a single page."""

    def __init__(self, session_id: str, page_num: int, storage_path: Path):
        self.session_id = session_id
        self.page_num = page_num
        self.storage_path = storage_path  # {session_dir}/history/page_{num}/
        self.snapshots: list[PageSnapshot] = []
        self._current_index: int = -1  # points to the "active" snapshot
        self._load_from_disk()

    def push(self, snapshot: PageSnapshot):
        """Add a new snapshot after an edit.

        If we previously reverted (current_index < len-1), truncate
        future snapshots — a new edit creates a new branch.
        The image file should already exist at image_filename.
        """
        # Truncate any snapshots after current if we branched
        if self._current_index < len(self.snapshots) - 1:
            self.snapshots = self.snapshots[: self._current_index + 1]

        self.snapshots.append(snapshot)
        self._current_index = len(self.snapshots) - 1
        self._save_to_disk()

    def get(self, step: int) -> PageSnapshot | None:
        """Get snapshot at a specific step."""
        for snap in self.snapshots:
            if snap.step == step:
                return snap
        return None

    @property
    def current(self) -> PageSnapshot:
        """The latest snapshot (current state)."""
        if not self.snapshots:
            raise IndexError("No snapshots in stack")
        return self.snapshots[self._current_index]

    @property
    def current_step(self) -> int:
        """The step number of the current snapshot."""
        if not self.snapshots:
            return -1
        return self.snapshots[self._current_index].step

    @property
    def history(self) -> list[PageSnapshot]:
        """All snapshots in order."""
        return list(self.snapshots)

    def set_current(self, step: int) -> PageSnapshot:
        """Move the current pointer to a specific step without deleting future snapshots."""
        for i, snap in enumerate(self.snapshots):
            if snap.step == step:
                self._current_index = i
                self._save_to_disk()
                return snap
        raise ValueError(f"Step {step} not found in stack")

    def _save_to_disk(self):
        """Persist snapshot metadata to {storage_path}/snapshots.json"""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self.session_id,
            "page_num": self.page_num,
            "current_index": self._current_index,
            "snapshots": [
                snap.model_dump(mode="json") for snap in self.snapshots
            ],
        }
        (self.storage_path / "snapshots.json").write_text(
            json.dumps(data, default=str)
        )

    def _load_from_disk(self):
        """Load snapshot metadata from disk on initialization."""
        snapshots_file = self.storage_path / "snapshots.json"
        if not snapshots_file.exists():
            return

        try:
            data = json.loads(snapshots_file.read_text())
            self.snapshots = [
                PageSnapshot.model_validate(s) for s in data["snapshots"]
            ]
            self._current_index = data.get(
                "current_index", len(self.snapshots) - 1
            )
        except Exception:
            logger.warning(
                "Failed to load snapshots from %s, starting fresh",
                snapshots_file,
                exc_info=True,
            )
            self.snapshots = []
            self._current_index = -1


def _hash_pdf_page(pdf_path: Path, page_num: int) -> str:
    """Compute a SHA-256 hash of a PDF page's raw bytes."""
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(str(pdf_path))
        page = doc[page_num - 1]  # 0-indexed
        content = page.get_text("rawdict")
        raw = json.dumps(content, sort_keys=True, default=str).encode()
        doc.close()
        return hashlib.sha256(raw).hexdigest()[:16]
    except Exception:
        # Fallback: hash the whole file (less precise but safe)
        return hashlib.sha256(pdf_path.read_bytes()).hexdigest()[:16]


class StateManager:
    """Manages state stacks for all pages in a session."""

    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager
        self._stacks: dict[str, dict[int, PageStateStack]] = {}

    def get_stack(self, session_id: str, page_num: int) -> PageStateStack:
        """Get or create the state stack for a page."""
        if session_id not in self._stacks:
            self._stacks[session_id] = {}

        if page_num not in self._stacks[session_id]:
            history_path = self.session_manager.get_history_path(
                session_id, page_num
            )
            self._stacks[session_id][page_num] = PageStateStack(
                session_id=session_id,
                page_num=page_num,
                storage_path=history_path,
            )

        return self._stacks[session_id][page_num]

    def initialize_page(
        self,
        session_id: str,
        page_num: int,
        image_path: str,
        text_layer: list[TextBlock] | None,
    ):
        """Create the initial snapshot (step 0) for a page from the original PDF.

        Called when a PDF is first uploaded and pages are rendered.
        Also saves a copy of the working PDF for step 0.
        """
        session_path = self.session_manager.get_session_path(session_id)
        pdf_path = session_path / "original.pdf"

        page_hash = _hash_pdf_page(pdf_path, page_num)

        # Save a copy of the working PDF for step 0
        self.session_manager.save_working_pdf_copy(session_id, 0)

        snapshot = PageSnapshot(
            step=0,
            timestamp=datetime.now(timezone.utc),
            prompt=None,
            plan_summary=None,
            execution_result=None,
            image_filename=image_path,
            text_layer=text_layer,
            text_layer_source="original",
            pdf_page_hash=page_hash,
            conversation_messages=[],
        )

        stack = self.get_stack(session_id, page_num)
        stack.push(snapshot)

    def snapshot_after_edit(
        self,
        session_id: str,
        page_num: int,
        prompt: str,
        plan_summary: str,
        result: ExecutionResult,
        image_path: str,
        text_layer: list[TextBlock] | None,
        text_layer_source: str,
        conversation_messages: list[dict],
    ):
        """Push a new snapshot after a successful edit.

        Also saves a copy of the working PDF at this step.
        """
        stack = self.get_stack(session_id, page_num)
        new_step = stack.current_step + 1

        session_path = self.session_manager.get_session_path(session_id)
        working_pdf = session_path / "working.pdf"
        pdf_for_hash = working_pdf if working_pdf.exists() else session_path / "original.pdf"
        page_hash = _hash_pdf_page(pdf_for_hash, page_num)

        # Save a copy of the working PDF at this step
        self.session_manager.save_working_pdf_copy(session_id, new_step)

        snapshot = PageSnapshot(
            step=new_step,
            timestamp=datetime.now(timezone.utc),
            prompt=prompt,
            plan_summary=plan_summary,
            execution_result=result,
            image_filename=image_path,
            text_layer=text_layer,
            text_layer_source=text_layer_source,
            pdf_page_hash=page_hash,
            conversation_messages=conversation_messages,
        )

        stack.push(snapshot)

    def restore_to_step(
        self, session_id: str, page_num: int, step: int
    ) -> PageSnapshot:
        """Restore a page to a previous state.

        1. Get the target snapshot
        2. Restore the working PDF from the stored copy
        3. Update the current pointer (keep future snapshots)
        4. Return the restored snapshot
        """
        stack = self.get_stack(session_id, page_num)
        target = stack.get(step)
        if target is None:
            raise ValueError(
                f"Step {step} not found for page {page_num}"
            )

        # Restore the working PDF from the stored copy
        self.session_manager.restore_working_pdf_from_step(session_id, step)

        # Move the current pointer (don't delete future snapshots)
        stack.set_current(step)

        return target

    def get_conversation_context(
        self, session_id: str, page_num: int
    ) -> list[dict]:
        """Get the conversation history for a page from the current snapshot."""
        stack = self.get_stack(session_id, page_num)
        if not stack.snapshots:
            return []
        return stack.current.conversation_messages
