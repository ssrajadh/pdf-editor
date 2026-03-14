"""Edit engine — delegates to the Orchestrator for planning + execution."""

import asyncio
import json
import logging
from typing import Awaitable, Callable

from app.models.schemas import (
    EditResult,
    EditVersion,
    ExecutionPlan,
    ExecutionResult,
    OperationResult,
    OperationType,
)
from app.services.model_provider import ModelProvider
from app.services.orchestrator import Orchestrator
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str, dict | None], Awaitable[None]]


class EditEngine:
    """Entry point for edits. Wraps the Orchestrator and manages concurrency."""

    def __init__(
        self,
        session_manager: SessionManager,
        model_provider: ModelProvider,
        state_manager: StateManager | None = None,
    ):
        self._sessions = session_manager
        self._provider = model_provider
        self._state_manager = state_manager or StateManager(session_manager)
        self._orchestrator = Orchestrator(
            model_provider=model_provider,
            session_manager=session_manager,
            state_manager=self._state_manager,
        )
        self._locks: dict[str, asyncio.Lock] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def execute_edit(
        self,
        session_id: str,
        page_num: int,
        prompt: str,
        on_progress: ProgressCallback,
        force_visual: bool = False,
    ) -> ExecutionResult:
        """Execute an edit via the orchestrator. Returns rich ExecutionResult."""
        lock = self._session_lock(session_id)
        if lock.locked():
            raise RuntimeError("An edit is already in progress for this session")

        metadata = self._sessions.get_metadata(session_id)
        page_count = metadata["page_count"]
        if page_num < 1 or page_num > page_count:
            raise ValueError(f"page_num {page_num} out of range (1-{page_count})")

        async with lock:
            return await self._orchestrator.execute_edit(
                session_id, page_num, prompt, on_progress, force_visual,
            )

    # ------------------------------------------------------------------
    # Plan preview (no execution)
    # ------------------------------------------------------------------

    async def preview_plan(
        self,
        session_id: str,
        page_num: int,
        prompt: str,
    ) -> ExecutionPlan:
        """Generate an execution plan without running it."""
        metadata = self._sessions.get_metadata(session_id)
        page_count = metadata["page_count"]
        if page_num < 1 or page_num > page_count:
            raise ValueError(f"page_num {page_num} out of range (1-{page_count})")

        return await self._orchestrator.plan_only(session_id, page_num, prompt)

    # ------------------------------------------------------------------
    # Edit history
    # ------------------------------------------------------------------

    async def get_edit_history(
        self, session_id: str, page_num: int,
    ) -> list[EditVersion]:
        history_path = (
            self._sessions.get_session_path(session_id)
            / "edits"
            / f"page_{page_num}_history.json"
        )
        if not history_path.exists():
            return []

        raw = json.loads(history_path.read_text())
        versions: list[EditVersion] = []
        for entry in raw:
            ops_raw = entry.get("operations", [])
            ops: list[OperationResult] = []
            for op_data in ops_raw:
                try:
                    if isinstance(op_data.get("op_type"), str):
                        op_data["op_type"] = OperationType(op_data["op_type"])
                    ops.append(OperationResult.model_validate(op_data))
                except Exception:
                    continue

            versions.append(EditVersion(
                version=entry["version"],
                prompt=entry["prompt"],
                created_at=entry["created_at"],
                text_layer_preserved=entry.get("text_layer_preserved", True),
                plan_summary=entry.get("plan_summary", ""),
                operations=ops,
                base_source=entry.get("base_source", ""),
                text_layer_source=entry.get("text_layer_source", ""),
                working_pdf_modified=entry.get("working_pdf_modified", False),
            ))
        return versions

    # ------------------------------------------------------------------
    # Simplified revert (Phase 2)
    # ------------------------------------------------------------------

    async def revert_to_version(
        self, session_id: str, page_num: int, version: int,
    ) -> EditResult:
        """Revert a page to a previous version.

        Phase 2 simplified revert:
        - Revert to v0 (original): deletes working.pdf and resets all pages
          to their original state.
        - Revert to version N: updates the version pointer. For visual-only
          edits this is lossless. For programmatic edits, the working PDF
          retains accumulated changes (logged as a warning).
        - Full arbitrary revert with working PDF replay is Phase 3.
        """
        session_path = self._sessions.get_session_path(session_id)

        if version == 0:
            working_pdf = session_path / "working.pdf"
            if working_pdf.exists():
                working_pdf.unlink()
                logger.info(
                    "Revert to original: deleted working.pdf for session %s",
                    session_id,
                )

            metadata = self._sessions.get_metadata(session_id)
            metadata["current_page_versions"][str(page_num)] = 0
            self._sessions.update_metadata(session_id, metadata)

            return EditResult(
                session_id=session_id,
                page_num=page_num,
                version=0,
                processing_time_ms=0,
                text_layer_preserved=True,
            )

        target_image = session_path / "pages" / f"page_{page_num}_v{version}.png"
        if not target_image.exists():
            raise FileNotFoundError(
                f"Version {version} does not exist for page {page_num}"
            )

        # Check if any version between target+1 and current modified working PDF
        history_path = (
            session_path / "edits" / f"page_{page_num}_history.json"
        )
        if history_path.exists():
            history = json.loads(history_path.read_text())
            reverted_versions = [
                e for e in history
                if e["version"] > version and e.get("working_pdf_modified", False)
            ]
            if reverted_versions:
                logger.warning(
                    "Reverting page %d to v%d, but %d later version(s) modified "
                    "working.pdf. The working PDF retains those changes. "
                    "Full revert with PDF replay is a Phase 3 feature.",
                    page_num, version, len(reverted_versions),
                )

        metadata = self._sessions.get_metadata(session_id)
        metadata["current_page_versions"][str(page_num)] = version
        self._sessions.update_metadata(session_id, metadata)

        # Determine text_layer_preserved from history
        text_layer_preserved = True
        if history_path.exists():
            history = json.loads(history_path.read_text())
            for entry in history:
                if entry["version"] == version:
                    text_layer_preserved = entry.get("text_layer_preserved", True)
                    break

        return EditResult(
            session_id=session_id,
            page_num=page_num,
            version=version,
            processing_time_ms=0,
            text_layer_preserved=text_layer_preserved,
        )
