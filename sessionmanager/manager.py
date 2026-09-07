from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import Session
from .providers import providers


@dataclass(frozen=True)
class DeleteResult:
    session: Session
    deleted: tuple[Path, ...]
    failed: tuple[tuple[Path, str], ...]


def discover(platform: str = "all") -> list[Session]:
    sessions: list[Session] = []
    for provider in providers(platform):
        sessions.extend(provider.discover())
    return sorted(sessions, key=lambda item: item.started_at or datetime.min, reverse=True)


def delete_session(session: Session) -> DeleteResult:
    deleted: list[Path] = []
    failed: list[tuple[Path, str]] = []
    for path in session.files:
        try:
            path.unlink()
            deleted.append(path)
        except OSError as error:
            failed.append((path, str(error)))
    # Remove now-empty UUID-owned directories only. Codex files normally live
    # in shared date directories, which are intentionally left untouched.
    if session.path.is_dir():
        directories = sorted((path for path in session.path.rglob("*") if path.is_dir() and not path.is_symlink()),
                             key=lambda path: len(path.parts), reverse=True)
        for directory in (*directories, session.path):
            try:
                directory.rmdir()
            except OSError:
                pass
    return DeleteResult(session, tuple(deleted), tuple(failed))
