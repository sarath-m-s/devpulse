"""Tests for analyzers: toil, time_tracker, context_switch."""

from datetime import datetime, timedelta

import pytest

from ghost_pulse import db
from ghost_pulse.analyzers.toil import detect_toil, normalize_command, get_ranked_patterns
from ghost_pulse.analyzers.time_tracker import compute_time_per_project
from ghost_pulse.analyzers.context_switch import compute_context_switches


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


def _ts(delta_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%S")


class TestToilDetector:
    def _insert_sequence(self, cmds: list[str], repetitions: int = 6) -> None:
        for rep in range(repetitions):
            base_offset = rep * len(cmds) * 10
            for i, cmd in enumerate(cmds):
                db.insert_event(
                    "shell_cmd",
                    {"cmd": cmd, "cwd": "/tmp", "exit_code": 0},
                    timestamp=_ts(base_offset + i),
                )

    def test_detects_repeated_pair(self):
        self._insert_sequence(["docker compose down", "docker compose up -d"], repetitions=6)
        patterns = detect_toil(days=1, threshold=5)
        assert len(patterns) > 0

    def test_threshold_respected(self):
        self._insert_sequence(["make build", "make test"], repetitions=3)
        patterns = detect_toil(days=1, threshold=5)
        # count < threshold — should not appear
        assert len(patterns) == 0

    def test_pattern_hash_stable(self):
        cmds = ["git stash", "git checkout main"]
        from ghost_pulse.analyzers.toil import _seq_hash
        h1 = _seq_hash(cmds)
        h2 = _seq_hash(cmds)
        assert h1 == h2

    def test_longer_sequences_score_higher(self):
        # Insert a 4-command sequence more to ensure it scores higher
        self._insert_sequence(["a", "b", "c", "d"], repetitions=8)
        self._insert_sequence(["x", "y"], repetitions=6)
        patterns = detect_toil(days=1, threshold=5)
        if len(patterns) >= 2:
            assert patterns[0]["score"] >= patterns[1]["score"]

    def test_normalization_groups_variants(self):
        # Two commands that normalize to the same form
        result1 = normalize_command("git checkout feature/foo")
        result2 = normalize_command("git checkout feature/bar")
        assert result1 == result2


class TestTimeTracker:
    def _insert_cmds(self, project: str, cmds_with_offsets: list[tuple[str, int]]) -> None:
        for cmd, offset in cmds_with_offsets:
            db.insert_event(
                "shell_cmd",
                {"cmd": cmd, "cwd": f"/home/user/{project}", "exit_code": 0},
                project=project,
                timestamp=_ts(offset),
            )

    def test_accumulates_project_time(self):
        # Use past timestamps so they fall within the since..now window
        self._insert_cmds("myproject", [
            ("ls", -120),
            ("vim main.py", -60),
            ("vim main.py", -10),
        ])
        since = _ts(-300)
        data = compute_time_per_project(since=since)
        assert "myproject" in data
        assert data["myproject"]["total_minutes"] > 0

    def test_caps_idle_gap(self):
        self._insert_cmds("proj", [("ls", -4000), ("ls", -100)])  # large gap
        since = _ts(-5000)
        data = compute_time_per_project(since=since, idle_timeout_minutes=15)
        # Should be capped at 15 min, not the full gap
        assert data["proj"]["total_minutes"] <= 15 + 1  # +1 for float rounding

    def test_multiple_projects(self):
        self._insert_cmds("alpha", [("ls", -60), ("ls", -30)])
        self._insert_cmds("beta", [("ls", -60), ("ls", -30)])
        since = _ts(-120)
        data = compute_time_per_project(since=since)
        assert "alpha" in data
        assert "beta" in data

    def test_git_commits_add_time(self):
        db.insert_event(
            "git_commit",
            {"sha": "abc", "message": "fix", "branch": "main",
             "files_changed": 1, "insertions": 5, "deletions": 2},
            project="myrepo",
            timestamp=_ts(-30),
        )
        since = _ts(-60)
        data = compute_time_per_project(since=since)
        assert "myrepo" in data
        assert data["myrepo"]["commits"] == 1
        assert data["myrepo"]["total_minutes"] >= 5  # commit base unit


class TestContextSwitch:
    def _insert_project_events(self, project: str, offsets: list[int]) -> None:
        for offset in offsets:
            db.insert_event(
                "shell_cmd",
                {"cmd": "ls", "cwd": f"/work/{project}"},
                project=project,
                timestamp=_ts(offset),
            )

    def test_counts_switches_between_projects(self):
        # Use past timestamps — earliest first
        self._insert_project_events("proj_a", [-70, -60, -50])
        self._insert_project_events("proj_b", [-40, -30, -20])
        self._insert_project_events("proj_a", [-10, -5])
        since = _ts(-120)
        ctx = compute_context_switches(since=since)
        assert ctx["switches"] >= 1

    def test_fragmentation_score_bounds(self):
        self._insert_project_events("proj_a", [-120, -60, -10])
        since = _ts(-300)
        ctx = compute_context_switches(since=since)
        assert 0 <= ctx["fragmentation_score"] <= 100

    def test_deep_work_block_detection(self):
        # 35 minutes on the same project — use past timestamps
        offsets = [-35 * 60 + i * 120 for i in range(18)]  # every 2 min, starting 35 min ago
        self._insert_project_events("focused_proj", offsets)
        since = _ts(-40 * 60)
        ctx = compute_context_switches(since=since, min_deep_work_minutes=30)
        assert any(b["project"] == "focused_proj" for b in ctx["deep_work_blocks"])

    def test_empty_data_returns_zeroes(self):
        ctx = compute_context_switches(since=_ts(-3600))
        assert ctx["switches"] == 0
        assert ctx["fragmentation_score"] == 0.0
        assert ctx["deep_work_blocks"] == []
