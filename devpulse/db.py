"""SQLite database layer for DevPulse."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Generator

_DB_PATH: Path | None = None
_write_lock = threading.Lock()


def set_db_path(path: Path) -> None:
    """Set the database file path (called during init)."""
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> Path:
    """Return configured DB path, defaulting to ~/.devpulse/devpulse.db."""
    if _DB_PATH is not None:
        return _DB_PATH
    return Path.home() / ".devpulse" / "devpulse.db"


@contextmanager
def _get_conn(readonly: bool = False) -> Generator[sqlite3.Connection, None, None]:
    """Open a connection, configure it, and close on exit."""
    db = get_db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if they don't exist."""
    with _write_lock, _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%S','now','localtime')),
                event_type  TEXT NOT NULL,
                data        JSON NOT NULL,
                project     TEXT,
                session_id  TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_events_timestamp   ON events(timestamp);
            CREATE INDEX IF NOT EXISTS idx_events_event_type  ON events(event_type);
            CREATE INDEX IF NOT EXISTS idx_events_project     ON events(project);

            CREATE TABLE IF NOT EXISTS toil_patterns (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash  TEXT UNIQUE NOT NULL,
                commands      JSON NOT NULL,
                count         INTEGER DEFAULT 1,
                first_seen    TEXT NOT NULL,
                last_seen     TEXT NOT NULL,
                automation    TEXT,
                dismissed     BOOLEAN DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS daily_summaries (
                date                TEXT PRIMARY KEY,
                total_commands      INTEGER,
                total_commits       INTEGER,
                context_switches    INTEGER,
                fragmentation_score REAL,
                top_projects        JSON,
                generated_at        TEXT
            );
        """)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------

def insert_event(
    event_type: str,
    data: dict[str, Any],
    project: str | None = None,
    session_id: str | None = None,
    timestamp: str | None = None,
) -> int:
    """Insert a single event and return its rowid."""
    ts = timestamp or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO events (timestamp, event_type, data, project, session_id) VALUES (?,?,?,?,?)",
            (ts, event_type, json.dumps(data), project, session_id),
        )
        return cur.lastrowid  # type: ignore[return-value]


def query_events(
    event_type: str | None = None,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 10_000,
) -> list[dict[str, Any]]:
    """Query events with optional filters."""
    clauses: list[str] = []
    params: list[Any] = []

    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if project:
        clauses.append("project = ?")
        params.append(project)
    if since:
        clauses.append("timestamp >= ?")
        params.append(since)
    if until:
        clauses.append("timestamp <= ?")
        params.append(until)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)

    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY timestamp ASC LIMIT ?",
            params,
        ).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        try:
            d["data"] = json.loads(d["data"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def count_events_today() -> int:
    today = datetime.now().strftime("%Y-%m-%d")
    with _get_conn(readonly=True) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM events WHERE timestamp >= ?",
            (today + "T00:00:00",),
        ).fetchone()
    return row["n"] if row else 0


# ---------------------------------------------------------------------------
# Toil pattern helpers
# ---------------------------------------------------------------------------

def upsert_toil_pattern(
    pattern_hash: str,
    commands: list[str],
    count: int = 1,
    now: str | None = None,
) -> None:
    ts = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        existing = conn.execute(
            "SELECT id, count FROM toil_patterns WHERE pattern_hash = ?",
            (pattern_hash,),
        ).fetchone()
        if existing:
            # Keep the higher observed count so re-runs don't lose data
            new_count = max(existing["count"], count)
            conn.execute(
                "UPDATE toil_patterns SET count = ?, last_seen = ? WHERE pattern_hash = ?",
                (new_count, ts, pattern_hash),
            )
        else:
            conn.execute(
                """INSERT INTO toil_patterns
                   (pattern_hash, commands, count, first_seen, last_seen)
                   VALUES (?, ?, ?, ?, ?)""",
                (pattern_hash, json.dumps(commands), count, ts, ts),
            )


def get_toil_patterns(include_dismissed: bool = False) -> list[dict[str, Any]]:
    where = "" if include_dismissed else "WHERE dismissed = 0"
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM toil_patterns {where} ORDER BY count DESC",
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["commands"] = json.loads(d["commands"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def update_toil_automation(pattern_id: int, automation: str) -> None:
    with _write_lock, _get_conn() as conn:
        conn.execute(
            "UPDATE toil_patterns SET automation = ? WHERE id = ?",
            (automation, pattern_id),
        )


def dismiss_toil_pattern(pattern_id: int) -> None:
    with _write_lock, _get_conn() as conn:
        conn.execute(
            "UPDATE toil_patterns SET dismissed = 1 WHERE id = ?",
            (pattern_id,),
        )


# ---------------------------------------------------------------------------
# Daily summary helpers
# ---------------------------------------------------------------------------

def upsert_daily_summary(
    date: str,
    total_commands: int,
    total_commits: int,
    context_switches: int,
    fragmentation_score: float,
    top_projects: list[dict[str, Any]],
) -> None:
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_summaries
               (date, total_commands, total_commits, context_switches,
                fragmentation_score, top_projects, generated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                   total_commands=excluded.total_commands,
                   total_commits=excluded.total_commits,
                   context_switches=excluded.context_switches,
                   fragmentation_score=excluded.fragmentation_score,
                   top_projects=excluded.top_projects,
                   generated_at=excluded.generated_at""",
            (date, total_commands, total_commits, context_switches,
             fragmentation_score, json.dumps(top_projects), now),
        )


def get_daily_summaries(days: int = 7) -> list[dict[str, Any]]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            "SELECT * FROM daily_summaries WHERE date >= ? ORDER BY date ASC",
            (since,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["top_projects"] = json.loads(d["top_projects"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def cleanup_old_events(days: int = 90) -> int:
    """Delete events older than `days` days. Returns rows deleted."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
        return cur.rowcount
