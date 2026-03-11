from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.post("/submit")
async def submit_edit(request: dict):
    """Submit an edit request for processing."""
    # TODO: implement edit submission
    return {"status": "received"}


@router.websocket("/ws/{session_id}")
async def edit_websocket(websocket: WebSocket, session_id: str):
    """WebSocket for real-time edit progress updates."""
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({"session_id": session_id, "status": "connected"})
    except WebSocketDisconnect:
        pass
