import json
import logging
from typing import Optional, Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from fastapi.responses import FileResponse

from app.config import settings
from datetime import datetime, timezone
from app.models.schemas import (
    TextBlock,
    UploadResponse,
    PageTextResponse,
    SessionInfoResponse,
    TextLayerResponse,
    SessionListItem,
    SessionStateResponse,
    SessionStatePage,
)
from app.services import pdf_service
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()
session_mgr = SessionManager(settings.storage_path)
state_mgr = StateManager(session_mgr)


def _touch_session_page(session_id: str, page_num: int) -> None:
    try:
        metadata = session_mgr.get_metadata(session_id)
    except FileNotFoundError:
        return
    metadata["last_active_page"] = page_num
    metadata["last_active_at"] = datetime.now(timezone.utc).isoformat()
    session_mgr.update_metadata(session_id, metadata)


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF file, create a session, and render all pages."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    pdf_bytes = await file.read()

    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_file_size_mb}MB limit")

    import tempfile
    from pathlib import Path
    import pikepdf

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    try:
        with pikepdf.open(tmp_path) as test_pdf:
            if test_pdf.is_encrypted:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(status_code=400, detail="Encrypted PDFs are not supported. Please remove the password and try again.")
    except pikepdf.PasswordError:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="This PDF is password-protected. Please remove the password and try again.")
    except HTTPException:
        raise
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid or corrupt PDF file")

    try:
        page_count = pdf_service.get_page_count(tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Failed to read PDF pages. The file may be corrupt.")

    tmp_path.unlink(missing_ok=True)

    session_id = session_mgr.create_session(pdf_bytes, file.filename, page_count)
    session_path = session_mgr.get_session_path(session_id)
    pdf_path = session_path / "original.pdf"
    pages_dir = session_path / "pages"

    rendered_pages = await pdf_service.render_all_pages_async(pdf_path, pages_dir)

    # Initialize state stack for each page with step 0 snapshots
    for i in range(1, page_count + 1):
        try:
            text_data = pdf_service.extract_text(pdf_path, i)
            text_blocks = [
                TextBlock(**b) for b in text_data["blocks"]
            ]
        except Exception:
            text_blocks = None

        image_filename = str(rendered_pages[i - 1]) if i <= len(rendered_pages) else ""
        state_mgr.initialize_page(session_id, i, image_filename, text_blocks)

    return UploadResponse(session_id=session_id, filename=file.filename, page_count=page_count)


@router.get("/sessions", response_model=list[SessionListItem])
async def list_sessions():
    """Return active sessions (last 24h), sorted by last_edit_at desc."""
    sessions = session_mgr.list_sessions(max_age_hours=24)
    items: list[SessionListItem] = []
    for meta in sessions:
        last_edit_at = meta.get("last_edit_at") or meta.get("created_at")
        items.append(SessionListItem(
            session_id=meta["session_id"],
            filename=meta.get("filename", "document.pdf"),
            page_count=meta.get("page_count", 0),
            created_at=meta.get("created_at", ""),
            last_edit_at=last_edit_at or "",
            total_edits=int(meta.get("total_edits", 0)),
        ))

    items.sort(key=lambda i: i.last_edit_at, reverse=True)
    return items


@router.get("/{session_id}/state", response_model=SessionStateResponse)
async def get_session_state(session_id: str):
    """Return full session state for frontend restore."""
    try:
        metadata = session_mgr.get_metadata(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    page_count = int(metadata.get("page_count", 0))
    current_page = int(metadata.get("last_active_page", 1))

    pages: list[SessionStatePage] = []
    conversations: dict[str, list[dict]] = {}

    for page_num in range(1, page_count + 1):
        stack = state_mgr.get_stack(session_id, page_num)
        current_step = stack.current_step if stack.snapshots else 0
        total_steps = len(stack.snapshots)

        has_program = False
        has_visual = False
        for snap in stack.snapshots:
            if not snap.execution_result:
                continue
            for op in snap.execution_result.operations:
                if op.path == "programmatic":
                    has_program = True
                if op.path in ("visual", "fallback_visual"):
                    has_visual = True

        edit_types: list[Literal["programmatic", "visual"]] = []
        if has_program:
            edit_types.append("programmatic")
        if has_visual:
            edit_types.append("visual")

        image_url = (
            f"/api/pdf/{session_id}/page/{page_num}/image?step={current_step}"
        )

        pages.append(SessionStatePage(
            page_num=page_num,
            current_step=current_step,
            total_steps=total_steps,
            image_url=image_url,
            has_edits=current_step > 0,
            edit_types=edit_types,
        ))

        messages: list[dict] = []
        for snap in stack.snapshots:
            if snap.step == 0 or not snap.prompt:
                continue
            ts = snap.timestamp.isoformat() if isinstance(snap.timestamp, datetime) else str(snap.timestamp)
            messages.append({
                "id": f"restored-{page_num}-{snap.step}-user",
                "role": "user",
                "content": snap.prompt,
                "timestamp": ts,
            })
            if snap.execution_result:
                result = snap.execution_result.model_dump(mode="json")
            else:
                result = None
            messages.append({
                "id": f"restored-{page_num}-{snap.step}-assistant",
                "role": "assistant",
                "content": snap.plan_summary or "Edit applied",
                "timestamp": ts,
                "result": result,
            })

        conversations[str(page_num)] = messages

    _touch_session_page(session_id, current_page)

    return SessionStateResponse(
        session_id=metadata["session_id"],
        filename=metadata.get("filename", "document.pdf"),
        page_count=page_count,
        current_page=current_page,
        pages=pages,
        conversations=conversations,
    )


@router.get("/{session_id}/page/{page_num}/image")
async def get_page_image(
    session_id: str,
    page_num: int,
    v: Optional[int] = Query(None, description="Specific version to retrieve (legacy)"),
    step: Optional[int] = Query(None, description="State-stack step number"),
):
    """Return the rendered PNG image for a page.

    Resolution order:
    1. ?step=N  — serve from the state-stack snapshot at step N
    2. ?v=N     — serve the version-specific file (legacy)
    3. default  — current step's image from state stack, fallback to latest version
    """
    try:
        session_path = session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    _touch_session_page(session_id, page_num)

    # --- step-based lookup via state stack ---
    if step is not None or v is None:
        try:
            stack = state_mgr.get_stack(session_id, page_num)
            if stack.snapshots:
                if step is not None:
                    snap = stack.get(step)
                    if snap is None:
                        raise HTTPException(
                            status_code=404,
                            detail=f"Step {step} not found for page {page_num}",
                        )
                else:
                    snap = stack.current

                from pathlib import Path as _P
                img = _P(snap.image_filename)
                if img.exists():
                    return FileResponse(
                        img,
                        media_type="image/png",
                        headers={"Cache-Control": "public, max-age=3600"},
                    )
        except (IndexError, KeyError):
            pass  # fall through to version-based lookup

    # --- version-based lookup (legacy / fallback) ---
    version = str(v) if v is not None else "latest"
    try:
        image_path = pdf_service.get_page_image_path(session_path, page_num, version)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Image for page {page_num} not found")

    return FileResponse(
        image_path,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{session_id}/page/{page_num}/text", response_model=PageTextResponse)
async def get_page_text(session_id: str, page_num: int):
    """Return extracted text and block positions for a page."""
    try:
        session_path = session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    _touch_session_page(session_id, page_num)

    pdf_path = session_path / "original.pdf"

    try:
        result = pdf_service.extract_text(pdf_path, page_num)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return PageTextResponse(
        session_id=session_id,
        page_number=page_num,
        full_text=result["full_text"],
        blocks=result["blocks"],
    )


@router.get("/{session_id}/page/{page_num}/text-layer", response_model=TextLayerResponse)
async def get_text_layer(session_id: str, page_num: int):
    """Return the text layer status and content for the current version of a page."""
    try:
        session_path = session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    _touch_session_page(session_id, page_num)

    metadata = session_mgr.get_metadata(session_id)
    version = int(metadata["current_page_versions"].get(str(page_num), 0))

    if version == 0:
        pdf_path = session_path / "original.pdf"
        try:
            result = pdf_service.extract_text(pdf_path, page_num)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return TextLayerResponse(
            session_id=session_id,
            page_num=page_num,
            version=0,
            text_layer_preserved=True,
            full_text=result["full_text"],
        )

    version_layer = session_path / "edits" / f"page_{page_num}_v{version}_text.json"
    if version_layer.exists():
        data = json.loads(version_layer.read_text())
        return TextLayerResponse(
            session_id=session_id,
            page_num=page_num,
            version=version,
            text_layer_preserved=not data.get("stale", False),
            full_text=data.get("full_text", ""),
        )

    return TextLayerResponse(
        session_id=session_id,
        page_num=page_num,
        version=version,
        text_layer_preserved=False,
        full_text="",
    )


@router.get("/{session_id}/info", response_model=SessionInfoResponse)
async def get_session_info(session_id: str):
    """Return session metadata."""
    try:
        metadata = session_mgr.get_metadata(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    return SessionInfoResponse(
        session_id=metadata["session_id"],
        filename=metadata["filename"],
        page_count=metadata["page_count"],
        created_at=metadata["created_at"],
        current_page_versions={int(k): v for k, v in metadata["current_page_versions"].items()},
    )


@router.post("/{session_id}/export")
async def export_pdf(session_id: str):
    """Export the PDF with all edits merged in. Returns the file as a download."""
    try:
        session_path = session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    metadata = session_mgr.get_metadata(session_id)

    try:
        output_path = await pdf_service.export_pdf_async(session_path, metadata)
    except Exception as e:
        logger.error("Export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Export failed: {e}")

    original_name = metadata.get("filename", "document.pdf")
    stem = original_name.rsplit(".", 1)[0] if "." in original_name else original_name
    download_name = f"{stem}_edited.pdf"

    return FileResponse(
        output_path,
        media_type="application/pdf",
        filename=download_name,
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )
