from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Iterable
from uuid import UUID
import warnings

from .compression import text_lines
from .models import Session
from .utils import env_roots, file_snapshot, json_object, parse_datetime, unique_paths, walk_files

UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
UUID_RE = re.compile(UUID_PATTERN)
ROLLOUT_RE = re.compile(
    rf"rollout-(?:(\d{{4}}-\d{{2}}-\d{{2}}T\d{{2}}-\d{{2}}-\d{{2}})-)?"
    rf"({UUID_PATTERN})(?:_({UUID_PATTERN}))?\.jsonl(?:\.zst)?"
)


class Provider(ABC):
    name: str

    @abstractmethod
    def discover(self) -> list[Session]:
        raise NotImplementedError


def _uuid(value: object) -> str | None:
    if not isinstance(value, str) or not UUID_RE.fullmatch(value):
        return None
    return str(UUID(value))


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _time(value: object) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    return parse_datetime(value)


def _records(path: Path):
    lines = text_lines(path)
    try:
        for line in lines:
            item = json_object(line)
            if item is not None:
                yield item
    finally:
        lines.close()


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _text(item))) or None
    if isinstance(value, dict):
        return _text(value.get("text") or value.get("content") or value.get("input"))
    return None


def _user_text(value: object) -> str | None:
    text = _text(value)
    if text and text.startswith("<git-context>"):
        text = text.split("</git-context>", 1)[-1].strip()
    if text and text.startswith(("<environment_context>", "<system-reminder>", "# AGENTS.md instructions")):
        return None
    return text


def _exchange_records(records: Iterable[dict]) -> tuple[str | None, str | None]:
    prompt = None
    streamed: list[str] = []
    for item in records:
        record = item.get("payload") if isinstance(item.get("payload"), dict) else item
        kind = record.get("type")
        if not isinstance(kind, str):
            continue
        if kind in {"turn.prompt", "prompt.accepted", "user_message"}:
            if prompt is None:
                prompt = _user_text(record.get("input") or record.get("content") or record.get("message"))
            elif streamed:
                break
        elif kind in {"message", "response_item", "context.append_message"}:
            message = record.get("message") if kind == "context.append_message" else record
            if not isinstance(message, dict):
                continue
            if message.get("role") == "user" and prompt is None:
                prompt = _user_text(message.get("content") or message.get("text"))
            elif message.get("role") == "assistant" and prompt:
                response = _text(message.get("content") or message.get("text"))
                if response:
                    return prompt, response
        elif kind == "agent_message" and prompt:
            response = _text(record.get("message"))
            if response:
                return prompt, response
        elif kind == "context.append_loop_event" and prompt:
            event = record.get("event")
            if isinstance(event, dict) and event.get("type") == "content.part":
                part = event.get("part")
                if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                    streamed.append(part["text"])
        elif kind == "turn.ended" and prompt:
            break
    return prompt, "".join(streamed).strip() or None


def _exchange(path: Path) -> tuple[str | None, str | None]:
    records = _records(path)
    try:
        return _exchange_records(records)
    finally:
        records.close()


def _snapshot(session: Session) -> Session:
    snapshots = []
    blockers = list(session.blocked_reasons)
    for path in session.files:
        try:
            snapshots.append((path, file_snapshot(path)))
        except OSError:
            blockers.append(f"File changed during discovery: {path}")
    return replace(session, snapshots=tuple(snapshots), blocked_reasons=tuple(blockers))


def _merge(old: Session, new: Session) -> Session:
    return replace(old, started_at=old.started_at or new.started_at,
                   updated_at=max(filter(None, (old.updated_at, new.updated_at)), default=None),
                   cwd=old.cwd or new.cwd, title=old.title or new.title,
                   prompt=old.prompt or new.prompt, response=old.response or new.response,
                   archived=old.archived and new.archived,
                   files=unique_paths((*old.files, *new.files)),
                   owned_dirs=tuple(dict.fromkeys((*old.owned_dirs, *new.owned_dirs))),
                   rollout_ids=tuple(dict.fromkeys((*old.rollout_ids, *new.rollout_ids))),
                   history_bases=tuple(dict.fromkeys((*old.history_bases, *new.history_bases))),
                   blocked_reasons=tuple(dict.fromkeys((*old.blocked_reasons, *new.blocked_reasons))))


def sort_sessions(sessions: Iterable[Session]) -> list[Session]:
    return sorted(sessions, key=lambda s: (s.updated_at or s.started_at or datetime.min, s.platform, s.session_id), reverse=True)


