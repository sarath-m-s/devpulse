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

    # Close any focus sessions left open from a previous daemon run
    orphans = db.close_orphaned_focus_sessions()
    if orphans:
        logger.info("Closed %d orphaned focus sessions from previous run", orphans)

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

    # v2: Background intelligence tasks
    _schedule_v2_tasks(config, stop_event)

    stop_event.wait()  # block until signal

    logger.info("Stopping collectors…")
    for c in collectors:
        try:
            c.stop()
        except Exception as exc:
            logger.warning("Error stopping collector: %s", exc)

    _cleanup()
    logger.info("DevPulse daemon stopped")


def _schedule_v2_tasks(config: dict[str, Any], stop_event: threading.Event) -> None:
    """Start background threads for v2 intelligence features."""
    v2 = config.get("v2", {})

    # Workflow sequence learner — runs every hour
    def _learn_sequences() -> None:
        while not stop_event.is_set():
            stop_event.wait(3600)
            if stop_event.is_set():
                break
            try:
                from devpulse.analyzers.workflow_predictor import WorkflowPredictor
                n = WorkflowPredictor().learn_sequences(
                    days=v2.get("prediction_learning_days", 30)
                )
                if n:
                    logger.info("Workflow learner: upserted %d sequences", n)
            except Exception as exc:
                logger.warning("Workflow learner error: %s", exc)

    threading.Thread(target=_learn_sequences, daemon=True, name="workflow-learner").start()

    # Context restorer — snapshot sessions every 30 min
    def _snapshot_sessions() -> None:
        gap = v2.get("session_gap_minutes", 30)
        if not v2.get("auto_snapshot", True):
            return
        while not stop_event.is_set():
            stop_event.wait(gap * 60)
            if stop_event.is_set():
                break
            try:
                from devpulse.analyzers.context_restorer import ContextRestorer
                snapshotted = ContextRestorer().capture_on_gap(gap_minutes=gap)
                if snapshotted:
                    logger.info("Captured snapshots for: %s", ", ".join(snapshotted))
            except Exception as exc:
                logger.warning("Context restorer error: %s", exc)

    threading.Thread(target=_snapshot_sessions, daemon=True, name="context-restorer").start()

    # Error fix detector — runs every 5 min
    def _detect_fixes() -> None:
        while not stop_event.is_set():
            stop_event.wait(300)
            if stop_event.is_set():
                break
            try:
                from devpulse.analyzers.error_memory import ErrorMemory
                n = ErrorMemory().detect_fixes_from_history()
                if n:
                    logger.info("Error memory: recorded %d new fixes", n)
            except Exception as exc:
                logger.warning("Error fix detector error: %s", exc)

    threading.Thread(target=_detect_fixes, daemon=True, name="error-fix-detector").start()

    # Focus guard — project-switch monitor (runs every 30 seconds)
    if v2.get("focus_guard_enabled", True):
        from devpulse.analyzers.focus_guard import FocusGuard, _get_active_app_name
        guard = FocusGuard(config)
        _last_project: list[str] = [""]  # mutable container for closure

        def _monitor_focus() -> None:
            while not stop_event.is_set():
                stop_event.wait(30)
                if stop_event.is_set():
                    break
                try:
                    now_dt = __import__("datetime").datetime.now()
                    now_str = now_dt.strftime("%Y-%m-%dT%H:%M:%S")
                    since_str = (
                        now_dt - __import__("datetime").timedelta(seconds=90)
                    ).strftime("%Y-%m-%dT%H:%M:%S")

                    recent = db.query_events(
                        event_type="shell_cmd",
                        since=since_str,
                    )
                    if not recent:
                        continue
                    latest_ev = recent[-1]
                    project = latest_ev.get("project") or "unknown"

                    # If the shell went idle / unknown, try to find the active app
                    if project == "unknown":
                        app = _get_active_app_name()
                        if app:
                            project = app
                        else:
                            continue  # still nothing useful

                    if project != _last_project[0] and _last_project[0]:
                        ts = latest_ev.get("timestamp", now_str)
                        guard.on_project_change(_last_project[0], project, ts)
                    elif not _last_project[0]:
                        guard.start_session(project, latest_ev.get("timestamp", now_str))
                    _last_project[0] = project
                except Exception as exc:
                    logger.debug("Focus guard error: %s", exc)

        threading.Thread(target=_monitor_focus, daemon=True, name="focus-guard").start()
        logger.info("Focus guard started")

    logger.info("v2 background tasks scheduled")


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
