"""Fix window tracker — lifecycle management for error → fix journeys.

A "fix window" opens when a command fails and closes when:
  - a subsequent command succeeds (auto-resolved)
  - the developer runs `devpulse fix-done` (manually resolved)
  - a git commit is detected (commit-resolved)
  - the window exceeds the expiry threshold (abandoned)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from devpulse import db
from devpulse.analyzers.toil import normalize_command
from devpulse.analyzers.error_memory import _error_hash


# Max age before a window is considered abandoned
_WINDOW_EXPIRY_HOURS = 4


def open_fix_window(
    command: str,
    exit_code: int,
    project: str = "",
    error_memory_id: int | None = None,
) -> int:
    """Open a new fix window for a failing command. Returns window id."""
    if exit_code == 0:
        return -1
    ehash = _error_hash(command, exit_code)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with db._write_lock, db._get_conn() as conn:
        # Only one open window per error_hash at a time
        existing = conn.execute(
            "SELECT id FROM fix_windows WHERE error_hash=? AND status='open'",
            (ehash,),
        ).fetchone()
        if existing:
            return existing["id"]
        cur = conn.execute(
            """INSERT INTO fix_windows
               (error_hash, error_memory_id, project, started_at, status, commands_after, files_changed)
               VALUES (?,?,?,?,'open','[]','[]')""",
            (ehash, error_memory_id, project or None, now),
        )
        return cur.lastrowid  # type: ignore[return-value]


def track_command(window_id: int, command: str) -> None:
    """Append a command to an open fix window's command trail."""
    if window_id <= 0:
        return
    with db._write_lock, db._get_conn() as conn:
        row = conn.execute(
            "SELECT commands_after, status FROM fix_windows WHERE id=?",
            (window_id,),
        ).fetchone()
        if not row or row["status"] != "open":
            return
        try:
            cmds = json.loads(row["commands_after"]) if row["commands_after"] else []
        except (json.JSONDecodeError, TypeError):
            cmds = []
        cmds.append(command)
        conn.execute(
            "UPDATE fix_windows SET commands_after=? WHERE id=?",
            (json.dumps(cmds), window_id),
        )


def track_file_change(window_id: int, filepath: str) -> None:
    """Append a changed file to an open fix window."""
    if window_id <= 0:
        return
    with db._write_lock, db._get_conn() as conn:
        row = conn.execute(
            "SELECT files_changed, status FROM fix_windows WHERE id=?",
            (window_id,),
        ).fetchone()
        if not row or row["status"] != "open":
            return
        try:
            files = json.loads(row["files_changed"]) if row["files_changed"] else []
        except (json.JSONDecodeError, TypeError):
            files = []
        if filepath not in files:
            files.append(filepath)
        conn.execute(
            "UPDATE fix_windows SET files_changed=? WHERE id=?",
            (json.dumps(files), window_id),
        )


def close_fix_window(
    window_id: int,
    resolution: str = "auto",
    commit_sha: str | None = None,
) -> dict[str, Any] | None:
    """Close a fix window and build a fix record from its data.

    Returns the closed window dict or None if not found / already closed.
    """
    if window_id <= 0:
        return None
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with db._write_lock, db._get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM fix_windows WHERE id=?", (window_id,)
        ).fetchone()
        if not row:
            return None
        if row["status"] != "open":
            return dict(row)

        started = datetime.strptime(row["started_at"][:19], "%Y-%m-%dT%H:%M:%S")
        duration_ms = int((datetime.now() - started).total_seconds() * 1000)

        conn.execute(
            """UPDATE fix_windows
               SET status=?, closed_at=?, commit_sha=?, fix_duration_ms=?
               WHERE id=?""",
            (resolution, now, commit_sha, duration_ms, window_id),
        )
        updated = conn.execute(
            "SELECT * FROM fix_windows WHERE id=?", (window_id,)
        ).fetchone()
        return dict(updated)


def close_fix_window_by_hash(
    error_hash: str,
    resolution: str = "auto",
    commit_sha: str | None = None,
) -> dict[str, Any] | None:
    """Close an open fix window by its error hash."""
    with db._get_conn(readonly=True) as conn:
        row = conn.execute(
            "SELECT id FROM fix_windows WHERE error_hash=? AND status='open'",
            (error_hash,),
        ).fetchone()
    if not row:
        return None
    return close_fix_window(row["id"], resolution=resolution, commit_sha=commit_sha)


def get_open_windows() -> list[dict[str, Any]]:
    """Return all currently open fix windows."""
    with db._get_conn(readonly=True) as conn:
        rows = conn.execute(
            "SELECT * FROM fix_windows WHERE status='open' ORDER BY started_at ASC",
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for key in ("commands_after", "files_changed"):
            try:
                d[key] = json.loads(d[key]) if d[key] else []
            except (json.JSONDecodeError, TypeError):
                d[key] = []
        result.append(d)
    return result


def get_fix_windows(
    project: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return fix windows with optional filters."""
    clauses: list[str] = []
    params: list[Any] = []
    if project:
        clauses.append("project=?")
        params.append(project)
    if status:
        clauses.append("status=?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with db._get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM fix_windows {where} ORDER BY started_at DESC LIMIT ?",
            params,
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for key in ("commands_after", "files_changed"):
            try:
                d[key] = json.loads(d[key]) if d[key] else []
            except (json.JSONDecodeError, TypeError):
                d[key] = []
        result.append(d)
    return result


def expire_stale_windows(expiry_hours: int = _WINDOW_EXPIRY_HOURS) -> int:
    """Mark windows older than expiry_hours as 'abandoned'. Returns count."""
    cutoff = (datetime.now() - timedelta(hours=expiry_hours)).strftime("%Y-%m-%dT%H:%M:%S")
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with db._write_lock, db._get_conn() as conn:
        cur = conn.execute(
            """UPDATE fix_windows
               SET status='abandoned', closed_at=?
               WHERE status='open' AND started_at < ?""",
            (now, cutoff),
        )
        return cur.rowcount
