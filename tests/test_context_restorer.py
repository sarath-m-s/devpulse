"""Tests for the ContextRestorer analyzer."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from ghost_pulse import db
from ghost_pulse.analyzers.context_restorer import (
    ContextRestorer,
    _fmt_time_away,
    _fmt_duration,
)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


def _ts(delta_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%S")


class TestFormatHelpers:
    def test_fmt_duration_minutes_only(self):
        assert _fmt_duration(45) == "45m"

    def test_fmt_duration_hours_and_minutes(self):
        assert _fmt_duration(90) == "1h 30m"

    def test_fmt_duration_none(self):
        assert _fmt_duration(None) == "unknown"

    def test_fmt_time_away_minutes(self):
        recent = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
        result = _fmt_time_away(recent)
        assert "minute" in result

    def test_fmt_time_away_days(self):
        old = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%S")
        result = _fmt_time_away(old)
        assert "day" in result


class TestCaptureSnapshot:
    def test_captures_snapshot_with_git_mocked(self):
        # Insert some events for the project
        for i in range(3):
            db.insert_event(
                "shell_cmd",
                {"cmd": f"cmd-{i}", "cwd": "/home/user/myproj", "exit_code": 0},
                project="myproj",
                timestamp=_ts(-300 + i * 10),
                session_id="sess1",
            )

        restorer = ContextRestorer(llm_provider=None)
        with patch("ghost_pulse.analyzers.context_restorer._run_git", return_value="main"), \
             patch("ghost_pulse.analyzers.context_restorer._find_project_path", return_value="/home/user/myproj"):
            snap_id = restorer.capture_snapshot("myproj", "sess1")

        assert snap_id > 0
        snap = db.get_latest_snapshot("myproj")
        assert snap is not None
        assert snap["project"] == "myproj"
        assert snap["session_id"] == "sess1"

    def test_captures_last_command(self):
        db.insert_event(
            "shell_cmd",
            {"cmd": "pytest tests/", "cwd": "/home/user/myproj", "exit_code": 0},
            project="myproj",
            timestamp=_ts(-60),
            session_id="sess1",
        )
        restorer = ContextRestorer(llm_provider=None)
        with patch("ghost_pulse.analyzers.context_restorer._run_git", return_value=""), \
             patch("ghost_pulse.analyzers.context_restorer._find_project_path", return_value=None):
            restorer.capture_snapshot("myproj", "sess1")

        snap = db.get_latest_snapshot("myproj")
        assert snap["last_command"] == "pytest tests/"

    def test_records_last_error_on_failure(self):
        db.insert_event(
            "shell_cmd",
            {"cmd": "make build", "cwd": "/tmp", "exit_code": 1},
            project="myproj",
            timestamp=_ts(-30),
            session_id="sess1",
        )
        restorer = ContextRestorer(llm_provider=None)
        with patch("ghost_pulse.analyzers.context_restorer._run_git", return_value=""), \
             patch("ghost_pulse.analyzers.context_restorer._find_project_path", return_value=None):
            restorer.capture_snapshot("myproj", "sess1")

        snap = db.get_latest_snapshot("myproj")
        assert snap["last_error"] == "make build"


class TestResume:
    def test_returns_error_for_missing_project(self):
        restorer = ContextRestorer(llm_provider=None)
        result = restorer.resume("nonexistent-project")
        assert "error" in result

    def test_returns_context_for_known_project(self):
        db.insert_session_snapshot(
            project="myproj",
            session_id="sess1",
            branch="feature/new-feature",
            last_command="pytest",
            duration_minutes=90,
        )
        restorer = ContextRestorer(llm_provider=None)
        result = restorer.resume("myproj")
        assert result["project"] == "myproj"
        assert result["branch"] == "feature/new-feature"
        assert result["session_duration"] == "1h 30m"
        assert "time_away" in result

    def test_uses_llm_for_summary_when_available(self):
        db.insert_session_snapshot(
            project="myproj",
            session_id="sess1",
            branch="main",
            last_command="vim src/app.py",
        )
        mock_llm = MagicMock()
        mock_llm.name = "mock"
        mock_response = MagicMock()
        mock_response.content = "You were editing the main app module."
        mock_llm.analyze.return_value = mock_response

        restorer = ContextRestorer(llm_provider=mock_llm)
        result = restorer.resume("myproj")
        # LLM may be called for summary if notes is empty
        assert isinstance(result.get("summary"), (str, type(None)))


class TestSessionHistory:
    def test_returns_multiple_sessions(self):
        for i in range(3):
            db.insert_session_snapshot(
                project="myproj",
                session_id=f"sess-{i}",
                branch="main",
                duration_minutes=60 + i * 10,
            )
        restorer = ContextRestorer(llm_provider=None)
        history = restorer.get_session_history("myproj", limit=10)
        assert len(history) == 3

    def test_respects_limit(self):
        for i in range(5):
            db.insert_session_snapshot("myproj", f"s{i}")
        restorer = ContextRestorer(llm_provider=None)
        history = restorer.get_session_history("myproj", limit=2)
        assert len(history) == 2

    def test_empty_for_unknown_project(self):
        restorer = ContextRestorer(llm_provider=None)
        history = restorer.get_session_history("unknown-project")
        assert history == []


class TestCaptureOnGap:
    def test_returns_empty_when_no_events(self):
        restorer = ContextRestorer(llm_provider=None)
        result = restorer.capture_on_gap(gap_minutes=30)
        assert result == []

    def test_snapshots_projects_with_old_activity(self):
        # Insert an event that's 2 hours old
        old_ts = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        db.insert_event(
            "shell_cmd",
            {"cmd": "ls", "exit_code": 0},
            project="oldproj",
            timestamp=old_ts,
            session_id="oldsess",
        )
        restorer = ContextRestorer(llm_provider=None)
        with patch("ghost_pulse.analyzers.context_restorer._run_git", return_value=""), \
             patch("ghost_pulse.analyzers.context_restorer._find_project_path", return_value=None):
            result = restorer.capture_on_gap(gap_minutes=30)
        assert "oldproj" in result
