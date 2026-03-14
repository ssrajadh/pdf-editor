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

    def get_working_pdf_path(self, session_id: str) -> Path:
        """Return the working PDF path, copying from original if it doesn't exist.

        The working PDF accumulates programmatic edits across the session.
        Visual edits don't modify it — they produce images directly.
        """
        session_path = self.get_session_path(session_id)
        working = session_path / "working.pdf"
        if not working.exists():
            shutil.copy2(session_path / "original.pdf", working)
        return working

    # ------------------------------------------------------------------
    # History / state-stack helpers
    # ------------------------------------------------------------------

    def get_history_path(self, session_id: str, page_num: int) -> Path:
        """Returns {session_dir}/history/page_{num}/, creating it if needed."""
        path = self.get_session_path(session_id) / "history" / f"page_{page_num}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def save_working_pdf_copy(self, session_id: str, step: int) -> Path:
        """Copy the current working.pdf (or original.pdf) to history/working_step_{step}.pdf"""
        session_path = self.get_session_path(session_id)
        history_dir = session_path / "history"
        history_dir.mkdir(parents=True, exist_ok=True)

        dest = history_dir / f"working_step_{step}.pdf"

        working = session_path / "working.pdf"
        source = working if working.exists() else session_path / "original.pdf"
        shutil.copy2(source, dest)
        return dest

    def restore_working_pdf_from_step(self, session_id: str, step: int) -> None:
        """Copy history/working_step_{step}.pdf back to working.pdf"""
        session_path = self.get_session_path(session_id)
        source = session_path / "history" / f"working_step_{step}.pdf"
        if not source.exists():
            raise FileNotFoundError(
                f"No stored PDF for step {step} in session {session_id}"
            )
        shutil.copy2(source, session_path / "working.pdf")

    def cleanup_session(self, session_id: str) -> None:
        """Delete all session data."""
        path = self._storage_path / session_id
        if path.exists():
            shutil.rmtree(path)

    def cleanup_old_sessions(self, max_age_hours: int = 24) -> int:
        """Delete sessions older than max_age_hours. Returns count of deleted sessions."""
        cutoff = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)
        deleted = 0

        for session_dir in self._storage_path.iterdir():
            if not session_dir.is_dir():
                continue
            meta_path = session_dir / "metadata.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text())
                created = datetime.fromisoformat(meta["created_at"]).timestamp()
                if created < cutoff:
                    shutil.rmtree(session_dir)
                    deleted += 1
            except Exception:
                continue

        return deleted
