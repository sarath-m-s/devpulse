"""Focus guard — real-time focus session tracking and interruption notifications."""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from typing import Any

from devpulse import db


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def _fmt_duration(minutes: float) -> str:
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _get_active_app_name() -> str | None:
    """Return the name of the currently focused application (macOS/Linux).

    Returns None if unavailable or if the app is a terminal (user is coding).
    """
    import sys
    try:
        if sys.platform == "darwin":
            result = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            app = result.stdout.strip()
            if not app:
                return None
            # Skip terminals — those are covered by shell_cmd events
            _TERMINALS = {"Terminal", "iTerm2", "Alacritty", "kitty", "Hyper", "WezTerm", "Warp"}
            if app in _TERMINALS:
                return None
            return app
        elif sys.platform.startswith("linux"):
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True, timeout=2,
            )
            title = result.stdout.strip()
            if not title:
                return None
            # Use last segment after " — " as app name (common convention)
            app = title.split(" — ")[-1].strip()
            return app or None
    except Exception:
        pass
    return None


def _send_notification(title: str, message: str, method: str) -> None:
    """Send a desktop or terminal notification."""
    if method in ("desktop", "both"):
        try:
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{message}" with title "{title}"'],
                capture_output=True,
                timeout=3,
            )
            return
        except Exception:
            pass
        try:
            subprocess.run(
                ["notify-send", title, message],
                capture_output=True,
                timeout=3,
            )
        except Exception:
            pass
    if method in ("terminal", "both"):
        # Bell + printed message
        print(f"\a[DevPulse] {title}: {message}")


class FocusGuard:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        v2 = config.get("v2", {})
        self.focus_threshold_min: float = v2.get("focus_threshold_minutes", 15)
        self.cooldown_min: float = v2.get("focus_cooldown_minutes", 5)
        self.notification_method: str = v2.get("focus_notification_method", "terminal")
        self.enabled: bool = v2.get("focus_guard_enabled", True)

        # In-memory session state (lives in daemon process)
        self._session_id: int | None = None
        self._session_project: str | None = None
        self._session_start: datetime | None = None
        self._last_activity: datetime | None = None
        self._interruptions: list[str] = []
        self._last_notification: datetime | None = None

    def on_project_change(
        self,
        from_project: str,
        to_project: str,
        timestamp: str,
    ) -> dict[str, Any] | None:
        """Called when the user switches projects.

        Returns a notification dict if focus was interrupted, None otherwise.
        """
        if not self.enabled:
            return None

        ts = _parse_ts(timestamp)
        notification: dict[str, Any] | None = None

        if self._session_project == from_project and self._session_start is not None:
            focus_duration = (ts - self._session_start).total_seconds() / 60

            if focus_duration >= self.focus_threshold_min:
                # Check cooldown
                in_cooldown = (
                    self._last_notification is not None
                    and (ts - self._last_notification).total_seconds() / 60 < self.cooldown_min
                )
                if not in_cooldown:
                    cost_estimate = min(30, int(focus_duration * 0.55))  # ~55% cost heuristic
                    msg = (
                        f"You were focused on {from_project} for "
                        f"{_fmt_duration(focus_duration)}. "
                        f"Context switches typically cost ~{cost_estimate} min to recover."
                    )
                    notification = {
                        "type": "focus_warning",
                        "message": msg,
                        "focus_duration_min": round(focus_duration, 1),
                        "cost_estimate_min": cost_estimate,
                        "from_project": from_project,
                        "to_project": to_project,
                    }
                    _send_notification("Focus interrupted", msg, self.notification_method)
                    self._last_notification = ts

                # Record interruption
                self._interruptions.append(to_project)

                # Close session if switching to a different project
                self._close_session(ended_at=ts)

        # Start new session for the new project
        self.start_session(to_project, timestamp)
        return notification

    def start_session(self, project: str, timestamp: str | None = None) -> None:
        """Start tracking a new focus session."""
        ts = _parse_ts(timestamp) if timestamp else datetime.now()
        ts_str = ts.strftime("%Y-%m-%dT%H:%M:%S")

        self._session_project = project
        self._session_start = ts
        self._last_activity = ts
        self._interruptions = []
        self._session_id = db.insert_focus_session(project=project, started_at=ts_str)

    def _close_session(self, ended_at: datetime) -> dict[str, Any] | None:
        """Persist the current session to DB and reset state."""
        if self._session_id is None or self._session_start is None:
            return None

        duration = (ended_at - self._session_start).total_seconds() / 60
        interruptions = len(self._interruptions)
        target_duration = 90.0  # target: 90-min focus blocks
        quality = min(100.0, (duration / target_duration) * 100 - interruptions * 15)
        quality = max(0.0, quality)

        db.close_focus_session(
            session_id=self._session_id,
            ended_at=ended_at.strftime("%Y-%m-%dT%H:%M:%S"),
            duration_minutes=round(duration, 1),
            interruption_count=interruptions,
            interruption_sources=list(self._interruptions),
            quality_score=round(quality, 1),
        )

        summary = {
            "session_id": self._session_id,
            "project": self._session_project,
            "duration_min": round(duration, 1),
            "quality_score": round(quality, 1),
        }
        self._session_id = None
        self._session_start = None
        return summary

    def end_session(self) -> dict[str, Any]:
        """End current focus session and return summary."""
        result = self._close_session(ended_at=datetime.now())
        return result or {}

    def on_activity(self, project: str, timestamp: str) -> None:
        """Call on every event to keep session alive or detect restarts."""
        if not self.enabled:
            return
        ts = _parse_ts(timestamp)

        if self._session_project != project:
            # New project — fire on_project_change
            from_proj = self._session_project or ""
            self.on_project_change(from_proj, project, timestamp)
        else:
            self._last_activity = ts

    def get_today_sessions(self) -> list[dict[str, Any]]:
        """Get all focus sessions from today."""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return db.get_focus_sessions(since=today.strftime("%Y-%m-%dT%H:%M:%S"))

    def get_focus_score_today(self) -> float:
        """Get aggregate focus score for today (0-100)."""
        sessions = self.get_today_sessions()
        if not sessions:
            return 0.0
        scores = [s["quality_score"] for s in sessions if s.get("quality_score") is not None]
        return round(sum(scores) / len(scores), 1) if scores else 0.0
