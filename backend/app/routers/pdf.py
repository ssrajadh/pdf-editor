from fastapi import APIRouter, UploadFile, File

router = APIRouter()


@router.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Upload a PDF file and create a session."""
    # TODO: implement PDF upload handling
    return {"filename": file.filename, "status": "uploaded"}


@router.get("/{session_id}/pages")
async def get_pages(session_id: str):
    """Retrieve page info for a session."""
    # TODO: implement page retrieval
    return {"session_id": session_id, "pages": []}
