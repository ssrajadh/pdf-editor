"""Shared singleton instances for routers.

Both pdf.py and edit.py need the same SessionManager and StateManager
so that in-memory caches stay consistent. This module provides a single
instance of each.
"""

from app.config import settings
from app.services.model_provider import ProviderFactory
from app.services.state_manager import StateManager
from app.storage.session import SessionManager

session_mgr = SessionManager(settings.storage_path)

state_mgr = StateManager(session_mgr)

model_provider = ProviderFactory.get_provider(
    settings.model_provider,
    settings.gemini_api_key,
)
