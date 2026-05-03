"""Background collector daemon — manages all collector threads."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

from devpulse import db
from devpulse.config import load_config, DEVPULSE_DIR

logger = logging.getLogger("devpulse.daemon")

PID_FILE = DEVPULSE_DIR / "daemon.pid"
LOG_FILE = DEVPULSE_DIR / "daemon.log"


def _setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _write_pid() -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None


def is_running() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _cleanup() -> None:
    try:
        PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _run_daemon(config: dict[str, Any]) -> None:
    """Main daemon loop — starts all enabled collectors."""
    _setup_logging()
    _write_pid()
    logger.info("DevPulse daemon started (pid=%d)", os.getpid())

    db.init_db()

    collectors = []
    cfg_collectors = config.get("collectors", {})
    projects = config.get("projects", {}).get("paths", [])
    poll = config.get("general", {}).get("poll_interval_seconds", 30)

    if cfg_collectors.get("git", True):
        from devpulse.collectors.git_collector import GitCollector
        gc = GitCollector(project_paths=projects, poll_interval=poll)
        gc.start()
        collectors.append(gc)
        logger.info("Git collector started")

    if cfg_collectors.get("file_watcher", True):
        from devpulse.collectors.file_watcher import FileWatcher
        fw = FileWatcher(project_paths=projects)
        fw.start()
        collectors.append(fw)
        logger.info("File watcher started")

    if cfg_collectors.get("window_tracker", False):
        from devpulse.collectors.window import WindowTracker
        wt = WindowTracker()
        wt.start()
        collectors.append(wt)
        logger.info("Window tracker started")

    stop_event = threading.Event()

    def _handle_signal(signum: int, frame: Any) -> None:
        logger.info("Received signal %d — stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Periodic DB cleanup
    def _maintenance() -> None:
        retention = config.get("general", {}).get("data_retention_days", 90)
        while not stop_event.is_set():
            stop_event.wait(3600)  # every hour
            if not stop_event.is_set():
                deleted = db.cleanup_old_events(days=retention)
                if deleted:
                    logger.info("Cleaned up %d old events", deleted)

    threading.Thread(target=_maintenance, daemon=True, name="maintenance").start()

    stop_event.wait()  # block until signal

    logger.info("Stopping collectors…")
    for c in collectors:
        try:
            c.stop()
        except Exception as exc:
            logger.warning("Error stopping collector: %s", exc)

    _cleanup()
    logger.info("DevPulse daemon stopped")


def start_daemon() -> None:
    """Fork to background and run the daemon."""
    if is_running():
        print("Daemon is already running.")
        return

    config = load_config()

    # Double-fork to daemonise
    pid = os.fork()
    if pid > 0:
        # Parent: wait briefly and confirm
        time.sleep(0.5)
        if is_running():
            print(f"Daemon started (pid {_read_pid()})")
        else:
            print("Daemon may have failed to start — check ~/.devpulse/daemon.log")
        return

    # Child: detach
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # Grandchild: actual daemon
    sys.stdin.close()
    try:
        _run_daemon(config)
    except Exception as exc:
        logging.getLogger("devpulse.daemon").exception("Unhandled error: %s", exc)
    finally:
        _cleanup()
    os._exit(0)


def stop_daemon() -> None:
    pid = _read_pid()
    if pid is None or not is_running():
        print("Daemon is not running.")
        _cleanup()
        return
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(30):
            time.sleep(0.2)
            if not is_running():
                break
        if is_running():
            os.kill(pid, signal.SIGKILL)
        _cleanup()
        print("Daemon stopped.")
    except Exception as exc:
        print(f"Error stopping daemon: {exc}")
