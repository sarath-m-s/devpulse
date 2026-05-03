"""Tests for collectors."""

import pytest

from devpulse import db
from devpulse.collectors.shell import log_command, _parse_zsh_history, _parse_bash_history
from devpulse.analyzers.toil import normalize_command


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


class TestCommandNormalization:
    def test_strips_git_branch(self):
        result = normalize_command("git checkout feature/my-branch")
        assert "<branch>" in result
        assert "feature/my-branch" not in result

    def test_strips_sha(self):
        result = normalize_command("git show abc1234def5678")
        assert "<sha>" in result

    def test_preserves_command_structure(self):
        result = normalize_command("docker compose down")
        assert "docker" in result
        assert "compose" in result
        assert "down" in result

    def test_handles_empty_string(self):
        assert normalize_command("") == ""

    def test_collapses_whitespace(self):
        assert "  " not in normalize_command("git   status")


class TestLogCommand:
    def test_stores_event(self):
        log_command("ls -la", "/tmp", 0, 42, "sess-1")
        events = db.query_events(event_type="shell_cmd")
        assert len(events) == 1
        assert events[0]["data"]["cmd"] == "ls -la"
        assert events[0]["data"]["exit_code"] == 0
        assert events[0]["data"]["duration_ms"] == 42
        assert events[0]["session_id"] == "sess-1"

    def test_infers_project_from_cwd(self, tmp_path):
        # Create a fake git repo
        git_dir = tmp_path / "myrepo"
        git_dir.mkdir()
        (git_dir / ".git").mkdir()
        log_command("ls", str(git_dir), 0, 0)
        events = db.query_events(event_type="shell_cmd")
        assert events[0]["project"] == "myrepo"

    def test_no_project_for_non_git(self, tmp_path):
        log_command("ls", str(tmp_path), 0, 0)
        events = db.query_events(event_type="shell_cmd")
        assert events[0]["project"] is None


class TestZshHistoryParsing:
    def test_extended_format(self, tmp_path):
        hist = tmp_path / ".zsh_history"
        hist.write_text(": 1700000000:0;git status\n: 1700000001:0;ls\n")
        entries = _parse_zsh_history(hist)
        assert len(entries) == 2
        assert entries[0]["cmd"] == "git status"
        assert entries[0]["timestamp"] is not None

    def test_simple_format(self, tmp_path):
        hist = tmp_path / ".zsh_history"
        hist.write_text("ls\npwd\ngit status\n")
        entries = _parse_zsh_history(hist)
        assert len(entries) == 3
        assert any(e["cmd"] == "git status" for e in entries)

    def test_missing_file(self, tmp_path):
        entries = _parse_zsh_history(tmp_path / "nonexistent")
        assert entries == []


class TestBashHistoryParsing:
    def test_timestamped_format(self, tmp_path):
        hist = tmp_path / ".bash_history"
        hist.write_text("#1700000000\ngit status\n#1700000001\nls\n")
        entries = _parse_bash_history(hist)
        assert len(entries) == 2
        assert entries[0]["cmd"] == "git status"
        assert entries[0]["timestamp"] is not None

    def test_simple_format(self, tmp_path):
        hist = tmp_path / ".bash_history"
        hist.write_text("ls\npwd\n")
        entries = _parse_bash_history(hist)
        assert len(entries) == 2

    def test_missing_file(self, tmp_path):
        entries = _parse_bash_history(tmp_path / "nonexistent")
        assert entries == []
