"""Edit router — WebSocket for real-time edits, REST for history/revert."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.config import settings
from app.models.schemas import EditResult, EditVersion
from app.services.edit_engine import EditEngine
from app.services.model_provider import ProviderFactory
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()

session_mgr = SessionManager(settings.storage_path)

_provider = ProviderFactory.get_provider(
    settings.model_provider,
    settings.gemini_api_key,
)

edit_engine = EditEngine(
    session_manager=session_mgr,
    model_provider=_provider,
)


@router.websocket("/ws/{session_id}")
async def edit_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for submitting edits and receiving progress.

    Client sends:  {"type": "edit", "page_num": 1, "prompt": "..."}
    Server sends:  {"type": "progress", "stage": "...", "message": "..."}
                   {"type": "complete", "result": {...}}
                   {"type": "error", "message": "..."}
    """
    await websocket.accept()

    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close(code=4004)
        return

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            if msg.get("type") != "edit":
                await websocket.send_json({
                    "type": "error",
                    "message": f"Unknown message type: {msg.get('type')}",
                })
                continue

            page_num = msg.get("page_num")
            prompt = msg.get("prompt")
            if not page_num or not prompt:
                await websocket.send_json({
                    "type": "error",
                    "message": "page_num and prompt are required",
                })
                continue

            async def send_progress(stage: str, message: str) -> None:
                await websocket.send_json({
                    "type": "progress",
                    "stage": stage,
                    "message": message,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })

            try:
                result = await edit_engine.execute_edit(
                    session_id, int(page_num), prompt, send_progress,
                )
                await websocket.send_json({
                    "type": "complete",
                    "result": result.model_dump(),
                })
            except RuntimeError as exc:
                if "already in progress" in str(exc):
                    await websocket.send_json({
                        "type": "error",
                        "message": "An edit is already in progress for this session. Please wait.",
                    })
                else:
                    logger.error("Edit failed: %s", exc, exc_info=True)
                    await websocket.send_json({
                        "type": "error",
                        "message": str(exc),
                    })
            except Exception as exc:
                logger.error("Edit failed: %s", exc, exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": f"Edit failed: {exc}",
                })

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for session %s", session_id)


@router.get(
    "/{session_id}/page/{page_num}/history",
    response_model=list[EditVersion],
)
async def get_edit_history(session_id: str, page_num: int):
    """Return the edit history for a page."""
    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    return await edit_engine.get_edit_history(session_id, page_num)


@router.post(
    "/{session_id}/page/{page_num}/revert/{version}",
    response_model=EditResult,
)
async def revert_page(session_id: str, page_num: int, version: int):
    """Revert a page to a previous version."""
    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        return await edit_engine.revert_to_version(session_id, page_num, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
