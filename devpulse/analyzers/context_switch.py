"""Context-switch scoring and deep work block detection."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from devpulse import db


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")


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
        proj = ev.get("project") or "unknown"
        signals.append((_parse_ts(ev["timestamp"]), proj))
    for ev in win_events:
        title = ev.get("data", {}).get("title", "")
        proj = title.split(" — ")[-1].strip() if " — " in title else "unknown"
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


def _find_deep_work_blocks(
    signals: list[tuple[datetime, str]],
    min_minutes: int,
) -> list[dict[str, Any]]:
    """Find continuous stretches on the same project >= min_minutes."""
    if not signals:
        return []

    blocks: list[dict[str, Any]] = []
    block_start = signals[0][0]
    block_proj = signals[0][1]
    last_ts = signals[0][0]

    for ts, proj in signals[1:]:
        if proj == block_proj:
            last_ts = ts
        else:
            duration = (last_ts - block_start).total_seconds() / 60
            if duration >= min_minutes:
                blocks.append(
                    {
                        "project": block_proj,
                        "start": block_start.strftime("%H:%M"),
                        "end": last_ts.strftime("%H:%M"),
                        "duration_minutes": round(duration),
                    }
                )
            block_start = ts
            block_proj = proj
            last_ts = ts

    # Final block
    duration = (last_ts - block_start).total_seconds() / 60
    if duration >= min_minutes:
        blocks.append(
            {
                "project": block_proj,
                "start": block_start.strftime("%H:%M"),
                "end": last_ts.strftime("%H:%M"),
                "duration_minutes": round(duration),
            }
        )

    return blocks


def today_context() -> dict[str, Any]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return compute_context_switches(
        since=today.strftime("%Y-%m-%dT%H:%M:%S"),
    )
