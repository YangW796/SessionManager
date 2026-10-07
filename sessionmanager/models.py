from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .utils import safe_size


@dataclass(frozen=True)
class Session:
    platform: str
    session_id: str
    path: Path
    started_at: datetime | None = None
    cwd: str | None = None
    title: str | None = None
    files: tuple[Path, ...] = field(default_factory=tuple)
    prompt: str | None = None
    response: str | None = None
    updated_at: datetime | None = None
    archived: bool = False
    owned_dirs: tuple[Path, ...] = ()
    rollout_ids: tuple[str, ...] = ()
    history_bases: tuple[str, ...] = ()
    discovery_roots: tuple[Path, ...] = ()
    blocked_reasons: tuple[str, ...] = ()
    # Device/inode/size/mtime snapshots prevent deleting changed or replaced data.
    snapshots: tuple[tuple[Path, tuple[int, int, int, int]], ...] = ()

    @property
    def display_id(self) -> str:
        return self.session_id.removeprefix("session_")[:12]

    @property
    def size(self) -> int:
        return safe_size(self.files)
