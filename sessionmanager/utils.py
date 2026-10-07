from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def has_symlink(path: Path) -> bool:
    return any(part.is_symlink() for part in (path, *path.parents))


def file_snapshot(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def walk_files(root: Path) -> Iterable[Path]:
    """Walk without following directory links, surfacing unreadable directories."""
    if has_symlink(root):
        return
    try:
        root.stat()
    except FileNotFoundError:
        return
    def onerror(error: OSError) -> None:
        raise error
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=onerror):
        dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if not path.is_symlink() and path.is_file():
                yield path


def unique_paths(paths: Iterable[Path]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        try:
            # Do not follow links while constructing a destructive file set.
            # Unlinking the link itself is safe; unlinking its target is not.
            absolute = path.absolute()
        except OSError:
            continue
        if has_symlink(absolute) or absolute in seen or not absolute.is_file():
            continue
        seen.add(absolute)
        result.append(absolute)
    return tuple(sorted(result))


def env_roots(name: str, defaults: Iterable[Path]) -> tuple[Path, ...]:
    value = os.environ.get(name)
    roots = [Path(item).expanduser() for item in value.split(os.pathsep) if item] if value else list(defaults)
    return tuple(dict.fromkeys(path.absolute() for path in roots))


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def json_object(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def safe_size(paths: Iterable[Path]) -> int:
    total = 0
    for path in paths:
        try:
            if not has_symlink(path):
                total += path.stat().st_size
        except OSError:
            pass
    return total
