from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import Session
from .providers import providers, refresh_sessions, sort_sessions
from .utils import file_snapshot, has_symlink


@dataclass(frozen=True)
class DeleteResult:
    session: Session
    deleted: tuple[Path, ...]
    failed: tuple[tuple[Path, str], ...]


def discover(platform: str = "all") -> list[Session]:
    return sort_sessions(session for provider in providers(platform) for session in provider.discover())


def _key(session: Session) -> tuple[str, str]:
    return session.platform, session.session_id


def deletion_blockers(selected: list[Session], inventory: list[Session]) -> dict[tuple[str, str], list[str]]:
    """Validate the whole selection without changing files (also used by dry-run)."""
    selected_keys = {_key(session) for session in selected}
    blocked = {}
    for session in selected:
        reasons = list(session.blocked_reasons)
        for other in inventory:
            if other.platform == session.platform and _key(other) not in selected_keys:
                if set(other.history_bases).intersection(session.rollout_ids):
                    reasons.append(f"Referenced by session {other.session_id} ({other.cwd or 'unknown project'})")
        if reasons:
            blocked[_key(session)] = reasons
    # Catch cycles before any mutation; normal fork histories form a DAG.
    remaining = list(selected)
    while remaining:
        leaves = [s for s in remaining if not any(
            _key(other) != _key(s) and other.platform == s.platform and
            set(other.history_bases).intersection(s.rollout_ids) for other in remaining)]
        if not leaves:
            for session in remaining:
                blocked.setdefault(_key(session), []).append("Cyclic history references; deletion refused")
            break
        remaining = [s for s in remaining if s not in leaves]
    return blocked


def _validate_files(session: Session) -> list[tuple[Path, str]]:
    failed = []
    snapshots = dict(session.snapshots)
    for path in session.files:
        try:
            if has_symlink(path) or not path.is_file():
                raise OSError("File missing or replaced by a symlink; refresh the list")
            if path in snapshots and file_snapshot(path) != snapshots[path]:
                raise OSError("File changed since selection; refresh the list")
        except OSError as error:
            failed.append((path, str(error)))
    return failed


def delete_session(session: Session) -> DeleteResult:
    if session.discovery_roots:
        inventory = refresh_sessions(session)
        current = next((item for item in inventory if _key(item) == _key(session)), None)
        if current is None:
            return DeleteResult(session, (), ((session.path, "Session no longer discoverable; refresh the list"),))
        if current.files != session.files:
            return DeleteResult(session, (), ((session.path, "Session file set changed; refresh the list"),))
        blockers = deletion_blockers([current], inventory).get(_key(session), [])
    else:
        blockers = list(session.blocked_reasons)
    if blockers:
        return DeleteResult(session, (), tuple((session.path, reason) for reason in blockers))
    failed = _validate_files(session)
    if failed:
        return DeleteResult(session, (), tuple(failed))
    deleted = []
    snapshots = dict(session.snapshots)
    for path in session.files:
        try:
            # Recheck just before unlinking, including linked parent directories.
            if has_symlink(path) or (path in snapshots and file_snapshot(path) != snapshots[path]):
                raise OSError("File changed during deletion; refresh the list")
            path.unlink()
            deleted.append(path)
        except OSError as error:
            failed.append((path, str(error)))
    # These boundaries were explicitly validated by the provider. Never prune
    # shared date/workspace directories, and never follow directory links.
    for owner in session.owned_dirs:
        if has_symlink(owner) or not owner.is_dir():
            continue
        directories = sorted((path for path in owner.rglob("*") if path.is_dir() and not has_symlink(path)),
                             key=lambda path: len(path.parts), reverse=True)
        for directory in (*directories, owner):
            try:
                directory.rmdir()
            except OSError:
                pass
    return DeleteResult(session, tuple(deleted), tuple(failed))


def delete_sessions(selected: list[Session], inventory: list[Session]) -> list[DeleteResult]:
    blockers = deletion_blockers(selected, inventory)
    if blockers:
        return [DeleteResult(s, (), tuple((s.path, reason) for reason in
                blockers.get(_key(s), ["Batch refused because another selected session is blocked"]))) for s in selected]
    # Delete dependants first. Each deletion re-scans disk, so a failed child
    # deletion continues to protect its ancestor even when both were selected.
    remaining = list(selected)
    results = []
    while remaining:
        session = next(s for s in remaining if not any(
            _key(other) != _key(s) and other.platform == s.platform and
            set(other.history_bases).intersection(s.rollout_ids) for other in remaining))
        results.append(delete_session(session))
        remaining.remove(session)
    return results
