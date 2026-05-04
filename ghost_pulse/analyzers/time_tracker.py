"""Per-project time estimation from shell commands, git commits, and window focus."""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from devpulse import db

_HOME = str(Path.home())


def _project_label(ev: dict) -> str:
    """Return a human-readable project label for a shell/git event.

    Falls back to the cwd-derived directory name (or '~/home') when the
    event was recorded outside a tracked git repository.
    """
    proj = ev.get("project")
    if proj:
        return proj
    cwd = (ev.get("data") or {}).get("cwd", "")
    if not cwd:
        return "~/home"
    if cwd.rstrip("/") == _HOME:
        return "~/home"
    basename = os.path.basename(cwd.rstrip("/"))
    return basename if basename else "~/home"

# Minutes per commit as base contribution
_COMMIT_MINUTES = 5

# Command categories keyed by leading tokens
_CATEGORY_PATTERNS: list[tuple[str, list[str]]] = [
    ("git", ["git ", "gh "]),
    ("infrastructure", ["docker", "terraform", "aws ", "kubectl", "helm"]),
    ("testing", ["pytest", "jest", "npm test", "cargo test", "go test", "yarn test"]),
    ("build", ["make", "npm run build", "cargo build", "go build", "yarn build"]),
    ("coding", ["vim", "nvim", "code ", "nano ", "cat "]),
]


_GIT_COMMIT_RE = re.compile(r"^git\s+commit\b")


def _is_git_commit_command(cmd: str) -> bool:
    """Return True if the shell command is a git commit invocation."""
    return bool(_GIT_COMMIT_RE.match(cmd.strip()))


def _categorize_command(cmd: str) -> str:
    cmd_lower = cmd.strip().lower()
    for category, prefixes in _CATEGORY_PATTERNS:
        for prefix in prefixes:
            if cmd_lower.startswith(prefix):
                return category
    return "other"


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def compute_time_per_project(
    since: str,
    until: str | None = None,
    idle_timeout_minutes: int = 15,
) -> dict[str, dict[str, Any]]:
    """
    Returns {project: {total_minutes, by_category, commits}} for the given window.
    """
    until = until or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # --- Shell commands ---
    cmd_events = db.query_events(event_type="shell_cmd", since=since, until=until)
    # Group by project
    by_project: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for ev in cmd_events:
        proj = _project_label(ev)
        ts = _parse_ts(ev["timestamp"])
        cmd = ev.get("data", {}).get("cmd", "")
        by_project[proj].append((ts, cmd))

    result: dict[str, dict[str, Any]] = {}

    for proj, entries in by_project.items():
        entries.sort(key=lambda x: x[0])
        total_minutes = 0.0
        category_minutes: dict[str, float] = defaultdict(float)

        for i in range(len(entries) - 1):
            ts_curr, cmd = entries[i]
            ts_next, _ = entries[i + 1]
            gap = (ts_next - ts_curr).total_seconds() / 60
            active = min(gap, idle_timeout_minutes)
            category = _categorize_command(cmd)
            total_minutes += active
            category_minutes[category] += active

        result[proj] = {
            "total_minutes": round(total_minutes, 1),
            "by_category": {k: round(v, 1) for k, v in category_minutes.items()},
            "commits": 0,
        }

    # --- Git commits add base minutes ---
    git_events = db.query_events(event_type="git_commit", since=since, until=until)
    for ev in git_events:
        proj = _project_label(ev)
        if proj not in result:
            result[proj] = {"total_minutes": 0.0, "by_category": {}, "commits": 0}
        result[proj]["total_minutes"] += _COMMIT_MINUTES
        result[proj]["commits"] += 1
        result[proj]["by_category"]["git"] = (
            result[proj]["by_category"].get("git", 0.0) + _COMMIT_MINUTES
        )

    # --- Fallback: count "git commit" shell commands when collector has no data ---
    if not git_events:
        for ev in cmd_events:
            cmd = ev.get("data", {}).get("cmd", "")
            if _is_git_commit_command(cmd):
                proj = _project_label(ev)
                if proj not in result:
                    result[proj] = {"total_minutes": 0.0, "by_category": {}, "commits": 0}
                result[proj]["commits"] += 1

    # --- Window focus (if available) ---
    win_events = db.query_events(event_type="window_focus", since=since, until=until)
    if win_events:
        _apply_window_time(win_events, result, idle_timeout_minutes)

    return result


def _apply_window_time(
    win_events: list[dict[str, Any]],
    result: dict[str, dict[str, Any]],
    idle_timeout_minutes: int,
) -> None:
    """Supplement result with window-focus derived time estimates."""
    win_events.sort(key=lambda e: e["timestamp"])
    for i in range(len(win_events) - 1):
        ev = win_events[i]
        ts_curr = _parse_ts(ev["timestamp"])
        ts_next = _parse_ts(win_events[i + 1]["timestamp"])
        title: str = ev.get("data", {}).get("title", "")
        gap = min((ts_next - ts_curr).total_seconds() / 60, idle_timeout_minutes)
        # Try to map window title to a known project
        for proj in result:
            if proj.lower() in title.lower():
                result[proj]["total_minutes"] = (
                    result[proj].get("total_minutes", 0) + gap * 0.3
                )
                break


def today_stats() -> dict[str, Any]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return compute_time_per_project(
        since=today.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def week_stats() -> dict[str, Any]:
    week_ago = datetime.now() - timedelta(days=7)
    return compute_time_per_project(
        since=week_ago.strftime("%Y-%m-%dT%H:%M:%S"),
    )
