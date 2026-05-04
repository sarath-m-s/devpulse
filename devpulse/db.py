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

            -- v2: Learned workflow sequences per project
            CREATE TABLE IF NOT EXISTS workflow_sequences (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                project          TEXT NOT NULL,
                sequence_hash    TEXT NOT NULL,
                sequence         JSON NOT NULL,
                frequency        INTEGER DEFAULT 1,
                avg_duration_ms  INTEGER,
                last_seen        TEXT NOT NULL,
                first_seen       TEXT NOT NULL,
                confidence       REAL DEFAULT 0.0,
                is_active        BOOLEAN DEFAULT 1
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_seq_hash ON workflow_sequences(project, sequence_hash);
            CREATE INDEX IF NOT EXISTS idx_workflow_sequences_project ON workflow_sequences(project);

            -- v2: Error → fix memory
            CREATE TABLE IF NOT EXISTS error_memory (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                error_hash           TEXT NOT NULL,
                error_pattern        TEXT NOT NULL,
                error_type           TEXT,
                project              TEXT,
                fix_description      TEXT,
                fix_commands         JSON,
                fix_diff             TEXT,
                occurrences          INTEGER DEFAULT 1,
                first_seen           TEXT NOT NULL,
                last_seen            TEXT NOT NULL,
                last_fix_duration_ms INTEGER,
                resolved             BOOLEAN DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_error_memory_hash ON error_memory(error_hash);
            CREATE INDEX IF NOT EXISTS idx_error_memory_project ON error_memory(project);

            -- v2: Session context snapshots
            CREATE TABLE IF NOT EXISTS session_snapshots (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                project          TEXT NOT NULL,
                session_id       TEXT NOT NULL,
                snapshot_time    TEXT NOT NULL,
                branch           TEXT,
                last_file_edited TEXT,
                last_command     TEXT,
                last_error       TEXT,
                unstaged_files   JSON,
                open_tasks       JSON,
                notes            TEXT,
                duration_minutes INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_session_snapshots_project ON session_snapshots(project);

            -- v2: Developer profile / fingerprint
            CREATE TABLE IF NOT EXISTS developer_profile (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_type  TEXT NOT NULL,
                data          JSON NOT NULL,
                generated_at  TEXT NOT NULL,
                period_start  TEXT NOT NULL,
                period_end    TEXT NOT NULL,
                model_used    TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_developer_profile_type ON developer_profile(profile_type);

            -- v2: Focus sessions
            CREATE TABLE IF NOT EXISTS focus_sessions (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                project              TEXT NOT NULL,
                started_at           TEXT NOT NULL,
                ended_at             TEXT,
                duration_minutes     REAL,
                interruption_count   INTEGER DEFAULT 0,
                interruption_sources JSON,
                quality_score        REAL,
                was_warned           BOOLEAN DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_focus_sessions_project ON focus_sessions(project);

            -- v3: RAG fix records — store rich fix data + embeddings
            CREATE TABLE IF NOT EXISTS fix_records (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                error_hash    TEXT NOT NULL,
                error_pattern TEXT NOT NULL,
                fix_summary   TEXT,
                fix_commands  JSON,
                fix_diff      TEXT,
                embedding     BLOB,
                project       TEXT,
                source        TEXT DEFAULT 'auto',
                created_at    TEXT NOT NULL,
                occurrences   INTEGER DEFAULT 1
            );
            CREATE INDEX IF NOT EXISTS idx_fix_records_hash    ON fix_records(error_hash);
            CREATE INDEX IF NOT EXISTS idx_fix_records_project ON fix_records(project);

            -- v3: Fix windows — track error→fix lifecycle
            CREATE TABLE IF NOT EXISTS fix_windows (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                error_hash       TEXT NOT NULL,
                error_memory_id  INTEGER,
                project          TEXT,
                started_at       TEXT NOT NULL,
                closed_at        TEXT,
                status           TEXT DEFAULT 'open',
                commands_after   JSON,
                files_changed    JSON,
                commit_sha       TEXT,
                fix_duration_ms  INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_fix_windows_hash   ON fix_windows(error_hash);
            CREATE INDEX IF NOT EXISTS idx_fix_windows_status ON fix_windows(status);
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
            new_count = existing["count"] + count
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


# ---------------------------------------------------------------------------
# v2: Workflow sequence helpers
# ---------------------------------------------------------------------------

def upsert_workflow_sequence(
    project: str,
    sequence_hash: str,
    sequence: list[str],
    frequency: int = 1,
    now: str | None = None,
) -> int:
    """Upsert a workflow sequence, setting frequency. Returns rowid."""
    ts = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        existing = conn.execute(
            "SELECT id, frequency FROM workflow_sequences WHERE project=? AND sequence_hash=?",
            (project, sequence_hash),
        ).fetchone()
        if existing:
            new_freq = max(existing["frequency"], frequency)
            confidence = min(1.0, new_freq / 20.0)
            conn.execute(
                "UPDATE workflow_sequences SET frequency=?, confidence=?, last_seen=? WHERE id=?",
                (new_freq, confidence, ts, existing["id"]),
            )
            return existing["id"]
        else:
            confidence = min(1.0, frequency / 20.0)
            cur = conn.execute(
                """INSERT INTO workflow_sequences
                   (project, sequence_hash, sequence, frequency, confidence, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?,?)""",
                (project, sequence_hash, json.dumps(sequence), frequency, confidence, ts, ts),
            )
            return cur.lastrowid  # type: ignore[return-value]


def get_workflow_sequences(
    project: str | None = None,
    min_confidence: float = 0.0,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    clauses = []
    params: list[Any] = []
    if project:
        clauses.append("project=?")
        params.append(project)
    if active_only:
        clauses.append("is_active=1")
    if min_confidence > 0:
        clauses.append("confidence>=?")
        params.append(min_confidence)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM workflow_sequences {where} ORDER BY frequency DESC",
            params,
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["sequence"] = json.loads(d["sequence"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def dismiss_workflow_sequence(seq_id: int) -> None:
    with _write_lock, _get_conn() as conn:
        conn.execute("UPDATE workflow_sequences SET is_active=0 WHERE id=?", (seq_id,))


# ---------------------------------------------------------------------------
# v2: Error memory helpers
# ---------------------------------------------------------------------------

def upsert_error_memory(
    error_hash: str,
    error_pattern: str,
    project: str | None = None,
    error_type: str | None = None,
    now: str | None = None,
) -> int:
    """Insert or increment an error occurrence. Returns error_memory id."""
    ts = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        existing = conn.execute(
            "SELECT id, occurrences FROM error_memory WHERE error_hash=?",
            (error_hash,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE error_memory SET occurrences=occurrences+1, last_seen=? WHERE id=?",
                (ts, existing["id"]),
            )
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO error_memory
                   (error_hash, error_pattern, project, error_type, first_seen, last_seen)
                   VALUES (?,?,?,?,?,?)""",
                (error_hash, error_pattern, project, error_type, ts, ts),
            )
            return cur.lastrowid  # type: ignore[return-value]


def update_error_fix(
    error_id: int,
    fix_commands: list[str],
    fix_description: str | None = None,
    fix_diff: str | None = None,
    fix_duration_ms: int | None = None,
) -> None:
    with _write_lock, _get_conn() as conn:
        conn.execute(
            """UPDATE error_memory
               SET fix_commands=?, fix_description=?, fix_diff=?,
                   last_fix_duration_ms=?, resolved=1
               WHERE id=?""",
            (
                json.dumps(fix_commands),
                fix_description,
                fix_diff,
                fix_duration_ms,
                error_id,
            ),
        )


def get_error_memory_by_hash(error_hash: str) -> dict[str, Any] | None:
    with _get_conn(readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM error_memory WHERE error_hash=?", (error_hash,)
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["fix_commands"] = json.loads(d["fix_commands"]) if d["fix_commands"] else []
    except (json.JSONDecodeError, TypeError):
        d["fix_commands"] = []
    return d


def get_frequent_errors(
    project: str | None = None,
    days: int = 30,
    limit: int = 10,
) -> list[dict[str, Any]]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    clauses = ["last_seen >= ?"]
    params: list[Any] = [since]
    if project:
        clauses.append("project=?")
        params.append(project)
    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM error_memory {where} ORDER BY occurrences DESC LIMIT ?",
            params,
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["fix_commands"] = json.loads(d["fix_commands"]) if d["fix_commands"] else []
        except (json.JSONDecodeError, TypeError):
            d["fix_commands"] = []
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# v2: Session snapshot helpers
# ---------------------------------------------------------------------------

def insert_session_snapshot(
    project: str,
    session_id: str,
    branch: str | None = None,
    last_file_edited: str | None = None,
    last_command: str | None = None,
    last_error: str | None = None,
    unstaged_files: list[str] | None = None,
    open_tasks: list[str] | None = None,
    notes: str | None = None,
    duration_minutes: int | None = None,
    now: str | None = None,
) -> int:
    ts = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO session_snapshots
               (project, session_id, snapshot_time, branch, last_file_edited,
                last_command, last_error, unstaged_files, open_tasks, notes, duration_minutes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                project, session_id, ts, branch, last_file_edited,
                last_command, last_error,
                json.dumps(unstaged_files or []),
                json.dumps(open_tasks or []),
                notes, duration_minutes,
            ),
        )
        return cur.lastrowid  # type: ignore[return-value]


def get_latest_snapshot(project: str) -> dict[str, Any] | None:
    with _get_conn(readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM session_snapshots WHERE project=? ORDER BY snapshot_time DESC LIMIT 1",
            (project,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in ("unstaged_files", "open_tasks"):
        try:
            d[key] = json.loads(d[key]) if d[key] else []
        except (json.JSONDecodeError, TypeError):
            d[key] = []
    return d


def get_session_history(project: str, limit: int = 10) -> list[dict[str, Any]]:
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            "SELECT * FROM session_snapshots WHERE project=? ORDER BY snapshot_time DESC LIMIT ?",
            (project, limit),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        for key in ("unstaged_files", "open_tasks"):
            try:
                d[key] = json.loads(d[key]) if d[key] else []
            except (json.JSONDecodeError, TypeError):
                d[key] = []
        result.append(d)
    return result


def get_all_snapshot_projects() -> list[str]:
    """Return distinct projects that have at least one session snapshot."""
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            """SELECT project, MAX(snapshot_time) as last_seen
               FROM session_snapshots GROUP BY project ORDER BY last_seen DESC"""
        ).fetchall()
    return [row["project"] for row in rows]


# ---------------------------------------------------------------------------
# v2: Developer profile helpers
# ---------------------------------------------------------------------------

def insert_developer_profile(
    profile_type: str,
    data: dict[str, Any],
    period_start: str,
    period_end: str,
    model_used: str | None = None,
    now: str | None = None,
) -> int:
    ts = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO developer_profile
               (profile_type, data, generated_at, period_start, period_end, model_used)
               VALUES (?,?,?,?,?,?)""",
            (profile_type, json.dumps(data), ts, period_start, period_end, model_used),
        )
        return cur.lastrowid  # type: ignore[return-value]


def get_latest_profile(profile_type: str | None = None) -> dict[str, Any] | None:
    clause = "WHERE profile_type=?" if profile_type else ""
    params = [profile_type] if profile_type else []
    with _get_conn(readonly=True) as conn:
        row = conn.execute(
            f"SELECT * FROM developer_profile {clause} ORDER BY generated_at DESC LIMIT 1",
            params,
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["data"] = json.loads(d["data"])
    except (json.JSONDecodeError, TypeError):
        pass
    return d


def get_all_profiles(profile_type: str | None = None) -> list[dict[str, Any]]:
    clause = "WHERE profile_type=?" if profile_type else ""
    params: list[Any] = [profile_type] if profile_type else []
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM developer_profile {clause} ORDER BY generated_at DESC",
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


# ---------------------------------------------------------------------------
# v2: Focus session helpers
# ---------------------------------------------------------------------------

def insert_focus_session(project: str, started_at: str) -> int:
    with _write_lock, _get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO focus_sessions (project, started_at) VALUES (?,?)",
            (project, started_at),
        )
        return cur.lastrowid  # type: ignore[return-value]


def close_focus_session(
    session_id: int,
    ended_at: str,
    duration_minutes: float,
    interruption_count: int,
    interruption_sources: list[str],
    quality_score: float,
    was_warned: bool = False,
) -> None:
    with _write_lock, _get_conn() as conn:
        conn.execute(
            """UPDATE focus_sessions
               SET ended_at=?, duration_minutes=?, interruption_count=?,
                   interruption_sources=?, quality_score=?, was_warned=?
               WHERE id=?""",
            (
                ended_at, duration_minutes, interruption_count,
                json.dumps(interruption_sources), quality_score, was_warned,
                session_id,
            ),
        )


def close_orphaned_focus_sessions(cutoff_minutes: int = 120) -> int:
    """Close any focus sessions that were left open (ended_at IS NULL).

    Called on daemon startup to clean up sessions from previous runs.
    Sessions older than cutoff_minutes get duration computed from started_at.
    Returns count closed.
    """
    cutoff = (datetime.now() - timedelta(minutes=cutoff_minutes)).strftime("%Y-%m-%dT%H:%M:%S")
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        orphans = conn.execute(
            "SELECT id, started_at FROM focus_sessions WHERE ended_at IS NULL",
        ).fetchall()
        count = 0
        for row in orphans:
            start_str = row["started_at"]
            try:
                start_dt = datetime.strptime(start_str[:19], "%Y-%m-%dT%H:%M:%S")
                duration = max(0.0, (datetime.now() - start_dt).total_seconds() / 60)
            except (ValueError, TypeError):
                duration = 0.0
            quality = min(100.0, (duration / 90.0) * 100)
            conn.execute(
                """UPDATE focus_sessions
                   SET ended_at=?, duration_minutes=?, quality_score=?, was_warned=0
                   WHERE id=?""",
                (now_str, round(duration, 1), round(quality, 1), row["id"]),
            )
            count += 1
    return count


def get_focus_sessions(since: str, project: str | None = None) -> list[dict[str, Any]]:
    clauses = ["started_at >= ?"]
    params: list[Any] = [since]
    if project:
        clauses.append("project=?")
        params.append(project)
    where = "WHERE " + " AND ".join(clauses)
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM focus_sessions {where} ORDER BY started_at ASC",
            params,
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["interruption_sources"] = json.loads(d["interruption_sources"]) if d["interruption_sources"] else []
        except (json.JSONDecodeError, TypeError):
            d["interruption_sources"] = []
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# v3: Fix records helpers (RAG)
# ---------------------------------------------------------------------------

def upsert_fix_record(
    error_hash: str,
    error_pattern: str,
    fix_summary: str | None = None,
    fix_commands: list[str] | None = None,
    fix_diff: str | None = None,
    project: str | None = None,
    source: str = "auto",
    now: str | None = None,
) -> int:
    """Insert or update a fix record. Returns row id."""
    ts = now or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _write_lock, _get_conn() as conn:
        existing = conn.execute(
            "SELECT id, occurrences FROM fix_records WHERE error_hash=?",
            (error_hash,),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE fix_records
                   SET fix_summary=COALESCE(?,fix_summary),
                       fix_commands=COALESCE(?,fix_commands),
                       fix_diff=COALESCE(?,fix_diff),
                       occurrences=occurrences+1
                   WHERE id=?""",
                (fix_summary, json.dumps(fix_commands) if fix_commands else None, fix_diff, existing["id"]),
            )
            return existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO fix_records
                   (error_hash, error_pattern, fix_summary, fix_commands, fix_diff, project, source, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    error_hash, error_pattern, fix_summary,
                    json.dumps(fix_commands or []), fix_diff,
                    project, source, ts,
                ),
            )
            return cur.lastrowid  # type: ignore[return-value]


def get_fix_records(
    project: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project:
        clauses.append("project=?")
        params.append(project)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    with _get_conn(readonly=True) as conn:
        rows = conn.execute(
            f"SELECT * FROM fix_records {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d.pop("embedding", None)  # never return raw blob
        try:
            d["fix_commands"] = json.loads(d["fix_commands"]) if d["fix_commands"] else []
        except (json.JSONDecodeError, TypeError):
            d["fix_commands"] = []
        result.append(d)
    return result


def get_fix_record_by_hash(error_hash: str) -> dict[str, Any] | None:
    with _get_conn(readonly=True) as conn:
        row = conn.execute(
            "SELECT * FROM fix_records WHERE error_hash=? ORDER BY created_at DESC LIMIT 1",
            (error_hash,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d.pop("embedding", None)
    try:
        d["fix_commands"] = json.loads(d["fix_commands"]) if d["fix_commands"] else []
    except (json.JSONDecodeError, TypeError):
        d["fix_commands"] = []
    return d


def get_fix_stats() -> dict[str, Any]:
    """Return aggregate stats for the fix intelligence panel."""
    with _get_conn(readonly=True) as conn:
        total = conn.execute("SELECT COUNT(*) as n FROM fix_records").fetchone()["n"]
        with_embed = conn.execute(
            "SELECT COUNT(*) as n FROM fix_records WHERE embedding IS NOT NULL"
        ).fetchone()["n"]
        windows_total = conn.execute("SELECT COUNT(*) as n FROM fix_windows").fetchone()["n"]
        windows_resolved = conn.execute(
            "SELECT COUNT(*) as n FROM fix_windows WHERE status IN ('auto','manual','commit-resolved')"
        ).fetchone()["n"]
        windows_open = conn.execute(
            "SELECT COUNT(*) as n FROM fix_windows WHERE status='open'"
        ).fetchone()["n"]
        avg_fix_ms = conn.execute(
            "SELECT AVG(fix_duration_ms) as a FROM fix_windows WHERE fix_duration_ms IS NOT NULL"
        ).fetchone()["a"]
    return {
        "fix_records_total": total,
        "fix_records_with_embedding": with_embed,
        "windows_total": windows_total,
        "windows_resolved": windows_resolved,
        "windows_open": windows_open,
        "avg_fix_duration_ms": round(avg_fix_ms) if avg_fix_ms else None,
    }
