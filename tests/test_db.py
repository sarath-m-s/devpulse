"""Tests for the database layer."""

import json
import threading
from pathlib import Path

import pytest

from ghost_pulse import db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    """Point all DB operations at a temporary file for test isolation."""
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


class TestInitDb:
    def test_creates_tables(self, tmp_path):
        import sqlite3
        conn = sqlite3.connect(str(db.get_db_path()))
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        conn.close()
        assert "events" in tables
        assert "toil_patterns" in tables
        assert "daily_summaries" in tables

    def test_idempotent(self):
        db.init_db()  # calling twice should not raise


class TestInsertAndQueryEvents:
    def test_insert_basic(self):
        row_id = db.insert_event("shell_cmd", {"cmd": "ls"})
        assert row_id is not None and row_id > 0

    def test_query_by_type(self):
        db.insert_event("shell_cmd", {"cmd": "ls"})
        db.insert_event("git_commit", {"sha": "abc"})
        cmds = db.query_events(event_type="shell_cmd")
        assert all(e["event_type"] == "shell_cmd" for e in cmds)
        assert len(cmds) == 1

    def test_query_by_project(self):
        db.insert_event("shell_cmd", {"cmd": "ls"}, project="proj_a")
        db.insert_event("shell_cmd", {"cmd": "ls"}, project="proj_b")
        events = db.query_events(project="proj_a")
        assert len(events) == 1
        assert events[0]["project"] == "proj_a"

    def test_data_round_trips_as_dict(self):
        db.insert_event("shell_cmd", {"cmd": "echo hello", "exit_code": 0})
        events = db.query_events(event_type="shell_cmd")
        assert isinstance(events[0]["data"], dict)
        assert events[0]["data"]["cmd"] == "echo hello"

    def test_query_with_time_window(self):
        db.insert_event("shell_cmd", {"cmd": "old"}, timestamp="2020-01-01T00:00:00")
        db.insert_event("shell_cmd", {"cmd": "new"}, timestamp="2030-01-01T00:00:00")
        events = db.query_events(since="2025-01-01T00:00:00")
        assert len(events) == 1
        assert events[0]["data"]["cmd"] == "new"

    def test_count_events_today(self):
        from datetime import datetime
        today_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        db.insert_event("shell_cmd", {"cmd": "pwd"}, timestamp=today_ts)
        assert db.count_events_today() >= 1


class TestToilPatterns:
    def test_upsert_creates_new(self):
        db.upsert_toil_pattern("hash1", ["git status", "git diff"])
        patterns = db.get_toil_patterns()
        assert len(patterns) == 1
        assert patterns[0]["pattern_hash"] == "hash1"

    def test_upsert_increments_count(self):
        db.upsert_toil_pattern("hash1", ["ls"])
        db.upsert_toil_pattern("hash1", ["ls"])
        db.upsert_toil_pattern("hash1", ["ls"])
        patterns = db.get_toil_patterns()
        assert patterns[0]["count"] == 3

    def test_dismiss_hides_pattern(self):
        db.upsert_toil_pattern("hash1", ["ls"])
        pattern_id = db.get_toil_patterns()[0]["id"]
        db.dismiss_toil_pattern(pattern_id)
        assert len(db.get_toil_patterns()) == 0
        assert len(db.get_toil_patterns(include_dismissed=True)) == 1

    def test_update_automation(self):
        db.upsert_toil_pattern("hash1", ["make build"])
        pid = db.get_toil_patterns()[0]["id"]
        db.update_toil_automation(pid, "alias mb='make build'")
        patterns = db.get_toil_patterns()
        assert patterns[0]["automation"] == "alias mb='make build'"


class TestDailySummaries:
    def test_upsert_and_retrieve(self):
        db.upsert_daily_summary(
            date="2026-01-01",
            total_commands=42,
            total_commits=3,
            context_switches=8,
            fragmentation_score=45.0,
            top_projects=[{"name": "foo", "minutes": 120}],
        )
        summaries = db.get_daily_summaries(days=365)
        assert len(summaries) == 1
        assert summaries[0]["total_commands"] == 42
        assert summaries[0]["top_projects"][0]["name"] == "foo"

    def test_upsert_overwrites(self):
        for v in (10, 20):
            db.upsert_daily_summary("2026-01-01", v, 0, 0, 0.0, [])
        summaries = db.get_daily_summaries(days=365)
        assert len(summaries) == 1
        assert summaries[0]["total_commands"] == 20


class TestCleanup:
    def test_cleanup_old_events(self):
        db.insert_event("shell_cmd", {"cmd": "old"}, timestamp="2000-01-01T00:00:00")
        db.insert_event("shell_cmd", {"cmd": "new"})
        deleted = db.cleanup_old_events(days=90)
        assert deleted == 1
        events = db.query_events()
        assert all("old" not in str(e) for e in events)


class TestConcurrentAccess:
    def test_concurrent_inserts(self):
        errors = []

        def insert_many(n):
            try:
                for i in range(n):
                    db.insert_event("shell_cmd", {"cmd": f"cmd-{i}"})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=insert_many, args=(20,)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        events = db.query_events(event_type="shell_cmd")
        assert len(events) == 100
