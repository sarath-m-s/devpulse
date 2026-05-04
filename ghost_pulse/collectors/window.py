"""Active window tracker — optional, platform-specific."""

from __future__ import annotations

import subprocess
import sys
import threading
import time

from ghost_pulse import db


def _get_active_window_macos() -> tuple[str, str] | None:
    """Return (app_name, window_title) on macOS using osascript."""
    try:
        app = subprocess.check_output(
            [
                "osascript",
                "-e",
                'tell application "System Events" to get name of first process whose frontmost is true',
            ],
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).decode().strip()

        title = subprocess.check_output(
            [
                "osascript",
                "-e",
                f'tell application "{app}" to get name of front window',
            ],
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).decode().strip()

        return app, title
    except Exception:
        return None


def _get_active_window_linux() -> tuple[str, str] | None:
    """Return (app_name, window_title) on Linux using xdotool."""
    try:
        win_id = subprocess.check_output(
            ["xdotool", "getactivewindow"],
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).decode().strip()

        title = subprocess.check_output(
            ["xdotool", "getwindowname", win_id],
            timeout=3,
            stderr=subprocess.DEVNULL,
        ).decode().strip()

        # Best-effort: extract app name from title
        app = title.split(" — ")[-1] if " — " in title else title.split(" - ")[-1]
        return app.strip(), title
    except Exception:
        return None


def _get_active_window() -> tuple[str, str] | None:
    if sys.platform == "darwin":
        return _get_active_window_macos()
    elif sys.platform.startswith("linux"):
        return _get_active_window_linux()
    return None


class WindowTracker:
    """Polls active window every poll_interval seconds."""

    def __init__(self, poll_interval: int = 5) -> None:
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: tuple[str, str] | None = None
        self._available: bool | None = None

    def _check_availability(self) -> bool:
        result = _get_active_window()
        return result is not None

    def _run(self) -> None:
        if self._available is None:
            self._available = self._check_availability()

        if not self._available:
            import logging
            logging.getLogger("ghost_pulse").warning(
                "Window tracker unavailable on this platform — disabling"
            )
            return

        while not self._stop_event.is_set():
            try:
                current = _get_active_window()
                if current and current != self._last:
                    app, title = current
                    db.insert_event(
                        event_type="window_focus",
                        data={"app": app, "title": title},
                    )
                    self._last = current
            except Exception:
                pass
            self._stop_event.wait(self._poll_interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="window-tracker")
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
