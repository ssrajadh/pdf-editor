"""Session and file management."""

from pathlib import Path
import uuid


class SessionManager:
    """Manages upload sessions and file storage."""

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def create_session(self) -> str:
        """Create a new session and return its ID."""
        session_id = uuid.uuid4().hex
        session_dir = self._storage_path / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_id

    def get_session_path(self, session_id: str) -> Path:
        """Get the storage path for a session."""
        return self._storage_path / session_id
