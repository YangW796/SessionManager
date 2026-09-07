from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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

    @property
    def display_id(self) -> str:
        return self.session_id[:12]

    @property
    def size(self) -> int:
        return sum(p.stat().st_size for p in self.files if p.is_file())
