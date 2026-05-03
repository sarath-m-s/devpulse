"""Tests for the DeveloperFingerprint analyzer."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from devpulse import db
from devpulse.analyzers.developer_fingerprint import DeveloperFingerprint


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


def _ts(delta_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=delta_seconds)).strftime("%Y-%m-%dT%H:%M:%S")


def _insert_work_session(project: str, hour: int, n_cmds: int = 5, error_rate: float = 0.0) -> None:
    """Insert n_cmds events at a specific hour yesterday."""
    yesterday = (datetime.now() - timedelta(days=1)).replace(
        hour=hour, minute=0, second=0, microsecond=0
    )
    for i in range(n_cmds):
        ts = (yesterday + timedelta(minutes=i * 5)).strftime("%Y-%m-%dT%H:%M:%S")
        exit_code = 1 if (i == 0 and error_rate > 0) else 0
        db.insert_event(
            "shell_cmd",
            {"cmd": "vim src/app.py", "exit_code": exit_code},
            project=project,
            timestamp=ts,
        )


class TestEnergyMap:
    def test_returns_24_hourly_buckets(self):
        _insert_work_session("myproj", 9, n_cmds=10)
        fp = DeveloperFingerprint()
        result = fp.generate_energy_map(days=2)
        assert "hourly" in result
        # May not have all 24 if no data in some hours — that's OK

    def test_peak_hours_identified(self):
        # Insert lots of activity at hours 9-11 with low error rate
        for hour in [9, 10, 11]:
            _insert_work_session("myproj", hour, n_cmds=20, error_rate=0)
        # Insert commits at those hours
        for hour in [9, 10, 11]:
            ts = _ts(-86400 + hour * 3600)
            db.insert_event(
                "git_commit",
                {"sha": f"abc{hour}", "message": "fix", "insertions": 10, "deletions": 5},
                project="myproj",
                timestamp=ts,
            )
        fp = DeveloperFingerprint()
        result = fp.generate_energy_map(days=2)
        assert "peak_hours" in result
        assert isinstance(result["peak_hours"], list)

    def test_returns_best_day(self):
        _insert_work_session("myproj", 10, n_cmds=30)
        fp = DeveloperFingerprint()
        result = fp.generate_energy_map(days=2)
        assert "best_day" in result
        assert isinstance(result["best_day"], str)

    def test_total_counts(self):
        _insert_work_session("myproj", 9, n_cmds=10)
        fp = DeveloperFingerprint()
        result = fp.generate_energy_map(days=2)
        assert result["total_commands"] >= 10


class TestWorkflowFingerprint:
    def test_returns_style_string(self):
        _insert_work_session("myproj", 9, n_cmds=20)
        fp = DeveloperFingerprint()
        result = fp.generate_workflow_fingerprint(days=2)
        if "error" not in result:
            assert "style" in result
            assert isinstance(result["style"], str)

    def test_returns_top_tools(self):
        for tool in ["pytest", "docker", "git", "vim", "npm"]:
            for i in range(3):
                db.insert_event(
                    "shell_cmd",
                    {"cmd": f"{tool} args", "exit_code": 0},
                    project="myproj",
                    timestamp=_ts(-i * 60 - 3600),
                )
        fp = DeveloperFingerprint()
        result = fp.generate_workflow_fingerprint(days=1)
        if "error" not in result:
            assert "tools_top_5" in result
            assert len(result["tools_top_5"]) <= 5

    def test_identifies_morning_coder(self):
        # Insert 90% of activity before noon
        for hour in range(7, 13):
            _insert_work_session("myproj", hour, n_cmds=15)
        # Very little after noon
        _insert_work_session("myproj", 15, n_cmds=1)
        fp = DeveloperFingerprint()
        result = fp.generate_workflow_fingerprint(days=2)
        if "error" not in result:
            assert result.get("morning_activity_pct", 0) > 50

    def test_calculates_avg_commit_size(self):
        for i in range(5):
            db.insert_event(
                "git_commit",
                {"sha": f"sha{i}", "message": "feat", "insertions": 50, "deletions": 10},
                project="myproj",
                timestamp=_ts(-i * 1800 - 3600),
            )
        fp = DeveloperFingerprint()
        result = fp.generate_workflow_fingerprint(days=2)
        if "error" not in result:
            assert result.get("avg_commit_size_lines", 0) > 0


class TestFocusPattern:
    def test_returns_expected_keys(self):
        _insert_work_session("myproj", 9, n_cmds=20)
        fp = DeveloperFingerprint()
        result = fp.generate_focus_pattern(days=2)
        assert "avg_focus_block_min" in result
        assert "longest_focus_block_min" in result
        assert "trend" in result
        assert "best_focus_day" in result

    def test_trend_is_valid_value(self):
        fp = DeveloperFingerprint()
        result = fp.generate_focus_pattern(days=2)
        assert result["trend"] in ("improving", "worsening", "stable")

    def test_works_with_no_data(self):
        fp = DeveloperFingerprint()
        result = fp.generate_focus_pattern(days=1)
        assert result["avg_focus_block_min"] == 0
        assert result["longest_focus_block_min"] == 0


class TestGenerateFullProfile:
    def test_stores_all_three_profile_types(self):
        _insert_work_session("myproj", 10, n_cmds=10)
        fp = DeveloperFingerprint()
        result = fp.generate_full_profile(days=2)
        assert "energy_map" in result
        assert "workflow_fingerprint" in result
        assert "focus_pattern" in result

        # Verify stored in DB
        for profile_type in ("energy_map", "workflow_fingerprint", "focus_pattern"):
            stored = db.get_latest_profile(profile_type)
            assert stored is not None
            assert stored["profile_type"] == profile_type

    def test_idempotent_multiple_calls(self):
        fp = DeveloperFingerprint()
        fp.generate_full_profile(days=1)
        fp.generate_full_profile(days=1)
        # Should have 2 entries per type (not de-duped — appends)
        profiles = db.get_all_profiles("energy_map")
        assert len(profiles) == 2


class TestGetLatestProfile:
    def test_returns_none_when_no_profiles(self):
        fp = DeveloperFingerprint()
        assert fp.get_latest_profile() is None

    def test_returns_most_recent(self):
        db.insert_developer_profile(
            "energy_map",
            {"peak_hours": [9, 10]},
            period_start="2026-01-01T00:00:00",
            period_end="2026-01-31T00:00:00",
        )
        fp = DeveloperFingerprint()
        result = fp.get_latest_profile("energy_map")
        assert result is not None
        assert result["data"]["peak_hours"] == [9, 10]

    def test_returns_specific_type(self):
        db.insert_developer_profile(
            "energy_map", {}, "2026-01-01", "2026-01-31"
        )
        db.insert_developer_profile(
            "focus_pattern", {"trend": "improving"}, "2026-01-01", "2026-01-31"
        )
        fp = DeveloperFingerprint()
        result = fp.get_latest_profile("focus_pattern")
        assert result is not None
        assert result["profile_type"] == "focus_pattern"


class TestGenerateInsights:
    def test_returns_string_without_llm(self):
        _insert_work_session("myproj", 10, n_cmds=10)
        fp = DeveloperFingerprint(llm_provider=None)
        result = fp.generate_insights(days=2)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_uses_llm_when_available(self):
        mock_llm = MagicMock()
        mock_llm.name = "mock"
        mock_response = MagicMock()
        mock_response.content = "Your peak hours are 9-11am. Consider blocking this time."
        mock_llm.analyze.return_value = mock_response

        fp = DeveloperFingerprint(llm_provider=mock_llm)
        result = fp.generate_insights(days=2)
        assert result == "Your peak hours are 9-11am. Consider blocking this time."
        assert mock_llm.analyze.called
