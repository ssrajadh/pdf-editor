from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import FileResponse

from app.config import settings
from app.models.schemas import UploadResponse, PageTextResponse, SessionInfoResponse
from app.services import pdf_service
from app.storage.session import SessionManager

router = APIRouter()
session_mgr = SessionManager(settings.storage_path)


@router.post("/upload", response_model=UploadResponse)
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF file, create a session, and render all pages."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    pdf_bytes = await file.read()

    max_bytes = settings.max_file_size_mb * 1024 * 1024
    if len(pdf_bytes) > max_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_file_size_mb}MB limit")

    # Get page count from raw bytes by writing to a temp location first
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    try:
        page_count = pdf_service.get_page_count(tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Invalid or corrupt PDF file")

    tmp_path.unlink(missing_ok=True)

    session_id = session_mgr.create_session(pdf_bytes, file.filename, page_count)
    session_path = session_mgr.get_session_path(session_id)
    pdf_path = session_path / "original.pdf"
    pages_dir = session_path / "pages"

    await pdf_service.render_all_pages_async(pdf_path, pages_dir)

    return UploadResponse(session_id=session_id, filename=file.filename, page_count=page_count)


@router.get("/{session_id}/page/{page_num}/image")
async def get_page_image(session_id: str, page_num: int):
    """Return the rendered PNG image for a page."""
    try:
        session_path = session_mgr.get_session_path(session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        image_path = pdf_service.get_page_image_path(session_path, page_num)
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
