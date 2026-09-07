from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from .manager import delete_session, discover
from .models import Session


_BACK = object()


def _date(session: Session) -> str:
    return session.started_at.strftime("%Y-%m-%d %H:%M") if session.started_at else "unknown"


def _preview(value: str | None, limit: int = 72) -> str:
    if not value:
        return "-"
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _project(session: Session) -> str:
    return session.cwd or "(unknown project)"


def _projects(sessions: list[Session]) -> list[tuple[str, list[Session]]]:
    grouped: dict[str, list[Session]] = {}
    for session in sessions:
        grouped.setdefault(_project(session), []).append(session)
    return sorted(grouped.items(), key=lambda item: item[1][0].started_at or datetime.min,
                  reverse=True)


def _print_platforms(sessions: list[Session], as_json: bool) -> None:
    counts = {platform: sum(1 for session in sessions if session.platform == platform)
              for platform in ("codex", "kimi")}
    if as_json:
        print(json.dumps([{"platform": platform, "sessions": count} for platform, count in counts.items()],
                         ensure_ascii=False, indent=2))
        return
    print(" #  SESSIONS  AGENT PLATFORM")
    for index, (platform, count) in enumerate(counts.items(), 1):
        print(f"{index:>2}  {count:>8}  {platform}")


def _select_platform(sessions: list[Session], as_json: bool) -> str | None:
    _print_platforms(sessions, as_json)
    if as_json or not sys.stdin.isatty():
        if not as_json:
            print("Use --platform codex or --platform kimi to choose an agent.", file=sys.stderr)
        return None
    choices = {"1": "codex", "2": "kimi"}
    while True:
        answer = input("Select an agent platform (1=codex, 2=kimi, 0=quit): ").strip()
        if not answer:
            return None
        if answer == "0":
            return None
        if answer in choices:
            return choices[answer]
        print("Invalid agent platform selection. Please try again.", file=sys.stderr)


def _print_projects(projects: list[tuple[str, list[Session]]], as_json: bool) -> None:
    if as_json:
        print(json.dumps([{"project": project, "sessions": len(items)} for project, items in projects],
                         ensure_ascii=False, indent=2))
        return
    if not projects:
        print("No sessions found.")
        return
    print(" #  SESSIONS  PROJECT FOLDER")
    for index, (project, items) in enumerate(projects, 1):
        print(f"{index:>2}  {len(items):>8}  {project}")


def _select_project(projects: list[tuple[str, list[Session]]], project_arg: str | None,
                    as_json: bool) -> list[Session] | object | None:
    if project_arg:
        requested = Path(project_arg).expanduser()
        try:
            requested = requested.resolve()
        except OSError:
            pass
        for project, items in projects:
            if project != "(unknown project)" and Path(project).expanduser().resolve() == requested:
                return items
        print(f"Project not found: {project_arg}", file=sys.stderr)
        return None
    _print_projects(projects, as_json)
    if as_json:
        return None
    if not sys.stdin.isatty():
        print("Use --project PATH to choose a project in a script.", file=sys.stderr)
        return None
    while True:
        answer = input("Select a project (number, 0=back, Enter to quit): ").strip()
        if not answer:
            return None
        if answer == "0":
            return _BACK
        try:
            index = int(answer) - 1
            return projects[index][1]
        except (ValueError, IndexError):
            print("Invalid project selection. Please try again.", file=sys.stderr)


def _print(sessions: list[Session], as_json: bool) -> None:
    if as_json:
        print(json.dumps([{"platform": s.platform, "id": s.session_id, "date": _date(s), "cwd": s.cwd,
                           "title": s.title, "first_prompt": s.prompt, "first_response": s.response,
                           "files": [str(p) for p in s.files], "bytes": s.size} for s in sessions],
                         ensure_ascii=False, indent=2))
        return
    if not sessions:
        print("No sessions found.")
        return
    print(" #  PLATFORM  DATE             SESSION ID                            FILES  FIRST QUESTION / ANSWER")
    for index, session in enumerate(sessions, 1):
        print(f"{index:>2}  {session.platform:<8}  {_date(session):<16} {session.session_id:<36} {len(session.files):>5}  Q: {_preview(session.prompt)}")
        print(f"{'':>2}  {'':<8}  {'':<16} {'':<36} {'':>5}  A: {_preview(session.response)}")


