"""File watcher — monitors config/infra files for changes using watchdog."""

from __future__ import annotations

import threading
from pathlib import Path

from ghost_pulse import db

try:
    from watchdog.events import FileSystemEvent, PatternMatchingEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False


WATCHED_PATTERNS = [
    "*.env",
    ".env.*",
    "docker-compose*.yml",
    "docker-compose*.yaml",
    "Makefile",
    "*.tf",
    "*.tfvars",
    "pyproject.toml",
    "package.json",
    "tsconfig.json",
    "*.lock",
    "Dockerfile",
    "Dockerfile.*",
]


class _GhostPulseHandler:  # type: ignore[misc]
    """watchdog handler that logs file change events."""

    def __init__(self, project_name: str) -> None:
        self._project = project_name

    def dispatch(self, event: "FileSystemEvent") -> None:
        if event.is_directory:
            return
        change_type = event.event_type  # modified / created / deleted / moved
        db.insert_event(
            event_type="file_change",
            data={
                "file": event.src_path,
                "change_type": change_type,
            },
            project=self._project,
        )


class FileWatcher:
    """Watches registered project directories for config/infra file changes."""

    def __init__(self, project_paths: list[str]) -> None:
        self._paths = project_paths
        self._observer: "Observer | None" = None
        self._warned = False

    def start(self) -> None:
        if not _WATCHDOG_AVAILABLE:
            if not self._warned:
                import logging
                logging.getLogger("ghost_pulse").warning(
                    "watchdog not installed — file watcher disabled"
                )
                self._warned = True
            return

        self._observer = Observer()
        for raw_path in self._paths:
            p = Path(raw_path).expanduser().resolve()
            if not p.is_dir():
                continue

            handler = _GhostPulseHandler(project_name=p.name)

            # Wrap with PatternMatchingEventHandler if watchdog supports it
            pattern_handler = PatternMatchingEventHandler(
                patterns=WATCHED_PATTERNS,
                ignore_directories=True,
                case_sensitive=False,
            )
            # Monkey-patch the on_* methods to forward to our handler
            for evt in ("on_created", "on_modified", "on_deleted", "on_moved"):
                setattr(pattern_handler, evt, lambda e, h=handler: h.dispatch(e))

            self._observer.schedule(pattern_handler, str(p), recursive=True)

        self._observer.start()

    def stop(self) -> None:
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join(timeout=5)
