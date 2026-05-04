"""Weekly/daily digest generator — builds LLM prompts from activity data."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ghost_pulse.llm.base import LLMProvider, GHOST_PULSE_SYSTEM_PROMPT
from ghost_pulse.analyzers import time_tracker, context_switch, toil as toil_analyzer


def _build_activity_summary(days: int = 7) -> str:
    """Collect and format recent activity into a structured text summary."""
    now = datetime.now()
    since = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")

    time_data = time_tracker.compute_time_per_project(since=since)
    ctx = context_switch.compute_context_switches(since=since)
    toil_patterns = toil_analyzer.get_ranked_patterns()[:5]

    lines: list[str] = [
        f"Activity summary — last {days} days",
        f"Period: {since[:10]} to {now.strftime('%Y-%m-%d')}",
        "",
        "## Time per project",
    ]
    for proj, stats in sorted(
        time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
    ):
        hours = stats["total_minutes"] / 60
        lines.append(f"  {proj}: {hours:.1f}h")
        for cat, mins in sorted(
            stats.get("by_category", {}).items(), key=lambda x: x[1], reverse=True
        ):
            lines.append(f"    └ {cat}: {mins:.0f}m")

    lines += [
        "",
        "## Focus quality",
        f"  Context switches: {ctx['switches']}",
        f"  Fragmentation score: {ctx['fragmentation_score']}/100",
        f"  Unique projects: {ctx['unique_projects']}",
    ]

    if ctx["deep_work_blocks"]:
        lines.append("  Deep work blocks:")
        for b in ctx["deep_work_blocks"][:3]:
            lines.append(
                f"    {b['start']}-{b['end']} on {b['project']} ({b['duration_minutes']}m)"
            )

    if toil_patterns:
        lines += ["", "## Top toil patterns (automation opportunities)"]
        for p in toil_patterns:
            cmds = " → ".join(p.get("commands", []))
            lines.append(f"  [{p['count']}x] {cmds[:80]}")

    return "\n".join(lines)


def generate_insights(provider: LLMProvider, days: int = 7) -> str:
    """Send activity summary to LLM and return personalized insights."""
    summary = _build_activity_summary(days=days)
    prompt = f"{summary}\n\nBased on this data, give me your top 3-5 insights."
    response = provider.analyze(prompt, system_prompt=GHOST_PULSE_SYSTEM_PROMPT)
    return response.content.strip()


def generate_daily_summary_text(provider: LLMProvider | None = None) -> str:
    """Build a plain-text daily summary (no LLM needed)."""
    return _build_activity_summary(days=1)
