from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from uuid import UUID

from .models import Session
from .utils import env_roots, first_json_objects, parse_datetime, unique_paths


# Codex currently emits UUIDv7 session IDs. Accept all UUID versions because
# platform formats can change and the variant bits still provide validation.
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}")


class Provider(ABC):
    name: str

    @abstractmethod
    def discover(self) -> list[Session]:
        raise NotImplementedError


def _uuid(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    match = UUID_RE.search(value)
    return str(UUID(match.group(0))) if match else None


def _metadata(path: Path) -> tuple[str | None, datetime | None, str | None, str | None]:
    for item in first_json_objects(path):
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        session_id = (_uuid(item.get("id")) or _uuid(item.get("session_id")) or
                      _uuid(item.get("sessionId")) or _uuid(payload.get("id")) or
                      _uuid(payload.get("session_id")) or _uuid(payload.get("sessionId")))
        if session_id:
            started = parse_datetime(item.get("timestamp") or item.get("created_at") or item.get("createdAt") or
                                     payload.get("timestamp") or payload.get("created_at") or payload.get("createdAt"))
            cwd = item.get("cwd") or item.get("working_directory") or payload.get("cwd") or payload.get("working_directory")
            title = item.get("title") or item.get("name") or payload.get("title") or payload.get("name")
            return session_id, started, cwd if isinstance(cwd, str) else None, title if isinstance(title, str) else None
    session_id = _uuid(path.name)
    return session_id, None, None, None


def _text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [_text(item.get("text") if isinstance(item, dict) else item) for item in value]
        text = "\n".join(part for part in parts if part)
        return text or None
    if isinstance(value, dict):
        return _text(value.get("text") or value.get("content") or value.get("input"))
    return None


def _user_text(value: object) -> str | None:
    text = _text(value)
    if text and text.startswith("<git-context>"):
        text = text.split("</git-context>", 1)[-1].strip()
    if text and (text.startswith("<environment_context>") or text.startswith("<system-reminder>")):
        return None
    return text


def _exchange(path: Path) -> tuple[str | None, str | None]:
    prompt = response = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                item = json.loads(line)
                record = item.get("payload") if isinstance(item.get("payload"), dict) else item
                kind = record.get("type")
                if kind in {"turn.prompt", "prompt.accepted"} and prompt is None:
                    prompt = _user_text(record.get("input") or record.get("content"))
                elif kind in {"response_item", "message"}:
                    if record.get("role") == "user" and prompt is None:
                        prompt = _user_text(record.get("content") or record.get("text"))
                    elif record.get("role") == "assistant" and response is None:
                        response = _text(record.get("content") or record.get("text"))
                elif kind == "context.append_message" and response is None:
                    message = item.get("message") if isinstance(item.get("message"), dict) else {}
                    if message.get("role") == "assistant":
                        response = _text(message.get("content"))
                elif kind == "context.append_loop_event" and prompt and response is None:
                    event = item.get("event") if isinstance(item.get("event"), dict) else {}
                    part = event.get("part") if event.get("type") == "content.part" else {}
                    if isinstance(part, dict) and part.get("type") == "text":
                        response = _text(part.get("text"))
                if prompt and response:
                    break
    except (OSError, ValueError, TypeError):
        pass
    return prompt, response


def _session_dir(path: Path) -> Path | None:
    for parent in (path, *path.parents):
        if parent.name.startswith("session_") and _uuid(parent.name):
            return parent
    return None


class CodexProvider(Provider):
    name = "codex"

    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        self.roots = roots or env_roots("SESSIONMANAGER_CODEX_ROOTS", (
            Path.home() / ".codex" / "sessions",
            Path.home() / ".codex" / "archived_sessions",
        ))

    def discover(self) -> list[Session]:
        candidates = [path for root in self.roots for path in root.rglob("*.jsonl")]
        sessions: dict[str, Session] = {}
        for path in candidates:
            session_id, started, cwd, title = _metadata(path)
            if not session_id:
                match = UUID_RE.search(path.stem)
                session_id = str(UUID(match.group(0))) if match else None
            if not session_id:
                continue
            siblings = [item for item in path.parent.iterdir() if item.is_file() and (item == path or session_id in item.name)]
            prompt, response = _exchange(path)
            session = Session(self.name, session_id, path, started, cwd, title, unique_paths(siblings), prompt, response)
            previous = sessions.get(session_id)
            if previous:
                sessions[session_id] = Session(self.name, session_id, previous.path, previous.started_at or started,
                                                previous.cwd or cwd, previous.title or title,
                                                unique_paths((*previous.files, *session.files)),
                                                previous.prompt or prompt, previous.response or response)
            else:
                sessions[session_id] = session
        return sorted(sessions.values(), key=lambda item: item.started_at or datetime.min, reverse=True)


class KimiProvider(Provider):
    name = "kimi"

    def __init__(self, roots: tuple[Path, ...] | None = None) -> None:
        self.roots = roots or env_roots("SESSIONMANAGER_KIMI_ROOTS", (
            Path.home() / ".kimi-code" / "sessions",
            Path.home() / ".kimi" / "sessions",
            Path.home() / ".kimi" / "session",
        ))

    def discover(self) -> list[Session]:
        # Kimi has used both one-file and one-directory layouts. A directory
        # named by a UUID is the ownership boundary; never delete its parent.
        found: dict[str, Session] = {}
        for root in self.roots:
            session_dirs = {owner for path in root.rglob("*") if (owner := _session_dir(path))}
            for owner in session_dirs:
                state = owner / "state.json"
                state_items = first_json_objects(state) if state.is_file() else []
                metadata = state_items[0] if state_items else {}
                session_id = metadata.get("id") if isinstance(metadata.get("id"), str) else owner.name
                session_id = session_id if session_id.startswith("session_") else owner.name
                started_value = metadata.get("createdAt")
                started = parse_datetime(started_value) if isinstance(started_value, str) else None
                if isinstance(started_value, (int, float)):
                    started = datetime.fromtimestamp(started_value / 1000)
                cwd = metadata.get("cwd") if isinstance(metadata.get("cwd"), str) else None
                title = metadata.get("title") if isinstance(metadata.get("title"), str) else None
                wire_files = sorted(owner.glob("agents/*/wire.jsonl"))
                prompt = response = None
                for wire in wire_files:
                    found_prompt, found_response = _exchange(wire)
                    prompt = prompt or found_prompt
                    response = response or found_response
                    if prompt and response:
                        break
                files = list(owner.rglob("*"))
                session = Session(self.name, session_id, owner, started, cwd, title, unique_paths(files), prompt, response)
                old = found.get(session_id)
                if old:
                    found[session_id] = Session(self.name, session_id, old.path, old.started_at or started,
                                                old.cwd or cwd, old.title or title,
                                                unique_paths((*old.files, *session.files)),
                                                old.prompt or prompt, old.response or response)
                else:
                    found[session_id] = session
            # Files outside a UUID-owned session directory are intentionally ignored.
        return sorted(found.values(), key=lambda item: item.started_at or datetime.min, reverse=True)


def providers(selected: str = "all") -> list[Provider]:
    available = {provider.name: provider for provider in (CodexProvider(), KimiProvider())}
    return list(available.values()) if selected == "all" else [available[selected]]
