"""Edit engine — delegates to the Orchestrator for planning + execution."""

import asyncio
import json
import logging
from pathlib import Path
from typing import Awaitable, Callable

from app.models.schemas import EditResult, EditVersion, ExecutionResult
from app.services.model_provider import ModelProvider
from app.services.orchestrator import Orchestrator
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str], Awaitable[None]]


class EditEngine:
    """Entry point for edits. Wraps the Orchestrator and manages concurrency."""

    def __init__(
        self,
        session_manager: SessionManager,
        model_provider: ModelProvider,
    ):
        self._sessions = session_manager
        self._provider = model_provider
        self._orchestrator = Orchestrator(
            model_provider=model_provider,
            session_manager=session_manager,
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
    ) -> ExecutionResult:
        """Execute an edit via the orchestrator. Returns rich ExecutionResult."""
        lock = self._session_lock(session_id)
        if lock.locked():
            raise RuntimeError("An edit is already in progress for this session")

        # Validate page num
        metadata = self._sessions.get_metadata(session_id)
        page_count = metadata["page_count"]
        if page_num < 1 or page_num > page_count:
            raise ValueError(f"page_num {page_num} out of range (1-{page_count})")

        # Adapt the 2-arg callback from edit.py into the 3-arg callback the
        # orchestrator expects (stage, message, extra_data).
        async def orchestrator_progress(
            stage: str, message: str, extra: dict | None,
        ) -> None:
            await on_progress(stage, message)

        async with lock:
            return await self._orchestrator.execute_edit(
                session_id, page_num, prompt, orchestrator_progress,
            )

    # ------------------------------------------------------------------
    # Edit history (unchanged from Phase 1)
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
        return [
            EditVersion(
                version=entry["version"],
                prompt=entry["prompt"],
                created_at=entry["created_at"],
                text_layer_preserved=entry["text_layer_preserved"],
            )
            for entry in raw
        ]

    async def revert_to_version(
        self, session_id: str, page_num: int, version: int,
    ) -> EditResult:
        """Set the current version pointer back to a previous version."""
        session_path = self._sessions.get_session_path(session_id)

        target_image = session_path / "pages" / f"page_{page_num}_v{version}.png"
        if not target_image.exists():
            raise FileNotFoundError(
                f"Version {version} does not exist for page {page_num}"
            )

        metadata = self._sessions.get_metadata(session_id)
        metadata["current_page_versions"][str(page_num)] = version
        self._sessions.update_metadata(session_id, metadata)

        return EditResult(
            session_id=session_id,
            page_num=page_num,
            version=version,
            processing_time_ms=0,
            text_layer_preserved=True,
        )