class CodexProvider(Provider):
    name = "codex"

    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()
        roots = roots if roots is not None else env_roots("SESSIONMANAGER_CODEX_ROOTS", (home / "sessions", home / "archived_sessions"))
        # A normal sessions root must include archived references in safety checks.
        expanded = list(roots)
        for root in roots:
            for parent in (root, *root.parents):
                if parent.name in {"sessions", "archived_sessions"}:
                    expanded.extend((parent.parent / "sessions", parent.parent / "archived_sessions"))
                    break
        self.roots = tuple(dict.fromkeys(path.absolute() for path in expanded))

    def discover(self) -> list[Session]:
        found: dict[str, Session] = {}
        problems: list[str] = []
        candidates: set[Path] = set()
        for root in self.roots:
            try:
                candidates.update(path for path in walk_files(root) if ROLLOUT_RE.fullmatch(path.name))
            except OSError as error:
                problems.append(f"Cannot scan {root}: {error}")
        for path in sorted(candidates, key=lambda p: (p.name.endswith('.zst'), str(p))):
            match = ROLLOUT_RE.fullmatch(path.name)
            assert match is not None
            if match[1]:
                try:
                    datetime.strptime(match[1], "%Y-%m-%dT%H-%M-%S")
                except ValueError:
                    continue
            records = _records(path)
            metadata = None
            try:
                for index, item in enumerate(records):
                    if item.get("type") == "session_meta":
                        metadata = item.get("payload", item)
                        break
                    if index >= 31:
                        break
            except OSError as error:
                problems.append(f"Cannot read {path}: {error}")
            finally:
                records.close()
            if not isinstance(metadata, dict) or not (sid := _uuid(metadata.get("id"))):
                problems.append(f"Skipped rollout with invalid metadata: {path}")
                continue
            # Metadata is authoritative, but mismatching filenames are not owned safely.
            if sid != _uuid(match[2]):
                problems.append(f"Rollout ID/metadata mismatch: {path}")
                continue
            base = metadata.get("history_base")
            references = ()
            if base is not None:
                if not isinstance(base, dict) or not (base_id := _uuid(base.get("thread_id"))):
                    problems.append(f"Unknown history reference in {path}")
                else:
                    references = (base_id,)
            try:
                prompt, response = _exchange(path)
                updated = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).replace(tzinfo=None)
            except OSError as error:
                problems.append(f"Cannot read {path}: {error}")
                continue
            # Only exact, known sidecars; UUID substrings are never ownership evidence.
            plain = Path(str(path).removesuffix(".zst"))
            files = unique_paths((path, Path(str(plain) + ".lock")))
            session = Session(self.name, sid, path, _time(metadata.get("timestamp")),
                              _string(metadata.get("cwd")), _string(metadata.get("title")), files, prompt, response,
                              updated_at=updated, archived="archived_sessions" in path.parts,
                              rollout_ids=(_uuid(match[3] or match[2]),), history_bases=references,
                              discovery_roots=self.roots)
            found[sid] = _merge(found[sid], session) if sid in found else session
        if problems:
            warnings.warn("Codex discovery incomplete; deletion disabled: " + "; ".join(problems), RuntimeWarning, stacklevel=2)
        return sort_sessions(_snapshot(replace(session, blocked_reasons=tuple(problems))) for session in found.values())


class KimiProvider(Provider):
    name = "kimi"

    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        home = Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code").expanduser()
        self.roots = tuple(path.absolute() for path in roots) if roots is not None else env_roots("SESSIONMANAGER_KIMI_ROOTS", (
            home / "sessions", Path.home() / ".kimi" / "sessions", Path.home() / ".kimi" / "session"))

    def discover(self) -> list[Session]:
        found: dict[str, Session] = {}
        owners: set[Path] = set()
        for root in self.roots:
            try:
                for path in walk_files(root):
                    if path.name == "state.json" and path.parent.name.startswith("session_") and _uuid(path.parent.name[8:]):
                        owners.add(path.parent)
            except OSError as error:
                warnings.warn(f"Cannot scan {root}: {error}", RuntimeWarning, stacklevel=2)
        for owner in sorted(owners):
            state = owner / "state.json"
            try:
                metadata = json.loads(state.read_text(encoding="utf-8"))
                if not isinstance(metadata, dict) or metadata.get("id") != owner.name:
                    continue
                files = unique_paths(walk_files(owner))
                # Preview is exclusively the main agent; child prompts belong to different tasks.
                wire = owner / "agents" / "main" / "wire.jsonl"
                prompt, response = _exchange(wire) if wire in files else (None, None)
            except (OSError, ValueError) as error:
                warnings.warn(f"Skipped Kimi session {owner}: {error}", RuntimeWarning, stacklevel=2)
                continue
            session = Session(self.name, owner.name, owner, _time(metadata.get("createdAt")),
                              _string(metadata.get("cwd")) or _string(metadata.get("workDir")),
                              _string(metadata.get("title")) or _string(metadata.get("customTitle")), files, prompt, response,
                              updated_at=_time(metadata.get("updatedAt")) or _time(metadata.get("createdAt")),
                              archived=metadata.get("archived") is True, owned_dirs=(owner,), discovery_roots=self.roots)
            found[session.session_id] = _merge(found[session.session_id], session) if session.session_id in found else session
        return sort_sessions(_snapshot(session) for session in found.values())


def providers(selected: str = "all") -> list[Provider]:
    available = {"codex": CodexProvider, "kimi": KimiProvider}
    return [factory() for factory in available.values()] if selected == "all" else [available[selected]()]


def refresh_sessions(session: Session) -> list[Session]:
    """Use the original discovery scope when validating a pending deletion."""
    provider = {"codex": CodexProvider, "kimi": KimiProvider}[session.platform]
    return provider(session.discovery_roots).discover()
