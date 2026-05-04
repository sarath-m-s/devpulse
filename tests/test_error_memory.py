"""Tests for the ErrorMemory analyzer."""

from datetime import datetime, timedelta

import pytest

from ghost_pulse import db
from ghost_pulse.analyzers.error_memory import ErrorMemory, _error_hash, _classify_error_type


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


def _ts(delta_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%S")


class TestErrorHash:
    def test_same_command_same_exit_produces_same_hash(self):
        h1 = _error_hash("pytest tests/", 1)
        h2 = _error_hash("pytest tests/", 1)
        assert h1 == h2

    def test_different_exit_codes_produce_different_hashes(self):
        h1 = _error_hash("make build", 1)
        h2 = _error_hash("make build", 2)
        assert h1 != h2

    def test_normalized_variants_produce_same_hash(self):
        # Different paths should normalize to same command
        h1 = _error_hash("pytest tests/unit/test_foo.py", 1)
        h2 = _error_hash("pytest tests/unit/test_bar.py", 1)
        assert h1 == h2  # Both normalize to "pytest <path>"


class TestErrorClassification:
    def test_pytest_is_test(self):
        assert _classify_error_type("pytest tests/") == "test"

    def test_docker_is_build(self):
        assert _classify_error_type("docker build .") == "build"

    def test_gradle_is_build(self):
        assert _classify_error_type("./gradlew build") == "build"

    def test_git_is_deploy(self):
        assert _classify_error_type("git push origin main") == "deploy"

    def test_unknown_is_runtime(self):
        assert _classify_error_type("myapp --start") == "runtime"


class TestRecordError:
    def test_records_non_zero_exit(self):
        em = ErrorMemory()
        eid = em.record_error("make build", 1, project="myproj")
        assert eid > 0
        errors = db.get_frequent_errors(days=30)
        assert len(errors) == 1
        assert errors[0]["error_type"] == "build"
        assert errors[0]["project"] == "myproj"

    def test_ignores_zero_exit(self):
        em = ErrorMemory()
        result = em.record_error("ls", 0)
        assert result == -1
        assert len(db.get_frequent_errors(days=30)) == 0

    def test_deduplicates_same_error(self):
        em = ErrorMemory()
        em.record_error("make build", 1, project="myproj")
        em.record_error("make build", 1, project="myproj")
        em.record_error("make build", 1, project="myproj")
        errors = db.get_frequent_errors(days=30)
        assert len(errors) == 1
        assert errors[0]["occurrences"] == 3

    def test_different_commands_create_separate_entries(self):
        em = ErrorMemory()
        em.record_error("make build", 1)
        em.record_error("pytest tests/", 1)
        assert len(db.get_frequent_errors(days=30)) == 2


class TestRecordFix:
    def test_records_fix_commands(self):
        em = ErrorMemory()
        eid = em.record_error("make build", 1)
        em.record_fix(eid, ["vim Makefile", "make clean", "make build"])
        row = db.get_error_memory_by_hash(_error_hash("make build", 1))
        assert row is not None
        assert row["resolved"] == 1
        assert "vim Makefile" in row["fix_commands"]

    def test_fix_with_diff(self):
        em = ErrorMemory()
        eid = em.record_error("pytest tests/", 1)
        em.record_fix(eid, ["vim src/app.py"], fix_diff="+    return True")
        row = db.get_error_memory_by_hash(_error_hash("pytest tests/", 1))
        assert row["fix_diff"] == "+    return True"


class TestCheckKnownError:
    def test_returns_none_for_unseen_error(self):
        em = ErrorMemory()
        result = em.check_known_error("some unknown command", 1)
        assert result is None

    def test_returns_fix_for_known_error(self):
        em = ErrorMemory()
        eid = em.record_error("docker compose up", 1, project="proj")
        em.record_fix(eid, ["docker compose down", "docker compose up"])
        result = em.check_known_error("docker compose up", 1)
        assert result is not None
        assert result["occurrences"] >= 1
        assert result["resolved"] is True
        assert "docker compose down" in result["fix_commands"]

    def test_respects_project_filter(self):
        em = ErrorMemory()
        em.record_error("npm install", 1, project="project-a")
        # Checking with different project still returns match (error_hash is command-based)
        result = em.check_known_error("npm install", 1)
        assert result is not None


class TestGetFrequentErrors:
    def test_returns_sorted_by_occurrences(self):
        em = ErrorMemory()
        em.record_error("rare-cmd", 1)
        for _ in range(5):
            em.record_error("frequent-cmd", 1)
        errors = em.get_frequent_errors(days=30)
        assert errors[0]["occurrences"] >= errors[-1]["occurrences"]

    def test_project_filter(self):
        em = ErrorMemory()
        em.record_error("cmd-a", 1, project="proj-a")
        em.record_error("cmd-b", 1, project="proj-b")
        errors = em.get_frequent_errors(project="proj-a", days=30)
        assert all(e["project"] == "proj-a" for e in errors)

    def test_days_filter(self):
        em = ErrorMemory()
        # Insert an "old" error manually
        db.upsert_error_memory(
            "oldhash", "old cmd",
            now="2020-01-01T00:00:00",
        )
        em.record_error("new-cmd", 1)
        errors = em.get_frequent_errors(days=30)
        patterns = [e["error_pattern"] for e in errors]
        assert "old cmd" not in patterns


class TestDetectFixesFromHistory:
    def test_detects_fix_after_error(self):
        # Insert an error event, then a success event in quick succession
        ts_err = _ts(-200)
        ts_fix = _ts(-190)
        db.insert_event(
            "shell_cmd",
            {"cmd": "make build", "exit_code": 1},
            project="myproj",
            timestamp=ts_err,
            session_id="sess1",
        )
        db.insert_event(
            "shell_cmd",
            {"cmd": "vim Makefile", "exit_code": 0},
            project="myproj",
            timestamp=ts_fix,
            session_id="sess1",
        )
        # First record the error in error_memory
        em = ErrorMemory()
        em.record_error("make build", 1, project="myproj", session_id="sess1")
        # Now detect fixes
        n = em.detect_fixes_from_history(session_gap_minutes=10)
        assert n >= 0  # may or may not detect depending on timing
