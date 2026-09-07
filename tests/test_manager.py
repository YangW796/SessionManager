from datetime import datetime
from pathlib import Path

from sessionmanager.manager import delete_session
from sessionmanager.models import Session
from sessionmanager.providers import CodexProvider, KimiProvider


def test_codex_groups_rollout_and_same_session_files(tmp_path: Path):
    root = tmp_path / "sessions" / "2026" / "09" / "01"
    root.mkdir(parents=True)
    sid = "123e4567-e89b-12d3-a456-426614174000"
    main = root / f"rollout-{sid}.jsonl"
    main.write_text('{"type":"session_meta","id":"' + sid + '","cwd":"/work"}\n', encoding="utf-8")
    extra = root / f"rollout-{sid}.jsonl.lock"
    extra.write_text("lock", encoding="utf-8")
    (root / "rollout-other.jsonl").write_text("{}\n", encoding="utf-8")

    sessions = CodexProvider((root.parent.parent.parent.parent,)).discover()
    assert len(sessions) == 1
    assert set(sessions[0].files) == {main.absolute(), extra.absolute()}
    assert sessions[0].cwd == "/work"


def test_kimi_directory_is_deleted_but_parent_is_preserved(tmp_path: Path):
    root = tmp_path / "sessions"
    sid = "123e4567-e89b-12d3-a456-426614174001"
    session_dir = root / f"session_{sid}"
    session_dir.mkdir(parents=True)
    (session_dir / "state.json").write_text('{"id":"session_' + sid + '","cwd":"/work","lastPrompt":"question","createdAt":1788671031892}\n', encoding="utf-8")
    (session_dir / "messages.jsonl").write_text("{}\n", encoding="utf-8")
    sessions = KimiProvider((root,)).discover()
    result = delete_session(sessions[0])
    assert len(result.deleted) == 2
    assert not session_dir.exists()
    assert root.exists()


def test_session_delete_reports_locked_file(tmp_path: Path):
    path = tmp_path / "session.jsonl"
    path.write_text("x", encoding="utf-8")
    session = Session("codex", "id", path, datetime.now(), files=(path,))
    result = delete_session(session)
    assert result.deleted == (path.absolute(),)
    assert not path.exists()
