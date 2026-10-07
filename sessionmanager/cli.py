from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import sys
import textwrap
import unicodedata

from .manager import delete_sessions, deletion_blockers, discover
from .models import Session

_BACK = object()


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _error(message: str, as_json: bool, code: int = 2) -> int:
    if as_json:
        _json({"status": "error", "error": message, "exit_code": code})
    else:
        print(message, file=sys.stderr)
    return code


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _date(session: Session) -> str:
    return session.started_at.strftime("%Y-%m-%d %H:%M") if session.started_at else "unknown"


def _preview(value: str | None, limit: int = 72) -> str:
    value = " ".join((value or "-").split())
    # Remove terminal control characters from data displayed in a terminal.
    value = "".join(char for char in value if char.isprintable())
    def width(char: str) -> int:
        return 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    if sum(width(char) for char in value) <= limit:
        return value
    result, used = [], 0
    for char in value:
        if used + width(char) > max(0, limit - 3):
            break
        result.append(char)
        used += width(char)
    return "".join(result) + "..."


def _size(size: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return "0 B"


def _project(session: Session) -> str:
    return session.cwd or "(unknown project)"


def _projects(sessions: list[Session]) -> list[tuple[str, list[Session]]]:
    grouped: dict[str, list[Session]] = {}
    for session in sessions:
        grouped.setdefault(_project(session), []).append(session)
    return list(grouped.items())


def _data(session: Session) -> dict:
    return {"platform": session.platform, "id": session.session_id, "date": _date(session),
            "cwd": session.cwd, "title": session.title,
            "created_at": session.started_at.isoformat() + "Z" if session.started_at else None,
            "updated_at": session.updated_at.isoformat() + "Z" if session.updated_at else None,
            "archived": session.archived, "first_prompt": session.prompt, "first_response": session.response,
            "files": [str(path) for path in session.files], "bytes": session.size,
            "history_bases": list(session.history_bases), "blocked_reasons": list(session.blocked_reasons)}


def _print_platforms(sessions: list[Session], as_json: bool) -> None:
    rows = [{"platform": name, "sessions": sum(s.platform == name for s in sessions)} for name in ("codex", "kimi")]
    if as_json:
        _json(rows)
    else:
        print(" #  SESSIONS  AGENT PLATFORM")
        for index, row in enumerate(rows, 1):
            print(f"{index:>2}  {row['sessions']:>8}  {row['platform']}")


def _select_platform(sessions: list[Session], as_json: bool) -> str | None:
    _print_platforms(sessions, as_json)
    if as_json or not sys.stdin.isatty():
        return None
    while True:
        answer = _input("Select platform (1=codex, 2=kimi; 0/q/Enter=quit): ")
        if answer in {"", "0", "q"}:
            return None
        if answer in {"1", "2", "codex", "kimi"}:
            return {"1": "codex", "2": "kimi"}.get(answer, answer)
        print("Invalid agent platform selection. Please try again.", file=sys.stderr)


def _print_projects(projects: list[tuple[str, list[Session]]], as_json: bool, offset: int = 0) -> None:
    if as_json:
        _json([{"project": project, "sessions": len(items)} for project, items in projects])
        return
    if not projects:
        print("No sessions found.")
        return
    print(" #  SESSIONS  PROJECT FOLDER")
    for index, (project, items) in enumerate(projects, offset + 1):
        print(f"{index:>2}  {len(items):>8}  {_preview(project, 160)}")


def _same_project(project: str, requested: str) -> bool:
    if project == "(unknown project)" or requested == "(unknown project)":
        return project == requested
    try:
        return Path(project).expanduser().resolve() == Path(requested).expanduser().resolve()
    except (OSError, ValueError):
        return project == requested


def _select_project(projects: list[tuple[str, list[Session]]], project_arg: str | None,
                    as_json: bool, page_size: int = 20) -> list[Session] | object | None:
    if project_arg:
        for project, items in projects:
            if _same_project(project, project_arg):
                return items
        return None
    if as_json or not sys.stdin.isatty():
        _print_projects(projects, as_json)
        return None
    query, page = "", 0
    while True:
        matches = [row for row in projects if query.casefold() in row[0].casefold()]
        pages = max(1, (len(matches) + page_size - 1) // page_size)
        page = min(page, pages - 1)
        _print_projects(matches[page * page_size:(page + 1) * page_size], False, page * page_size)
        print(f"Projects: page {page + 1}/{pages}; filter: {_preview(query or '(all)')}")
        answer = _input("Project number; n/p=page, /text=search, /=clear, 0=back, q/Enter=quit: ")
        if answer in {"", "q"}:
            return None
        if answer == "0":
            return _BACK
        if answer in {"n", "p"}:
            page = max(0, min(pages - 1, page + (1 if answer == "n" else -1)))
        elif answer.startswith("/"):
            query, page = answer[1:], 0
        else:
            try:
                indexes = _indexes(answer, len(matches))
                if len(indexes) != 1:
                    raise ValueError("Choose one project")
                return matches[indexes[0]][1]
            except ValueError:
                print("Invalid project selection. Please try again.", file=sys.stderr)


def _print(sessions: list[Session], as_json: bool, indexes: list[int] | None = None) -> None:
    if as_json:
        _json([_data(session) for session in sessions])
        return
    if not sessions:
        print("No sessions found.")
        return
    width = max(30, shutil.get_terminal_size((100, 24)).columns - 6)
    for index, session in zip(indexes if indexes is not None else range(len(sessions)), sessions):
        updated = session.updated_at.strftime("%Y-%m-%d %H:%M") if session.updated_at else _date(session)
        print(f"{index + 1:>3}  {session.platform} {session.display_id}  {updated} UTC  {_size(session.size)}"
              + (" [archived]" if session.archived else "") + (" [blocked]" if session.blocked_reasons else ""))
        print(f"     {_preview(session.title or session.prompt or '(untitled)', width)}")
        print(f"     Q: {_preview(session.prompt, width - 3)}")
        print(f"     A: {_preview(session.response, width - 3)}")


def _details(session: Session, as_json: bool = False) -> None:
    if as_json:
        _json(_data(session))
        return
    print(f"{session.platform} {session.session_id}")
    print(f"Project: {_preview(session.cwd, 500)}")
    print(f"Title: {_preview(session.title, 500)}")
    print(f"Files: {len(session.files)}; total: {_size(session.size)}")
    for label, content in (("First prompt", session.prompt), ("First response", session.response)):
        print(f"{label}:")
        for line in (content or "-").splitlines():
            print(textwrap.fill("".join(c for c in line if c.isprintable()),
                                width=max(30, shutil.get_terminal_size((100, 24)).columns)))
    for path in session.files:
        print(f"  {_preview(str(path), 1000)}")
    for reason in session.blocked_reasons:
        print(f"Blocked: {_preview(reason, 1000)}")


def _indexes(value: str, count: int) -> list[int]:
    result: set[int] = set()
    for item in value.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", item)
        if not match:
            raise ValueError("use numbers or ranges, e.g. 1,3-5")
        left, right = int(match[1]), int(match[2] or match[1])
        if not 1 <= left <= right <= count:
            raise ValueError("selection is outside the listed range")
        result.update(range(left - 1, right))
    if not result:
        raise ValueError("empty selection")
    return sorted(result)


def _matches(session: Session, query: str) -> bool:
    return query.casefold() in "\n".join((session.session_id, session.title or "", session.prompt or "", session.response or "")).casefold()


def _select_sessions(sessions: list[Session], page_size: int = 20) -> list[int] | object | None:
    query, page = "", 0
    while True:
        indexes = [i for i, session in enumerate(sessions) if _matches(session, query)]
        pages = max(1, (len(indexes) + page_size - 1) // page_size)
        page = min(page, pages - 1)
        visible = indexes[page * page_size:(page + 1) * page_size]
        _print([sessions[i] for i in visible], False, visible)
        print(f"Sessions: page {page + 1}/{pages}; filter: {_preview(query or '(all)')}")
        answer = _input("Number/v N=details; d 1,3-5=delete; n/p=page, /text=search, /=clear, 0=back, q/Enter=quit: ")
        if answer in {"", "q"}:
            return None
        if answer == "0":
            return _BACK
        if answer in {"n", "p"}:
            page = max(0, min(pages - 1, page + (1 if answer == "n" else -1)))
            continue
        if answer.startswith("/"):
            query, page = answer[1:], 0
            continue
        deleting = answer.startswith("d ")
        selection = answer[2:].strip() if deleting or answer.startswith("v ") else answer
        try:
            chosen = _indexes(selection, len(sessions))
            if not set(chosen).issubset(indexes):
                raise ValueError("selection is outside the current search results")
            if deleting:
                return chosen
            if len(chosen) != 1:
                raise ValueError("view one session, or use d before a deletion selection")
            _details(sessions[chosen[0]])
        except ValueError as error:
            print(f"Invalid selection: {error}. Please try again.", file=sys.stderr)


def _delete_selected(selected: list[Session], args: argparse.Namespace, inventory: list[Session]) -> int:
    blocked = deletion_blockers(selected, inventory)
    plan = {"status": "blocked" if blocked else "planned", "dry_run": args.dry_run,
            "sessions": [_data(s) for s in selected], "file_count": sum(len(s.files) for s in selected),
            "bytes": sum(s.size for s in selected),
            "blockers": [{"platform": key[0], "id": key[1], "reasons": reasons} for key, reasons in blocked.items()]}
    if not args.json:
        print(f"Selected {len(selected)} session(s), {plan['file_count']} file(s), {_size(plan['bytes'])}.")
        for session in selected:
            print(f"- {session.platform} {session.session_id} ({_preview(session.title or session.prompt)})")
            for path in session.files:
                print(f"  {_preview(str(path), 1000)}")
        for reasons in blocked.values():
            for reason in reasons:
                print(f"Blocked: {_preview(reason, 1000)}", file=sys.stderr)
    if args.dry_run or blocked:
        if args.json:
            _json(plan)
        return 1 if blocked else 0
    if not args.yes:
        if args.json or not sys.stdin.isatty():
            return _error("Deletion requires --yes in JSON or non-interactive mode; use --dry-run to review.", args.json)
        if _input("Delete these files permanently? [y/N] ").lower() not in {"y", "yes"}:
            print("Cancelled.")
            return 0
    results = delete_sessions(selected, inventory)
    failed = any(result.failed for result in results)
    if args.json:
        plan.update(status="failed" if failed else "deleted", results=[{
            "platform": result.session.platform, "id": result.session.session_id,
            "deleted": [str(path) for path in result.deleted],
            "failed": [{"path": str(path), "reason": reason} for path, reason in result.failed]
        } for result in results])
        _json(plan)
    else:
        for result in results:
            print(f"Deleted {len(result.deleted)} file(s) for {result.session.session_id}.")
            for path, reason in result.failed:
                print(f"Could not delete {_preview(str(path), 1000)}: {_preview(reason, 1000)}", file=sys.stderr)
    return 1 if failed else 0


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _positive(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description="View and remove Codex/Kimi Code sessions.")
    parser.add_argument("--platform", choices=("all", "codex", "kimi"))
    parser.add_argument("--project", metavar="PATH", help="select a project (use '(unknown project)' for missing cwd)")
    parser.add_argument("--json", action="store_true", help="print machine-readable data, including deletion results")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--delete", metavar="SELECTION", help="delete indexes such as 1,3-5 within the selected project")
    actions.add_argument("--delete-id", action="append", metavar="ID", help="delete exact ID; repeat for multiple sessions")
    actions.add_argument("--details", metavar="ID", help="show details for an exact session ID")
    parser.add_argument("--yes", action="store_true", help="explicitly confirm deletion without a prompt")
    parser.add_argument("--dry-run", action="store_true", help="show complete deletion plan without deleting")
    parser.add_argument("--search", default="", help="filter sessions by title, ID, or first question/answer")
    parser.add_argument("--sort", choices=("updated", "created", "size"), default="updated")
    parser.add_argument("--page-size", type=_positive, default=20, help="rows per interactive page (default: 20)")
    return parser


def _filtered(sessions: list[Session], args: argparse.Namespace) -> list[Session]:
    sessions = [s for s in sessions if _matches(s, args.search)]
    def key(session):
        primary = session.size if args.sort == "size" else (
            session.started_at if args.sort == "created" else session.updated_at or session.started_at) or datetime.min
        return primary, session.platform, session.session_id
    return sorted(sessions, key=key, reverse=True)


def _by_id(sessions: list[Session], value: str) -> Session:
    matches = [s for s in sessions if value in {s.session_id, f"{s.platform}:{s.session_id}"}]
    if len(matches) != 1:
        raise ValueError(f"Session ID not found or ambiguous in this project: {value}; use platform:ID if needed")
    return matches[0]


def _run(args: argparse.Namespace) -> int:
    action = args.delete is not None or args.delete_id is not None or args.details is not None
    if action and (not args.platform or not args.project):
        return _error("--delete, --delete-id and --details require --platform and --project.", args.json)
    if args.project and not args.platform and (args.json or not sys.stdin.isatty()):
        return _error("--project requires --platform in JSON or non-interactive mode.", args.json)
    inventory = discover(args.platform or "all")
    if args.project and not any(_same_project(_project(s), args.project) for s in inventory):
        return _error(f"Project not found: {args.project}", args.json)
    if action:
        sessions = _select_project(_projects(_filtered(inventory, args)), args.project, False)
        if sessions is None:
            return _error(f"Project not found: {args.project}", args.json)
        try:
            if args.details is not None:
                _details(_by_id(sessions, args.details), args.json)
                return 0
            if args.delete_id is not None:
                selected_by_key = {}
                for sid in args.delete_id:
                    session = _by_id(sessions, sid)
                    selected_by_key[(session.platform, session.session_id)] = session
                selected = list(selected_by_key.values())
            else:
                selected = [sessions[i] for i in _indexes(args.delete, len(sessions))]
        except ValueError as error:
            return _error(str(error), args.json)
        return _delete_selected(selected, args, inventory)
    if args.json or not sys.stdin.isatty():
        if args.platform is None:
            _print_platforms(inventory, args.json)
        elif args.project:
            sessions = _select_project(_projects(_filtered(inventory, args)), args.project, False)
            if sessions is None:
                return _error(f"Project not found: {args.project}", args.json)
            _print(sessions, args.json)
        else:
            _print_projects(_projects(_filtered(inventory, args)), args.json)
        return 0
    # Explicit navigation state: sessions -> projects -> platforms. Refresh after
    # each action while retaining the platform/project the user is browsing.
    platform = args.platform
    project = args.project
    exit_code = 0
    while True:
        if platform is None:
            platform = _select_platform(inventory, False)
            if platform is None:
                return exit_code
        platform_sessions = _filtered([s for s in inventory if platform == "all" or s.platform == platform], args)
        if project is None:
            selection = _select_project(_projects(platform_sessions), None, False, args.page_size)
            if selection is _BACK:
                platform = None
                inventory = discover("all")
                continue
            if selection is None:
                return exit_code
            project = _project(selection[0])
        sessions = [s for s in platform_sessions if _same_project(_project(s), project)]
        if not sessions:
            print(f"No sessions found for {_preview(project, 500)}.")
            project = None
            continue
        selected = _select_sessions(sessions, args.page_size)
        if selected is _BACK:
            project = None
            continue
        if selected is None:
            return exit_code
        exit_code = max(exit_code, _delete_selected([sessions[i] for i in selected], args, inventory))
        inventory = discover(platform)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    as_json = "--json" in argv
    try:
        return _run(build_parser().parse_args(argv))
    except KeyboardInterrupt:
        return _error("Interrupted.", as_json, 130)
    except ValueError as error:
        return _error(str(error), as_json)
    except OSError as error:
        return _error(str(error), as_json, 1)
