"""Shared pytest fixtures for backend tests.

Provides SessionManager, StateManager, PdfEditor, and helper utilities
so individual test files can stay focused on assertions.
"""

import shutil
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from app.services import pdf_service
from app.services.pdf_editor import PdfEditor
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

SHARED_STORAGE = Path(__file__).parent / "test_data_shared"


def make_pdf(pages: int = 2, text_fn=None) -> bytes:
    """Generate a simple multi-page PDF in memory and return bytes.

    Args:
        pages: Number of pages to generate.
        text_fn: Optional callback ``(canvas, page_num)`` to draw custom content.
                 If None, draws default placeholder text.
    """
    path = SHARED_STORAGE / "_tmp_fixture.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    for i in range(1, pages + 1):
        if text_fn:
            text_fn(c, i)
        else:
            c.setFont("Helvetica", 14)
            c.drawString(72, 700, f"Page {i} original content")
            c.drawString(72, 680, f"Version 0 — this is page {i}")
        c.showPage()
    c.save()
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data


@pytest.fixture
def shared_storage():
    """Provide a clean shared storage directory, cleaned up after test."""
    if SHARED_STORAGE.exists():
        shutil.rmtree(SHARED_STORAGE)
    SHARED_STORAGE.mkdir(parents=True, exist_ok=True)
    yield SHARED_STORAGE
    if SHARED_STORAGE.exists():
        shutil.rmtree(SHARED_STORAGE)


@pytest.fixture
def session_mgr(shared_storage) -> SessionManager:
    return SessionManager(shared_storage)


@pytest.fixture
def state_mgr(session_mgr) -> StateManager:
    return StateManager(session_mgr)


@pytest.fixture
def editor(session_mgr) -> PdfEditor:
    return PdfEditor(session_manager=session_mgr)


@pytest.fixture
def session_with_pages(session_mgr):
    """Create a session with a 2-page PDF that has renderable pages.

    Returns ``(session_id, session_path)``.
    """
    pdf_bytes = make_pdf(pages=2)
    session_id = session_mgr.create_session(pdf_bytes, "test.pdf", 2)
    session_path = session_mgr.get_session_path(session_id)
    pdf_path = session_path / "original.pdf"
    pages_dir = session_path / "pages"
    pages_dir.mkdir(exist_ok=True)
    pdf_service.render_all_pages(pdf_path, pages_dir)
    return session_id, session_path
