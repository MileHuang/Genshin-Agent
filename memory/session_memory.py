"""Human-readable, short-lived working memory stored as Markdown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re


DEFAULT_SESSION_DIRECTORY = Path(__file__).resolve().parents[1] / "data" / "sessions"


@dataclass(frozen=True)
class SessionMemory:
    """Temporary planning context for one user session."""

    session_id: str
    user_id: str
    timezone_name: str
    expires_at: datetime
    body: str

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= datetime.now(timezone.utc)


class SessionMemoryStore:
    """Store temporary context in inspectable Markdown files.

    Session files are intentionally not part of the long-term RAG index.  A
    caller may explicitly promote useful information through the long-term
    memory writer once the session is complete.
    """

    def __init__(self, root: str | Path = DEFAULT_SESSION_DIRECTORY) -> None:
        self.root = Path(root)

    def save(
        self,
        *,
        user_id: str,
        session_id: str,
        timezone_name: str,
        expires_at: datetime,
        goal: str,
        temporary_constraints: list[str] | None = None,
        notes: str = "",
    ) -> SessionMemory:
        clean_user_id = _safe_identifier("user_id", user_id)
        clean_session_id = _safe_identifier("session_id", session_id)
        if not isinstance(timezone_name, str) or not timezone_name.strip():
            raise ValueError("timezone_name must not be blank")
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError("expires_at must include a timezone")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("goal must not be blank")
        constraints = temporary_constraints or []
        if any(not isinstance(item, str) or not item.strip() for item in constraints):
            raise ValueError("temporary_constraints must contain non-empty strings")

        memory = SessionMemory(
            session_id=clean_session_id,
            user_id=clean_user_id,
            timezone_name=timezone_name.strip(),
            expires_at=expires_at.astimezone(timezone.utc),
            body=_render_body(goal.strip(), [item.strip() for item in constraints], notes),
        )
        path = self._path(clean_user_id, clean_session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(memory), encoding="utf-8")
        return memory

    def load_latest_active(self, user_id: str) -> SessionMemory | None:
        clean_user_id = _safe_identifier("user_id", user_id)
        directory = self.root / clean_user_id
        if not directory.exists():
            return None

        candidates: list[tuple[float, SessionMemory]] = []
        for path in directory.glob("*.md"):
            memory = self._read(path)
            if memory is not None and not memory.is_expired:
                candidates.append((path.stat().st_mtime, memory))
        return max(candidates, default=(0.0, None), key=lambda item: item[0])[1]

    def _path(self, user_id: str, session_id: str) -> Path:
        return self.root / user_id / f"{session_id}.md"

    @staticmethod
    def _read(path: Path) -> SessionMemory | None:
        try:
            text = path.read_text(encoding="utf-8")
            front_matter, body = text.split("---\n", 2)[1:]
            fields = dict(
                line.split(": ", 1)
                for line in front_matter.splitlines()
                if ": " in line
            )
            expires_at = datetime.fromisoformat(fields["expires_at"])
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                return None
            return SessionMemory(
                session_id=_safe_identifier("session_id", fields["session_id"]),
                user_id=_safe_identifier("user_id", fields["user_id"]),
                timezone_name=fields["timezone"],
                expires_at=expires_at,
                body=body.strip(),
            )
        except (KeyError, OSError, ValueError):
            return None


def _render_markdown(memory: SessionMemory) -> str:
    return (
        "---\n"
        f"session_id: {memory.session_id}\n"
        f"user_id: {memory.user_id}\n"
        f"timezone: {memory.timezone_name}\n"
        f"expires_at: {memory.expires_at.isoformat()}\n"
        "---\n\n"
        f"{memory.body.strip()}\n"
    )


def _render_body(goal: str, constraints: list[str], notes: str) -> str:
    constraint_lines = "\n".join(f"- {item}" for item in constraints) or "- None recorded."
    note_block = notes.strip() or "None recorded."
    return (
        "# Current goal\n"
        f"{goal}\n\n"
        "# Temporary constraints\n"
        f"{constraint_lines}\n\n"
        "# Notes\n"
        f"{note_block}"
    )


def _safe_identifier(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be blank")
    clean_value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", clean_value):
        raise ValueError(f"{name} may only contain letters, numbers, '.', '_' and '-'")
    return clean_value
