import asyncio
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import pdf, edit
from app.deps import session_mgr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pdf_editor")

app = FastAPI(title="Nano PDF Studio", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    t0 = time.monotonic()
    response = await call_next(request)
    elapsed = (time.monotonic() - t0) * 1000

    path = request.url.path
    if path != "/health":
        logger.info(
            "%s %s %d %.0fms",
            request.method, path, response.status_code, elapsed,
        )
    return response


app.include_router(pdf.router, prefix="/api/pdf", tags=["pdf"])
app.include_router(edit.router, prefix="/api/edit", tags=["edit"])


@app.get("/health")
async def health():
    return {"status": "ok"}


async def _cleanup_loop():
    """Delete sessions older than 24 hours, runs every hour."""
    while True:
        await asyncio.sleep(3600)
        try:
            deleted = session_mgr.cleanup_old_sessions(max_age_hours=24)
            if deleted:
                logger.info("Cleaned up %d expired sessions", deleted)
        except Exception:
            logger.warning("Session cleanup failed", exc_info=True)


@app.on_event("startup")
async def startup():
    asyncio.create_task(_cleanup_loop())
