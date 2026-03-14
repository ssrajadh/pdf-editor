"""Edit router — WebSocket for real-time edits, REST for history/revert."""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from app.config import settings
from app.models.schemas import (
    EditResult,
    EditVersion,
    ExecutionPlan,
    PageHistoryResponse,
    PageSnapshotResponse,
    PlanPreviewRequest,
    RevertRequest,
)
from app.services.edit_engine import EditEngine
from app.services.model_provider import ProviderFactory
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()

session_mgr = SessionManager(settings.storage_path)

_provider = ProviderFactory.get_provider(
    settings.model_provider,
    settings.gemini_api_key,
)

state_mgr = StateManager(session_mgr)

edit_engine = EditEngine(
    session_manager=session_mgr,
    model_provider=_provider,
    state_manager=state_mgr,
)


def _snapshot_to_response(
    snap, session_id: str, page_num: int, is_current: bool,
) -> PageSnapshotResponse:
    """Convert an internal PageSnapshot to an API response."""
    ops = None
    if snap.execution_result and snap.execution_result.operations:
        ops = snap.execution_result.operations

    image_url = (
        f"/api/pdf/{session_id}/page/{page_num}/image?step={snap.step}"
    )

    return PageSnapshotResponse(
        step=snap.step,
        timestamp=snap.timestamp,
        prompt=snap.prompt,
        plan_summary=snap.plan_summary,
        operations_summary=ops,
        image_url=image_url,
        text_layer_source=snap.text_layer_source,
        is_current=is_current,
    )


@router.websocket("/ws/{session_id}")
async def edit_websocket(websocket: WebSocket, session_id: str):
    """WebSocket endpoint for submitting edits and receiving progress.

    Client sends:  {"type": "edit", "page_num": 1, "prompt": "...", "force_visual": false}
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
            force_visual = bool(msg.get("force_visual", False))
            if not page_num or not prompt:
                await websocket.send_json({
                    "type": "error",
                    "message": "page_num and prompt are required",
                })
                continue

            try:
                metadata = session_mgr.get_metadata(session_id)
                metadata["last_active_page"] = int(page_num)
                metadata["last_active_at"] = datetime.now(timezone.utc).isoformat()
                session_mgr.update_metadata(session_id, metadata)
            except Exception:
                pass

            try:
                result = await edit_engine.execute_edit(
                    session_id, int(page_num), prompt, send_progress, force_visual,
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


@router.post(
    "/{session_id}/page/{page_num}/plan-preview",
    response_model=ExecutionPlan,
)
async def plan_preview(
    session_id: str, page_num: int, body: PlanPreviewRequest,
):
    """Generate an execution plan without running it.

    Useful for debugging, testing, and demoing the planner logic.
    """
    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        metadata = session_mgr.get_metadata(session_id)
        metadata["last_active_page"] = int(page_num)
        metadata["last_active_at"] = datetime.now(timezone.utc).isoformat()
        session_mgr.update_metadata(session_id, metadata)
    except Exception:
        pass

    try:
        return await edit_engine.preview_plan(session_id, page_num, body.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Plan preview failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Plan preview failed")


# ------------------------------------------------------------------
# History endpoint — returns state-stack snapshots
# ------------------------------------------------------------------


@router.get(
    "/{session_id}/page/{page_num}/history",
    response_model=PageHistoryResponse,
)
async def get_page_history(session_id: str, page_num: int):
    """Return the full snapshot history for a page."""
    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    stack = state_mgr.get_stack(session_id, page_num)
    current_step = stack.current_step

    snapshots = [
        _snapshot_to_response(snap, session_id, page_num, snap.step == current_step)
        for snap in stack.history
    ]

    return PageHistoryResponse(
        session_id=session_id,
        page_num=page_num,
        current_step=current_step,
        total_steps=len(stack.snapshots),
        snapshots=snapshots,
    )


# ------------------------------------------------------------------
# Revert endpoint — restore to any step
# ------------------------------------------------------------------


@router.post(
    "/{session_id}/page/{page_num}/revert",
    response_model=PageSnapshotResponse,
)
async def revert_page(
    session_id: str, page_num: int, body: RevertRequest,
):
    """Revert a page to a previous step.

    Restores only this page in the working PDF — other pages are untouched.
    Returns the restored snapshot.
    """
    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        restored = state_mgr.restore_to_step(session_id, page_num, body.step)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return _snapshot_to_response(restored, session_id, page_num, is_current=True)


# ------------------------------------------------------------------
# Legacy revert endpoint — kept for backward compatibility
# ------------------------------------------------------------------


@router.post(
    "/{session_id}/page/{page_num}/revert/{version}",
    response_model=EditResult,
)
async def revert_page_legacy(session_id: str, page_num: int, version: int):
    """Revert a page to a previous version (legacy endpoint)."""
    try:
        session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        return await edit_engine.revert_to_version(session_id, page_num, version)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
