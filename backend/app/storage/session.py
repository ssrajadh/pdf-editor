"""Session and file management."""

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


class SessionManager:
    """Manages upload sessions and file storage."""

    def __init__(self, storage_path: Path):
        self._storage_path = storage_path
        self._storage_path.mkdir(parents=True, exist_ok=True)

    def create_session(self, pdf_bytes: bytes, filename: str, page_count: int) -> str:
        """Create a new session, store the PDF, and return session ID."""
        session_id = uuid.uuid4().hex
        session_dir = self._storage_path / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "pages").mkdir(exist_ok=True)
        (session_dir / "edits").mkdir(exist_ok=True)

        (session_dir / "original.pdf").write_bytes(pdf_bytes)

        metadata = {
            "session_id": session_id,
            "filename": filename,
            "page_count": page_count,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "current_page_versions": {str(i): 0 for i in range(1, page_count + 1)},
        }
        (session_dir / "metadata.json").write_text(json.dumps(metadata))

        return session_id

    def get_session_path(self, session_id: str) -> Path:
        """Get the storage path for a session."""
        path = self._storage_path / session_id
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        return path

    def get_metadata(self, session_id: str) -> dict:
        """Load session metadata."""
        meta_path = self.get_session_path(session_id) / "metadata.json"
        return json.loads(meta_path.read_text())

    def update_metadata(self, session_id: str, metadata: dict) -> None:
        """Update session metadata."""
        meta_path = self.get_session_path(session_id) / "metadata.json"
        meta_path.write_text(json.dumps(metadata))

    def cleanup_session(self, session_id: str) -> None:
        """Delete all session data."""
        path = self._storage_path / session_id
        if path.exists():
            shutil.rmtree(path)
