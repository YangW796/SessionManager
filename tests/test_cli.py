from pathlib import Path
import sys
from unittest.mock import patch

from sessionmanager.cli import _BACK, _select_platform, _select_project, _select_sessions
from sessionmanager.models import Session


def test_session_selection_retries_invalid_input():
    sessions = [Session("codex", "id", Path("session"))]

    with patch("builtins.input", side_effect=["invalid", "d 1,1"]):
        assert _select_sessions(sessions) == [0]


def test_session_selection_can_go_back():
    with patch("builtins.input", return_value="0"):
        assert _select_sessions([]) is _BACK


def test_project_selection_can_go_back():
    with patch("builtins.input", return_value="0"), patch.object(sys.stdin, "isatty", return_value=True):
        assert _select_project([("/work", [])], None, False) is _BACK


def test_platform_selection_retries_invalid_input():
    with patch("builtins.input", side_effect=["3", "1"]), patch.object(sys.stdin, "isatty", return_value=True):
        assert _select_platform([], False) == "codex"
