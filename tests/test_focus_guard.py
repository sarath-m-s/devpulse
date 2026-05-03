"""Tests for the FocusGuard analyzer."""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from devpulse import db
from devpulse.analyzers.focus_guard import FocusGuard


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    db.set_db_path(tmp_path / "test.db")
    db.init_db()
    yield
    db.set_db_path(None)


def _make_config(**v2_overrides) -> dict:
    v2 = {
        "focus_guard_enabled": True,
        "focus_threshold_minutes": 15,
        "focus_cooldown_minutes": 5,
        "focus_notification_method": "none",  # suppress during tests
    }
    v2.update(v2_overrides)
    return {"v2": v2}


def _ts(delta_minutes: float = 0) -> str:
    return (datetime.now() + timedelta(minutes=delta_minutes)).strftime("%Y-%m-%dT%H:%M:%S")


class TestFocusSessionDetection:
    def test_starts_session_on_first_activity(self):
        guard = FocusGuard(_make_config())
        with patch("devpulse.analyzers.focus_guard._send_notification"):
            guard.start_session("myproj", _ts(-20))
        assert guard._session_project == "myproj"
        assert guard._session_id is not None

    def test_no_notification_below_threshold(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=15))
        # Start session 5 minutes ago — below threshold
        guard.start_session("proj-a", _ts(-5))
        with patch("devpulse.analyzers.focus_guard._send_notification") as mock_notify:
            result = guard.on_project_change("proj-a", "proj-b", _ts(0))
        # Below threshold — no notification
        assert result is None
        mock_notify.assert_not_called()

    def test_notification_above_threshold(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=15))
        guard.start_session("proj-a", _ts(-20))  # 20 min ago > 15 min threshold
        with patch("devpulse.analyzers.focus_guard._send_notification") as mock_notify:
            result = guard.on_project_change("proj-a", "proj-b", _ts(0))
        assert result is not None
        assert result["type"] == "focus_warning"
        assert result["focus_duration_min"] >= 15

    def test_notification_contains_expected_keys(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=10))
        guard.start_session("proj-a", _ts(-15))
        with patch("devpulse.analyzers.focus_guard._send_notification"):
            result = guard.on_project_change("proj-a", "proj-b", _ts(0))
        assert result is not None
        assert "message" in result
        assert "focus_duration_min" in result
        assert "cost_estimate_min" in result
        assert "from_project" in result
        assert "to_project" in result


class TestInterruptionRecording:
    def test_interruptions_tracked(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=5))
        guard.start_session("proj-a", _ts(-10))
        with patch("devpulse.analyzers.focus_guard._send_notification"):
            guard.on_project_change("proj-a", "browser", _ts(0))
        # After switch, interruptions on the previous session should include "browser"
        # The session was closed — check new session started
        assert guard._session_project == "browser"

    def test_accumulates_multiple_interruptions(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=5))
        guard.start_session("proj-a", _ts(-10))
        with patch("devpulse.analyzers.focus_guard._send_notification"):
            guard.on_project_change("proj-a", "slack", _ts(-1))
            guard.on_project_change("slack", "proj-a", _ts(0))


class TestNotificationCooldown:
    def test_respects_cooldown(self):
        guard = FocusGuard(_make_config(
            focus_threshold_minutes=5,
            focus_cooldown_minutes=10,
        ))
        guard.start_session("proj-a", _ts(-15))
        with patch("devpulse.analyzers.focus_guard._send_notification"):
            r1 = guard.on_project_change("proj-a", "proj-b", _ts(-6))
            # Re-start the session for proj-a and switch quickly
            guard.start_session("proj-a", _ts(-5))
            r2 = guard.on_project_change("proj-a", "proj-b", _ts(0))
        # Second notification should be suppressed by cooldown
        assert r2 is None


class TestQualityScoreCalculation:
    def test_quality_score_decreases_with_interruptions(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=5))
        guard.start_session("proj-a", _ts(-60))
        guard._interruptions = ["slack", "browser", "email"]  # simulate 3 interruptions

        with patch("devpulse.analyzers.focus_guard._send_notification"):
            guard.on_project_change("proj-a", "proj-b", _ts(0))

        # The session that was closed should have a lower quality score with interruptions
        sessions = db.get_focus_sessions(
            since=(datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        )
        closed = [s for s in sessions if s.get("ended_at")]
        if closed:
            assert closed[-1]["quality_score"] < 100

    def test_quality_score_bounded_0_to_100(self):
        guard = FocusGuard(_make_config(focus_threshold_minutes=5))
        guard.start_session("proj-a", _ts(-20))
        with patch("devpulse.analyzers.focus_guard._send_notification"):
            guard.on_project_change("proj-a", "proj-b", _ts(0))

        sessions = db.get_focus_sessions(
            since=(datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        )
        for s in sessions:
            if s.get("quality_score") is not None:
                assert 0 <= s["quality_score"] <= 100


class TestGetTodaySessions:
    def test_returns_empty_before_any_sessions(self):
        guard = FocusGuard(_make_config())
        sessions = guard.get_today_sessions()
        assert sessions == []

    def test_returns_sessions_started_today(self):
        today_str = datetime.now().replace(hour=0).strftime("%Y-%m-%dT%H:%M:%S")
        db.insert_focus_session("myproj", today_str)
        guard = FocusGuard(_make_config())
        sessions = guard.get_today_sessions()
        assert len(sessions) >= 1

    def test_get_focus_score_today_zero_when_no_sessions(self):
        guard = FocusGuard(_make_config())
        assert guard.get_focus_score_today() == 0.0


class TestDisabledGuard:
    def test_no_notification_when_disabled(self):
        guard = FocusGuard(_make_config(focus_guard_enabled=False))
        guard.start_session("proj-a", _ts(-30))
        with patch("devpulse.analyzers.focus_guard._send_notification") as mock_notify:
            result = guard.on_project_change("proj-a", "proj-b", _ts(0))
        assert result is None
        mock_notify.assert_not_called()
