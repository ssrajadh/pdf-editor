from pydantic import BaseModel


class TextBlock(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    font_name: str = ""
    font_size: float = 0.0


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int


class PageTextResponse(BaseModel):
    session_id: str
    page_number: int
    full_text: str
    blocks: list[TextBlock]


class SessionInfoResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int
    created_at: str
    current_page_versions: dict[int, int]


class PageInfo(BaseModel):
    page_number: int
    width: float
    height: float


class EditRequest(BaseModel):
    session_id: str
    instruction: str
    page_number: int | None = None


class EditProgress(BaseModel):
    session_id: str
    status: str
    progress: float = 0.0
    message: str = ""


class EditResult(BaseModel):
    session_id: str
    status: str
    modified_pages: list[int] = []
