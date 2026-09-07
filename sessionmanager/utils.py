from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


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
        if absolute.is_symlink() or absolute in seen or not absolute.is_file():
            continue
        seen.add(absolute)
        result.append(absolute)
    return tuple(result)


def env_roots(name: str, defaults: Iterable[Path]) -> tuple[Path, ...]:
    value = os.environ.get(name)
    roots = [Path(item).expanduser() for item in value.split(os.pathsep)] if value else list(defaults)
    return tuple(path for path in roots if path.exists() and path.is_dir())


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


def first_json_objects(path: Path, limit: int = 32) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, line in enumerate(handle):
                if index >= limit:
                    break
                item = json_object(line)
                if item:
                    objects.append(item)
    except (OSError, UnicodeError):
        pass
    return objects


def safe_size(paths: Iterable[Path]) -> int:
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total
