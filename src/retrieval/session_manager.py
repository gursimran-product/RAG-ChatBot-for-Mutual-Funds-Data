"""
Multi-Thread Session Manager — Component 10.

Maintains per-thread conversation history so each chat session is fully
isolated. Backed by an in-memory dict (dev/MVP); swap for Redis for prod.

Thread lifecycle:
  - Created on first message: new UUID assigned
  - Expires after SESSION_TTL_SECONDS of inactivity
  - History capped at MAX_HISTORY_TURNS per thread to bound prompt size
"""

import logging
import uuid
from datetime import datetime, timezone
from threading import Lock

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 30 * 60   # 30 minutes
MAX_HISTORY_TURNS = 10          # (user + assistant) pairs to keep


class _Session:
    __slots__ = ("thread_id", "history", "last_active")

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self.history: list[dict] = []  # [{"role": "user"|"assistant", "content": str}]
        self.last_active: datetime = datetime.now(timezone.utc)

    def add_turn(self, role: str, content: str) -> None:
        self.history.append({"role": role, "content": content})
        if len(self.history) > MAX_HISTORY_TURNS * 2:
            # Drop oldest pair (user + assistant) to stay within cap
            self.history = self.history[2:]
        self.last_active = datetime.now(timezone.utc)

    def is_expired(self) -> bool:
        elapsed = (datetime.now(timezone.utc) - self.last_active).total_seconds()
        return elapsed > SESSION_TTL_SECONDS


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}
        self._lock = Lock()

    def create_session(self) -> str:
        """Create a new session and return its thread_id UUID."""
        thread_id = str(uuid.uuid4())
        with self._lock:
            self._sessions[thread_id] = _Session(thread_id)
        logger.info("Session created: %s", thread_id)
        return thread_id

    def get_history(self, thread_id: str) -> list[dict]:
        """Return conversation history for a thread (empty list if not found/expired)."""
        with self._lock:
            session = self._sessions.get(thread_id)
            if session is None or session.is_expired():
                return []
            return list(session.history)

    def add_turn(self, thread_id: str, role: str, content: str) -> None:
        """Append a message turn to a thread. Creates the session if it doesn't exist."""
        with self._lock:
            if thread_id not in self._sessions or self._sessions[thread_id].is_expired():
                self._sessions[thread_id] = _Session(thread_id)
                logger.info("Session auto-created/renewed: %s", thread_id)
            self._sessions[thread_id].add_turn(role, content)

    def purge_expired(self) -> int:
        """Remove all expired sessions. Returns count removed."""
        with self._lock:
            expired = [tid for tid, s in self._sessions.items() if s.is_expired()]
            for tid in expired:
                del self._sessions[tid]
        if expired:
            logger.info("Purged %d expired session(s)", len(expired))
        return len(expired)

    @property
    def active_count(self) -> int:
        with self._lock:
            return sum(1 for s in self._sessions.values() if not s.is_expired())


# Module-level singleton — shared across FastAPI workers in the same process
sessions = SessionManager()
