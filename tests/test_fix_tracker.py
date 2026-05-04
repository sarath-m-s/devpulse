"""Tests for RAG fix window tracker."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ghost_pulse import db
from ghost_pulse.rag.fix_tracker import (
    close_fix_window,
    close_fix_window_by_hash,
    expire_stale_windows,
    get_open_windows,
    get_fix_windows,
    open_fix_window,
    track_command,
    track_file_change,
)
from ghost_pulse.analyzers.error_memory import _error_hash


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


def test_open_fix_window_returns_id():
    wid = open_fix_window("pytest tests/", exit_code=1, project="myapp")
    assert wid > 0


def test_open_fix_window_skips_success():
    wid = open_fix_window("pytest tests/", exit_code=0, project="myapp")
    assert wid == -1


def test_open_fix_window_deduplicates():
    wid1 = open_fix_window("pytest tests/", exit_code=1, project="myapp")
    wid2 = open_fix_window("pytest tests/", exit_code=1, project="myapp")
    assert wid1 == wid2  # same hash → same open window


def test_track_command_appends():
    wid = open_fix_window("npm run build", exit_code=1, project="web")
    track_command(wid, "npm install")
    track_command(wid, "npm run build")

    wins = get_open_windows()
    assert len(wins) == 1
    assert "npm install" in wins[0]["commands_after"]
    assert "npm run build" in wins[0]["commands_after"]


def test_track_file_change_appends():
    wid = open_fix_window("make", exit_code=2, project="c-lib")
    track_file_change(wid, "src/main.c")
    track_file_change(wid, "src/main.c")  # duplicate — should only appear once

    wins = get_open_windows()
    assert wins[0]["files_changed"].count("src/main.c") == 1


def test_close_fix_window():
    wid = open_fix_window("docker build .", exit_code=1, project="svc")
    track_command(wid, "docker system prune -f")
    closed = close_fix_window(wid, resolution="manual")

    assert closed["status"] == "manual"
    assert closed["closed_at"] is not None
    assert closed["fix_duration_ms"] >= 0

    # Should no longer appear in open windows
    assert not any(w["id"] == wid for w in get_open_windows())


def test_close_by_hash():
    cmd = "terraform apply"
    ehash = _error_hash(cmd, 1)
    open_fix_window(cmd, exit_code=1, project="infra")
    closed = close_fix_window_by_hash(ehash, resolution="commit-resolved")
    assert closed is not None
    assert closed["status"] == "commit-resolved"


def test_close_nonexistent_returns_none():
    result = close_fix_window(99999, resolution="auto")
    assert result is None


def test_get_fix_windows_filter_by_status():
    wid = open_fix_window("go build ./...", exit_code=1, project="gopher")
    close_fix_window(wid, resolution="auto")
    open_fix_window("go test ./...", exit_code=1, project="gopher")

    resolved = get_fix_windows(status="auto")
    assert len(resolved) == 1
    open_wins = get_fix_windows(status="open")
    assert len(open_wins) == 1


def test_expire_stale_windows(monkeypatch):
    wid = open_fix_window("cargo build", exit_code=1, project="rust")
    # Monkeypatch the window's started_at to be old
    with db._write_lock, db._get_conn() as conn:
        conn.execute(
            "UPDATE fix_windows SET started_at='2020-01-01T00:00:00' WHERE id=?",
            (wid,),
        )
    count = expire_stale_windows(expiry_hours=1)
    assert count == 1
    wins = get_open_windows()
    assert not any(w["id"] == wid for w in wins)


def test_track_command_ignores_closed_window():
    wid = open_fix_window("pip install foo", exit_code=1)
    close_fix_window(wid, resolution="auto")
    # Should silently ignore — no crash
    track_command(wid, "some other command")
    # Verify no new commands were appended
    wins = get_fix_windows(status="auto")
    assert all("some other command" not in w.get("commands_after", []) for w in wins)
