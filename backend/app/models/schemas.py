from pydantic import BaseModel


class UploadResponse(BaseModel):
    session_id: str
    filename: str
    page_count: int


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
