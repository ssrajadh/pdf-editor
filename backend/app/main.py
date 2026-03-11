from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import pdf, edit

app = FastAPI(title="PDF Editor", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(pdf.router, prefix="/api/pdf", tags=["pdf"])
app.include_router(edit.router, prefix="/api/edit", tags=["edit"])


@app.get("/health")
async def health():
    return {"status": "ok"}