def _indexes(value: str, count: int) -> list[int]:
    result: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if "-" in item:
            left, right = (int(part) for part in item.split("-", 1))
            result.update(range(left, right + 1))
        elif item:
            result.add(int(item))
    if not result or min(result) < 1 or max(result) > count:
        raise ValueError("selection is outside the listed range")
    return sorted(index - 1 for index in result)


def _select_sessions(sessions: list[Session]) -> list[int] | object | None:
    _print(sessions, False)
    while True:
        answer = input(
            "Select sessions to delete (format: number or comma/range, e.g. 1,3-5; "
            "0=back, Enter to quit): "
        ).strip()
        if not answer:
            return None
        if answer == "0":
            return _BACK
        try:
            return _indexes(answer, len(sessions))
        except (ValueError, IndexError) as error:
            print(f"Invalid selection: {error}. Please try again.", file=sys.stderr)


def _delete_selected(selected: list[Session], args: argparse.Namespace) -> int:
    print(f"Selected {len(selected)} session(s), {sum(len(s.files) for s in selected)} file(s).")
    for session in selected:
        print(f"- {session.platform} {session.session_id}: {session.path}")
    if args.dry_run:
        return 0
    if not args.yes and input("Delete these files permanently? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Cancelled.")
        return 0
    failures = 0
    for session in selected:
        result = delete_session(session)
        failures += len(result.failed)
        print(f"Deleted {len(result.deleted)} file(s) for {session.session_id}.")
        for path, reason in result.failed:
            print(f"Could not delete {path}: {reason}", file=sys.stderr)
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="View and remove Codex/Kimi Code sessions.")
    parser.add_argument("--platform", choices=("all", "codex", "kimi"), default=None)
    parser.add_argument("--project", metavar="PATH", help="select a project folder without interactive selection")
    parser.add_argument("--json", action="store_true", help="print machine-readable session data")
    parser.add_argument("--delete", metavar="SELECTION", help="delete indexes such as 1,3-5")
    parser.add_argument("--yes", action="store_true", help="skip the delete confirmation")
    parser.add_argument("--dry-run", action="store_true", help="show files without deleting")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    all_sessions = discover(args.platform or "all")
    if args.platform is None:
        while True:
            selected_platform = _select_platform(all_sessions, args.json)
            if selected_platform is None:
                return 0
            platform_sessions = [session for session in all_sessions if session.platform == selected_platform]
            selected_sessions = _select_project(_projects(platform_sessions), None, args.json)
            if selected_sessions is _BACK:
                continue
            if selected_sessions is None:
                return 0
            sessions = selected_sessions
            selected_indexes = _select_sessions(sessions)
            if selected_indexes is _BACK:
                continue
            if selected_indexes is None:
                return 0
            return _delete_selected([sessions[index] for index in selected_indexes], args)

    selected_sessions = _select_project(_projects(all_sessions), args.project, args.json)
    if selected_sessions is None or selected_sessions is _BACK:
        return 0 if not args.project else 2
    sessions = selected_sessions
    if not args.delete:
        if args.json or not sys.stdin.isatty():
            _print(sessions, args.json)
            return 0
        selected_indexes = _select_sessions(sessions)
        if selected_indexes is _BACK or selected_indexes is None:
            return 0
        return _delete_selected([sessions[index] for index in selected_indexes], args)
    else:
        answer = args.delete
    try:
        selected = [sessions[index] for index in _indexes(answer, len(sessions))]
    except (ValueError, IndexError) as error:
        print(f"Invalid selection: {error}", file=sys.stderr)
        return 2
    return _delete_selected(selected, args)
