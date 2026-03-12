"""Edit orchestration engine — Phase 1 full-page visual regeneration."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Awaitable

from PIL import Image

from app.models.schemas import EditResult, EditVersion
from app.services.model_provider import ModelProvider
from app.services import pdf_service
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, str], Awaitable[None]]


class EditEngine:
    """Orchestrates the edit pipeline: load → extract → generate → save."""

    def __init__(
        self,
        session_manager: SessionManager,
        model_provider: ModelProvider,
    ):
        self._sessions = session_manager
        self._provider = model_provider
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
    ) -> EditResult:
        lock = self._session_lock(session_id)
        if lock.locked():
            raise RuntimeError("An edit is already in progress for this session")

        async with lock:
            return await self._run_pipeline(session_id, page_num, prompt, on_progress)

    async def _run_pipeline(
        self,
        session_id: str,
        page_num: int,
        prompt: str,
        on_progress: ProgressCallback,
    ) -> EditResult:
        t_start = time.monotonic()

        session_path = self._sessions.get_session_path(session_id)
        metadata = self._sessions.get_metadata(session_id)
        page_count = metadata["page_count"]
        if page_num < 1 or page_num > page_count:
            raise ValueError(f"page_num {page_num} out of range (1-{page_count})")

        # --- Step 1: Load current page image ---
        await on_progress("loading", "Loading page...")
        page_image_path = pdf_service.get_page_image_path(session_path, page_num)
        page_image = await asyncio.to_thread(Image.open, page_image_path)
        page_image.load()
        logger.info("Loaded page %d image: %s (%s)", page_num, page_image_path.name, page_image.size)

        # --- Step 2: Extract text layer from original PDF ---
        await on_progress("extracting", "Extracting text layer...")
        text_layer = await self._ensure_original_text_layer(session_path, page_num)

        # --- Step 3: Send to AI model ---
        await on_progress("generating", "Sending to AI model...")
        result_image = await self._provider.edit_image(page_image, prompt)
        logger.info("AI returned image: %s", result_image.size)

        # --- Step 4: Save new version ---
        await on_progress("processing", "Processing result...")
        versions = metadata["current_page_versions"]
        current_version = int(versions.get(str(page_num), 0))
        new_version = current_version + 1

        new_image_path = session_path / "pages" / f"page_{page_num}_v{new_version}.png"
        await asyncio.to_thread(result_image.save, new_image_path, "PNG")
        logger.info("Saved new version: %s", new_image_path.name)

        # --- Step 5: Text layer strategy ---
        await on_progress("text_layer", "Preserving text layer...")
        text_layer_preserved = await self._handle_text_layer(
            session_path, page_num, new_version, result_image, text_layer, prompt,
        )

        # --- Step 6: Update metadata and finish ---
        await on_progress("complete", "Edit complete")

        versions[str(page_num)] = new_version
        metadata["current_page_versions"] = versions
        self._sessions.update_metadata(session_id, metadata)

        self._save_edit_record(
            session_path, page_num, new_version, prompt, text_layer_preserved,
        )

        elapsed_ms = (time.monotonic() - t_start) * 1000
        logger.info("Edit pipeline completed in %.0fms", elapsed_ms)

        return EditResult(
            session_id=session_id,
            page_num=page_num,
            version=new_version,
            processing_time_ms=round(elapsed_ms, 1),
            text_layer_preserved=text_layer_preserved,
        )

    # ------------------------------------------------------------------
    # Text layer helpers
    # ------------------------------------------------------------------

    async def _ensure_original_text_layer(
        self, session_path: Path, page_num: int,
    ) -> dict:
        """Extract and cache the original PDF text layer for a page."""
        cache_path = session_path / "edits" / f"page_{page_num}_text_layer.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())

        pdf_path = session_path / "original.pdf"
        text_data = await asyncio.to_thread(pdf_service.extract_text, pdf_path, page_num)
        cache_path.write_text(json.dumps(text_data))
        return text_data

    async def _handle_text_layer(
        self,
        session_path: Path,
        page_num: int,
        version: int,
        result_image: Image.Image,
        original_text_layer: dict,
        prompt: str,
    ) -> bool:
        """Determine whether the original text layer can be preserved.

        Phase 1 simplified strategy:
        - Ask the vision model whether the edit changes text content.
        - If text is unchanged, keep the original text layer.
        - If text changed, run Tesseract OCR to generate a replacement.

        Returns True if the original text layer was preserved.
        """
        try:
            changes_text = await self._check_text_changed(prompt)
        except Exception:
            logger.warning("Text-change check failed; falling back to OCR", exc_info=True)
            changes_text = True

        layer_path = session_path / "edits" / f"page_{page_num}_v{version}_text.json"

        if not changes_text:
            layer_path.write_text(json.dumps(original_text_layer))
            return True

        ocr_text = await self._ocr_image(result_image)
        layer_path.write_text(json.dumps({"full_text": ocr_text, "blocks": []}))
        return False

    async def _check_text_changed(self, prompt: str) -> bool:
        """Heuristic check: does the edit prompt imply text content changes?

        Phase 1 uses keyword heuristics rather than an extra API call to keep
        latency low.  Phase 2 can upgrade to a vision-model analysis.
        """
        text_keywords = [
            "text", "title", "heading", "word", "sentence", "paragraph",
            "label", "caption", "rename", "rewrite", "rephrase", "change",
            "replace", "say", "spell", "write", "font", "typing",
        ]
        lower = prompt.lower()
        return any(kw in lower for kw in text_keywords)

    async def _ocr_image(self, image: Image.Image) -> str:
        """Run Tesseract OCR on an image, returning extracted text."""
        try:
            import pytesseract
            text: str = await asyncio.to_thread(pytesseract.image_to_string, image)
            return text.strip()
        except Exception:
            logger.warning("OCR failed; returning empty text layer", exc_info=True)
            return ""

    # ------------------------------------------------------------------
    # Edit history
    # ------------------------------------------------------------------

    def _save_edit_record(
        self,
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

        history.append({
            "version": version,
            "prompt": prompt,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "text_layer_preserved": text_layer_preserved,
        })
        history_path.write_text(json.dumps(history))

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
