"""Context-switch scoring and deep work block detection."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from devpulse import db

_HOME = str(Path.home())

# Apps recognised as browsers — window titles parsed differently
_BROWSER_APPS = frozenset({
    "Google Chrome", "Safari", "Firefox", "Arc", "Brave Browser",
    "Microsoft Edge", "Opera", "Chromium", "Vivaldi",
})


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


def _label_from_shell_event(ev: dict) -> str | None:
    """Return a meaningful project label for a shell event.

    Priority: stored project → cwd-derived label → None (skip).
    """
    proj = ev.get("project")
    if proj:
        return proj
    # Fall back to cwd for home-dir or untracked-dir commands
    cwd = (ev.get("data") or {}).get("cwd", "")
    if not cwd:
        return None
    if cwd.rstrip("/") == _HOME:
        return "~/home"
    basename = os.path.basename(cwd.rstrip("/"))
    return basename if basename else None


def _label_from_window_event(ev: dict) -> str | None:
    """Return a meaningful app/project label for a window_focus event.

    Uses the stored app name; for browsers, extracts the page context from
    the tab title.
    """
    data = ev.get("data") or {}
    app = (data.get("app") or "").strip()
    title = (data.get("title") or "").strip()

    if not app and not title:
        return None

    if app in _BROWSER_APPS and title:
        # Strip the " — AppName" suffix to get the tab/page title
        if " — " in title:
            page = title.split(" — ")[0].strip()
        elif " - " in title:
            parts = title.split(" - ")
            page = " - ".join(parts[:-1]).strip() if len(parts) > 1 else title
        else:
            page = title
        # Truncate and annotate with short app hint
        short_app = app.split()[0]  # "Google" from "Google Chrome"
        page_short = page[:22].strip()
        return f"{page_short} ({short_app})" if page_short else app
    elif app:
        return app
    elif " — " in title:
        return title.split(" — ")[-1].strip()[:20]
    elif title:
        return title[:20]
    return None


def compute_context_switches(
    since: str,
    until: str | None = None,
    min_deep_work_minutes: int = 30,
) -> dict[str, Any]:
    """
    Returns:
      switches: total context switches
      fragmentation_score: 0-100
      deep_work_blocks: list of {project, start, end, duration_minutes}
      top_transitions: list of {from_project, to_project, count}
    """
    until = until or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    events = db.query_events(event_type="shell_cmd", since=since, until=until)
    win_events = db.query_events(event_type="window_focus", since=since, until=until)

    # Merge and sort all focus signals
    signals: list[tuple[datetime, str]] = []
    for ev in events:
        proj = _label_from_shell_event(ev)
        if proj:
            signals.append((_parse_ts(ev["timestamp"]), proj))
    for ev in win_events:
        proj = _label_from_window_event(ev)
        if proj:
            signals.append((_parse_ts(ev["timestamp"]), proj))

    signals.sort(key=lambda x: x[0])
    if not signals:
        return {
            "switches": 0,
            "fragmentation_score": 0.0,
            "deep_work_blocks": [],
            "top_transitions": [],
            "unique_projects": 0,
        }

    # Count switches
    switches = 0
    transitions: defaultdict[tuple[str, str], int] = defaultdict(int)
    prev_proj = signals[0][1]
    for ts, proj in signals[1:]:
        if proj != prev_proj:
            switches += 1
            transitions[(prev_proj, proj)] += 1
            prev_proj = proj

    # Calculate time span
    total_hours = max(
        (signals[-1][0] - signals[0][0]).total_seconds() / 3600, 1
    )
    total_days = max(total_hours / 24, 1)
    switches_per_hour = switches / total_hours
    switches_per_day = switches / total_days
    unique_projects = len({p for _, p in signals})

    # Use per-hour rate only for active periods (<= 2 hours span); else per-day
    rate_for_fragmentation = switches_per_hour if total_hours <= 2 else switches_per_day / 8

    fragmentation = min(100.0, (rate_for_fragmentation * 10) + (unique_projects * 5))

    # Detect deep work blocks
    blocks = _find_deep_work_blocks(signals, min_deep_work_minutes)

    top_transitions = sorted(
        [
            {"from_project": k[0], "to_project": k[1], "count": v}
            for k, v in transitions.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )[:5]

    return {
        "switches": switches,
        "fragmentation_score": round(fragmentation, 1),
        "deep_work_blocks": blocks,
        "top_transitions": top_transitions,
        "unique_projects": unique_projects,
        "switches_per_hour": round(switches_per_hour, 2),
        "switches_per_day": round(switches_per_day, 1),
    }


_IDLE_GAP_MINUTES = 60


def _find_deep_work_blocks(
    signals: list[tuple[datetime, str]],
    min_minutes: int,
) -> list[dict[str, Any]]:
    """Find continuous stretches on the same project >= min_minutes.

    A block is broken when the project changes OR the gap between
    consecutive events exceeds _IDLE_GAP_MINUTES (even within the same
    project).  This prevents multi-day spans from being counted as a
    single focus block.
    """
    if not signals:
        return []

    def _emit(start: datetime, end: datetime, proj: str) -> dict[str, Any] | None:
        duration = (end - start).total_seconds() / 60
        if duration >= min_minutes:
            return {
                "project": proj,
                "start": start.strftime("%H:%M"),
                "end": end.strftime("%H:%M"),
                "duration_minutes": round(duration),
            }
        return None

    blocks: list[dict[str, Any]] = []
    block_start = signals[0][0]
    block_proj = signals[0][1]
    last_ts = signals[0][0]

    for ts, proj in signals[1:]:
        gap = (ts - last_ts).total_seconds() / 60
        if proj != block_proj or gap > _IDLE_GAP_MINUTES:
            b = _emit(block_start, last_ts, block_proj)
            if b:
                blocks.append(b)
            block_start = ts
            block_proj = proj
        last_ts = ts

    b = _emit(block_start, last_ts, block_proj)
    if b:
        blocks.append(b)

    return blocks


def today_context() -> dict[str, Any]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return compute_context_switches(
        since=today.strftime("%Y-%m-%dT%H:%M:%S"),
    )
