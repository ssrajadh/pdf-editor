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
    Server sends:
      {"type": "progress", "stage": "planning", "message": "..."}
      {"type": "progress", "stage": "planned", "message": "...", "plan": {...}}
      {"type": "progress", "stage": "programmatic", "message": "...", "op_index": 0}
      {"type": "progress", "stage": "generating", "message": "...", "op_index": 1}
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

    async def send_progress(
        stage: str, message: str, extra: dict | None = None,
    ) -> None:
        payload: dict = {
            "type": "progress",
            "stage": stage,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if extra:
            if "plan" in extra:
                payload["plan"] = extra["plan"]
            if "op_index" in extra:
                payload["op_index"] = extra["op_index"]
        await websocket.send_json(payload)

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

            try:
                result = await edit_engine.execute_edit(
                    session_id, int(page_num), prompt, send_progress,
                )
                await websocket.send_json({
                    "type": "complete",
                    "result": result.model_dump(),
                })
            except RuntimeError as exc:
                err = str(exc)
                if "already in progress" in err:
                    user_msg = "An edit is already in progress for this session. Please wait."
                elif "Content blocked" in err or "safety filters" in err:
                    user_msg = "The AI model filtered this edit request. Try rephrasing your instruction."
                elif "failed after" in err and "attempts" in err:
                    user_msg = "The AI model is temporarily unavailable. Please try again in a moment."
                elif "no image" in err.lower():
                    user_msg = "The AI model didn't return an edited image. Try rephrasing your instruction to be more specific."
                else:
                    user_msg = err
                    logger.error("Edit failed: %s", exc, exc_info=True)

                await websocket.send_json({"type": "error", "message": user_msg})
            except Exception as exc:
                logger.error("Edit failed: %s", exc, exc_info=True)
                await websocket.send_json({
                    "type": "error",
                    "message": "An unexpected error occurred. Please try again.",
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
