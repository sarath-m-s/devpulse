"""Shared data fetching layer for the DevPulse TUI.

Re-exposes the same data the web UI uses (analyzers, LLM, db, daemon) in a
unified API so screens can stay focused on rendering.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape as _esc
from typing import Any

from devpulse import db
from devpulse.analyzers import context_switch, time_tracker, toil as toil_analyzer


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def today_start() -> str:
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def week_start() -> str:
    return (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")


def month_start() -> str:
    return (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S")


def fmt_dur(minutes: float | int | None) -> str:
    if not minutes:
        return "0m"
    h = int(minutes) // 60
    m = int(minutes) % 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


# ---------------------------------------------------------------------------
# Today snapshot
# ---------------------------------------------------------------------------

def fetch_today() -> dict[str, Any]:
    since = today_start()
    time_data = time_tracker.compute_time_per_project(since=since)
    ctx = context_switch.compute_context_switches(since=since)

    total_minutes = sum(p["total_minutes"] for p in time_data.values())
    total_commits = sum(p["commits"] for p in time_data.values())
    total_cmds = db.count_events_today()
    focus_score = max(0, 100 - int(ctx["fragmentation_score"]))

    blocks = ctx.get("deep_work_blocks", [])
    longest_block = None
    if blocks:
        best = max(blocks, key=lambda b: b.get("duration_minutes", 0))
        longest_block = {
            "minutes": round(best.get("duration_minutes", 0)),
            "start": best.get("start", ""),
            "end": best.get("end", ""),
            "project": best.get("project", ""),
        }

    projects = []
    for proj, stats in sorted(
        time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
    )[:8]:
        projects.append({
            "name": proj,
            "minutes": round(stats["total_minutes"], 1),
            "commits": stats["commits"],
            "pct": round(stats["total_minutes"] / max(total_minutes, 1) * 100, 1),
        })

    n_proj = len([p for p in projects if p["minutes"] > 0])

    return {
        "total_minutes": round(total_minutes),
        "total_commits": total_commits,
        "total_cmds": total_cmds,
        "switches": ctx["switches"],
        "focus_score": focus_score,
        "fragmentation_score": ctx["fragmentation_score"],
        "projects": projects,
        "project_count": n_proj,
        "deep_work_blocks": blocks,
        "longest_block": longest_block,
    }


# ---------------------------------------------------------------------------
# Week snapshot (with per-day breakdown)
# ---------------------------------------------------------------------------

def fetch_week() -> dict[str, Any]:
    since = week_start()
    time_data = time_tracker.compute_time_per_project(since=since)
    ctx = context_switch.compute_context_switches(since=since)

    total_minutes = sum(p["total_minutes"] for p in time_data.values())
    total_commits = sum(p["commits"] for p in time_data.values())

    projects = []
    for proj, stats in sorted(
        time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
    )[:10]:
        projects.append({
            "name": proj,
            "minutes": round(stats["total_minutes"]),
            "hours": round(stats["total_minutes"] / 60, 1),
            "commits": stats["commits"],
            "pct": round(stats["total_minutes"] / max(total_minutes, 1) * 100, 1),
        })

    blocks = ctx.get("deep_work_blocks", [])
    avg_block = (
        round(sum(b.get("duration_minutes", 0) for b in blocks) / len(blocks))
        if blocks else 0
    )

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    now = datetime.now()
    daily = []
    for i in range(6, -1, -1):
        d = now - timedelta(days=i)
        ds = d.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        de = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        day_time = time_tracker.compute_time_per_project(since=ds, until=de)
        day_ctx = context_switch.compute_context_switches(since=ds, until=de)
        day_mins = sum(p["total_minutes"] for p in day_time.values())
        day_commits = sum(p["commits"] for p in day_time.values())
        day_blocks = day_ctx.get("deep_work_blocks", [])
        longest = max((b.get("duration_minutes", 0) for b in day_blocks), default=0)
        daily.append({
            "day": day_names[d.weekday()],
            "date": d.strftime("%Y-%m-%d"),
            "hours": round(day_mins / 60, 1),
            "minutes": round(day_mins),
            "commits": day_commits,
            "switches": day_ctx.get("switches", 0),
            "longest_block": round(longest),
            "is_today": i == 0,
        })

    best_day = max(daily, key=lambda d: d["minutes"]) if daily else None

    return {
        "total_minutes": round(total_minutes),
        "total_hours": round(total_minutes / 60, 1),
        "total_commits": total_commits,
        "switches": ctx["switches"],
        "switches_per_day": ctx.get("switches_per_day", 0),
        "fragmentation_score": ctx["fragmentation_score"],
        "focus_score": max(0, 100 - int(ctx["fragmentation_score"])),
        "projects": projects,
        "avg_focus_block": avg_block,
        "daily": daily,
        "best_day": best_day,
        "top_transitions": ctx.get("top_transitions", [])[:5],
    }


# ---------------------------------------------------------------------------
# Projects (today/week/month + branches)
# ---------------------------------------------------------------------------

def fetch_projects() -> list[dict[str, Any]]:
    week_data = time_tracker.compute_time_per_project(since=week_start())
    month_data = time_tracker.compute_time_per_project(since=month_start())
    today_data = time_tracker.compute_time_per_project(since=today_start())

    all_projects = set(week_data.keys()) | set(month_data.keys())
    total_week = max(sum(p["total_minutes"] for p in week_data.values()), 1)

    result = []
    for proj in sorted(
        all_projects,
        key=lambda p: week_data.get(p, {}).get("total_minutes", 0),
        reverse=True,
    ):
        ws = week_data.get(proj, {"total_minutes": 0, "commits": 0})
        ms = month_data.get(proj, {"total_minutes": 0, "commits": 0})
        ts = today_data.get(proj, {"total_minutes": 0, "commits": 0})
        result.append({
            "name": proj,
            "today_minutes": round(ts["total_minutes"]),
            "week_minutes": round(ws["total_minutes"]),
            "week_commits": ws["commits"],
            "month_minutes": round(ms["total_minutes"]),
            "month_commits": ms["commits"],
            "pct": round(ws["total_minutes"] / total_week * 100, 1),
        })
    return result


def fetch_branches() -> list[dict[str, Any]]:
    since = week_start()
    commit_events = db.query_events(event_type="git_commit", since=since)
    switch_events = db.query_events(event_type="git_branch_switch", since=since)

    branch_stats: dict[str, dict] = {}
    for e in commit_events:
        data = e.get("data") or {}
        branch = data.get("branch", "main")
        proj = e.get("project") or "unknown"
        key = f"{proj}/{branch}"
        if key not in branch_stats:
            branch_stats[key] = {
                "branch": branch, "project": proj,
                "commits": 0, "last_activity": e.get("timestamp", ""),
            }
        branch_stats[key]["commits"] += 1
        branch_stats[key]["last_activity"] = e.get("timestamp", "")

    for e in switch_events:
        data = e.get("data") or {}
        branch = data.get("to_branch", "")
        proj = e.get("project") or "unknown"
        key = f"{proj}/{branch}"
        if key not in branch_stats:
            branch_stats[key] = {
                "branch": branch, "project": proj,
                "commits": 0, "last_activity": e.get("timestamp", ""),
            }

    result = sorted(
        branch_stats.values(),
        key=lambda b: b["last_activity"],
        reverse=True,
    )[:10]

    for item in result:
        ts = item["last_activity"]
        try:
            dt = datetime.fromisoformat(ts)
            diff = datetime.now() - dt
            if diff.days == 0:
                item["when"] = "today"
            elif diff.days == 1:
                item["when"] = "yesterday"
            else:
                item["when"] = f"{diff.days}d ago"
        except (ValueError, TypeError):
            item["when"] = ""

    return result


# ---------------------------------------------------------------------------
# Toil
# ---------------------------------------------------------------------------

def fetch_toil() -> list[dict[str, Any]]:
    patterns = toil_analyzer.get_ranked_patterns()[:10]
    return [
        {
            "id": p.get("id"),
            "commands": p.get("commands", []),
            "label": " -> ".join(p.get("commands", [])),
            "count": p.get("count", 1),
            "wasted_hours": round(toil_analyzer.estimate_time_wasted(p), 2),
        }
        for p in patterns
    ]


def get_toil_pattern_by_id(pattern_id: int) -> dict[str, Any] | None:
    for p in toil_analyzer.get_ranked_patterns():
        if p.get("id") == pattern_id:
            return p
    return None


# ---------------------------------------------------------------------------
# Focus
# ---------------------------------------------------------------------------

def fetch_focus() -> dict[str, Any]:
    today = context_switch.compute_context_switches(since=today_start())
    week = context_switch.compute_context_switches(since=week_start())

    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    now = datetime.now()
    daily_switches = []
    daily_focus = []
    for i in range(6, -1, -1):
        d = now - timedelta(days=i)
        ds = d.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        de = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        day_ctx = context_switch.compute_context_switches(since=ds, until=de)
        s = day_ctx.get("switches", 0)
        blocks = day_ctx.get("deep_work_blocks", [])
        longest = max((b.get("duration_minutes", 0) for b in blocks), default=0)
        daily_switches.append({
            "day": day_names[d.weekday()],
            "switches": s,
            "is_today": i == 0,
        })
        daily_focus.append({
            "day": day_names[d.weekday()],
            "longest_block": round(longest),
            "is_today": i == 0,
        })

    week_blocks = week.get("deep_work_blocks", [])
    avg_block = (
        round(sum(b.get("duration_minutes", 0) for b in week_blocks) / len(week_blocks))
        if week_blocks else 0
    )

    best_day_sw = min(
        (d for d in daily_switches if d["switches"] > 0),
        key=lambda x: x["switches"],
        default=None,
    )
    worst_day_sw = max(daily_switches, key=lambda x: x["switches"], default=None)

    return {
        "today": {
            "switches": today["switches"],
            "fragmentation_score": today["fragmentation_score"],
            "focus_score": max(0, 100 - int(today["fragmentation_score"])),
            "deep_work_blocks": today.get("deep_work_blocks", []),
            "top_transitions": today.get("top_transitions", [])[:5],
        },
        "week": {
            "switches": week["switches"],
            "switches_per_day": week.get("switches_per_day", 0),
            "fragmentation_score": week["fragmentation_score"],
            "focus_score": max(0, 100 - int(week["fragmentation_score"])),
            "top_transitions": week.get("top_transitions", [])[:5],
            "avg_focus_block": avg_block,
            "best_day": best_day_sw["day"] if best_day_sw else "-",
            "worst_day": worst_day_sw["day"] if worst_day_sw else "-",
        },
        "daily_switches": daily_switches,
        "daily_focus": daily_focus,
    }


def fetch_heatmap(days: int = 7) -> dict[str, Any]:
    """Return hour-by-day intensity grid for activity heatmap."""
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    now = datetime.now()
    hours = list(range(9, 19))  # 9:00 to 18:00

    grid: list[list[int]] = []
    day_labels: list[str] = []

    for i in range(days - 1, -1, -1):
        d = now - timedelta(days=i)
        ds = d.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        de = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        events = db.query_events(event_type="shell_cmd", since=ds, until=de)
        by_hour = [0] * len(hours)
        for ev in events:
            ts = ev.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts)
                hi = dt.hour - hours[0]
                if 0 <= hi < len(hours):
                    by_hour[hi] += 1
            except (ValueError, TypeError):
                continue
        # Convert counts -> intensity 0-3
        max_h = max(by_hour) or 1
        intensity = [0 if v == 0 else 1 if v < max_h * 0.33 else 2 if v < max_h * 0.66 else 3 for v in by_hour]
        grid.append(intensity)
        day_labels.append(day_names[d.weekday()])

    return {"hours": hours, "days": day_labels, "grid": grid}


# ---------------------------------------------------------------------------
# Activity feed
# ---------------------------------------------------------------------------

def fetch_activity(limit: int = 20) -> list[dict[str, Any]]:
    since = week_start()
    events = db.query_events(since=since)[-limit:]
    result = []
    for e in reversed(events):
        data = e.get("data") or {}
        etype = e.get("event_type", "")

        if etype == "git_commit":
            msg = data.get("message", "")[:60]
            text = f"commit {msg}"
            color = "green"
        elif etype == "git_branch_switch":
            text = f"switched to {data.get('to_branch', '')}"
            color = "cyan"
        elif etype == "shell_cmd":
            cmd = data.get("cmd", "")[:60]
            text = cmd
            color = "white" if data.get("exit_code", 0) == 0 else "red"
        elif etype == "file_change":
            fpath = data.get("file", "") or data.get("path", "")
            fname = fpath.rsplit("/", 1)[-1] if fpath else "file"
            text = f"file changed {fname}"
            color = "yellow"
        elif etype == "window_focus":
            text = f"focused {data.get('app', '')}"
            color = "magenta"
        else:
            text = etype
            color = "dim"

        ts = e.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            time_str = dt.strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = ts[:5] if ts else ""

        result.append({
            "time": time_str,
            "color": color,
            "text": text,
            "proj": e.get("project") or "unknown",
            "type": etype,
            "timestamp": ts,
        })
    return result


# ---------------------------------------------------------------------------
# Predicted next & recurring errors (Today screen v2 panels)
# ---------------------------------------------------------------------------

def fetch_predicted_next() -> dict[str, Any] | None:
    """Predict next action for today's most active project."""
    try:
        from devpulse.analyzers.workflow_predictor import WorkflowPredictor

        time_data = time_tracker.compute_time_per_project(since=today_start())
        if not time_data:
            return None
        top_project = max(time_data, key=lambda p: time_data[p]["total_minutes"])
        recent_events = db.query_events(
            event_type="shell_cmd",
            project=top_project,
            since=(datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S"),
        )
        recent_cmds = [
            e["data"].get("cmd", "")
            for e in recent_events[-3:]
            if e.get("data")
        ]
        if not recent_cmds:
            return None

        predictor = WorkflowPredictor()
        predictions = predictor.predict_next(top_project, recent_cmds, top_k=1)
        if not predictions:
            return None

        pred = predictions[0]
        return {
            "project": top_project,
            "commands": pred["commands"][:4],
            "confidence": pred.get("confidence", 0),
        }
    except Exception:
        return None


def fetch_recurring_errors(limit: int = 4) -> list[dict[str, Any]]:
    try:
        from devpulse.analyzers.error_memory import ErrorMemory
        errors = ErrorMemory().get_frequent_errors(days=7, limit=limit * 2)
        recurring = [e for e in errors if e.get("occurrences", 1) >= 2]
        result = []
        for e in recurring[:limit]:
            result.append({
                "pattern": (e.get("error_pattern") or "?")[:50],
                "count": e.get("occurrences", 1),
                "fix": (e.get("fix_description") or "no fix recorded")[:60],
                "resolved": bool(e.get("resolved")),
            })
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Profile (developer fingerprint)
# ---------------------------------------------------------------------------

def fetch_profile(days: int = 30) -> dict[str, Any]:
    try:
        from devpulse.analyzers.developer_fingerprint import DeveloperFingerprint
        fp = DeveloperFingerprint()
        return {
            "energy": fp.generate_energy_map(days),
            "workflow": fp.generate_workflow_fingerprint(days),
            "focus": fp.generate_focus_pattern(days),
        }
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def get_llm_provider():
    """Return the configured LLM provider (or NoopProvider)."""
    from devpulse.config import load_config
    from devpulse.llm.factory import get_provider
    cfg = load_config()
    return get_provider(cfg), cfg


def fetch_insights() -> dict[str, Any]:
    """Generate AI insights via configured LLM. Slow — call from a worker."""
    provider, cfg = get_llm_provider()
    provider_name = provider.name
    model_name = cfg.get("llm", {}).get("model", "")
    available = provider.is_available()

    text = ""
    if available:
        try:
            from devpulse.generators.report_gen import generate_insights
            text = generate_insights(provider, days=30)
        except Exception as exc:
            text = f"Error generating insights: {exc}"

    return {
        "provider": provider_name,
        "model": model_name,
        "available": available,
        "insights": text,
    }


def ask_llm(question: str) -> str:
    """Synchronous LLM ask — call from a worker."""
    provider, _ = get_llm_provider()
    if not provider.is_available():
        return "No LLM provider is configured. Run `devpulse config set llm.provider ollama` to set one up."

    from devpulse.generators.report_gen import _build_activity_summary
    from devpulse.llm.base import DEVPULSE_SYSTEM_PROMPT

    summary = _build_activity_summary(days=30)
    prompt = f"{summary}\n\nUser question: {question}\n\nAnswer concisely based on the data above."
    try:
        response = provider.analyze(prompt, system_prompt=DEVPULSE_SYSTEM_PROMPT)
        return response.content.strip()
    except Exception as exc:
        return f"LLM error: {exc}"


def generate_toil_script(pattern_id: int) -> str:
    """Generate an automation script for a toil pattern. Synchronous — call from worker."""
    pattern = get_toil_pattern_by_id(pattern_id)
    if not pattern:
        return f"Pattern {pattern_id} not found."
    provider, _ = get_llm_provider()
    if not provider.is_available():
        return "No LLM provider configured. Configure one in the Config screen."
    try:
        from devpulse.generators.script_gen import generate_script
        return generate_script(pattern, provider)
    except Exception as exc:
        return f"Error generating script: {exc}"


# ---------------------------------------------------------------------------
# Status / daemon
# ---------------------------------------------------------------------------

def fetch_status() -> dict[str, Any]:
    from devpulse.daemon import is_running, _read_pid

    pid = _read_pid()
    db_path = db.get_db_path()
    db_size_bytes = db_path.stat().st_size if db_path.exists() else 0
    if db_size_bytes >= 1_048_576:
        db_size = f"{db_size_bytes / 1_048_576:.1f} MB"
    else:
        db_size = f"{db_size_bytes / 1024:.0f} KB"

    return {
        "daemon_running": is_running(),
        "pid": pid,
        "db_size": db_size,
        "total_events": db.count_events_today(),
    }


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_cfg() -> dict[str, Any]:
    from devpulse.config import load_config
    return load_config()


def save_cfg(updates: dict[str, Any]) -> None:
    from devpulse.config import load_config, save_config, set_config_value
    cfg = load_config()
    for key, value in updates.items():
        set_config_value(cfg, key, value)
    save_config(cfg)


def restart_daemon() -> str:
    from devpulse.daemon import stop_daemon, start_daemon
    try:
        stop_daemon()
    except Exception:
        pass
    try:
        start_daemon()
        return "Daemon restarted"
    except Exception as exc:
        return f"Restart failed: {exc}"


def stop_daemon() -> str:
    from devpulse.daemon import stop_daemon as _stop
    try:
        _stop()
        return "Daemon stopped"
    except Exception as exc:
        return f"Stop failed: {exc}"
