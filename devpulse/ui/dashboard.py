"""Rich terminal dashboard for devpulse today / week views."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn
from rich.table import Table
from rich.text import Text

from devpulse import db
from devpulse.analyzers import context_switch, time_tracker, toil as toil_analyzer

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_duration(minutes: float) -> str:
    h = int(minutes) // 60
    m = int(minutes) % 60
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m"


def _bar(fraction: float, width: int = 20) -> str:
    filled = round(fraction * width)
    return "█" * filled + "░" * (width - filled)


def _sparkline(values: list[float]) -> str:
    chars = "▁▂▃▄▅▆▇█"
    if not values:
        return ""
    max_v = max(values) or 1
    return "".join(chars[min(7, int(v / max_v * 7))] for v in values)


# ---------------------------------------------------------------------------
# Today dashboard
# ---------------------------------------------------------------------------

def render_today(width: int | None = None) -> None:
    """Print the full 'devpulse today' dashboard."""
    now = datetime.now()
    today_str = now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )

    time_data = time_tracker.compute_time_per_project(since=today_str)
    ctx = context_switch.compute_context_switches(since=today_str)
    toil_patterns = toil_analyzer.get_ranked_patterns()[:5]
    total_cmds = db.count_events_today()

    total_minutes = sum(p["total_minutes"] for p in time_data.values())
    total_commits = sum(p["commits"] for p in time_data.values())
    switches = ctx["switches"]
    frag = ctx["fragmentation_score"]

    # Focus score: inverse of fragmentation
    focus_score = max(0, 100 - int(frag))

    # Header panel
    date_str = now.strftime("%A, %B %-d")
    score_color = "green" if focus_score >= 70 else "yellow" if focus_score >= 40 else "red"
    score_bar = _bar(focus_score / 100, 10)

    stats_table = Table(show_header=False, box=None, padding=(0, 3))
    stats_table.add_column(justify="center")
    stats_table.add_column(justify="center")
    stats_table.add_column(justify="center")
    stats_table.add_column(justify="center")
    stats_table.add_column(justify="center")
    stats_table.add_row(
        f"⏱  [bold cyan]{_fmt_duration(total_minutes)}[/bold cyan]\n[dim]active[/dim]",
        f"📊 [bold cyan]{total_cmds}[/bold cyan]\n[dim]commands[/dim]",
        f"💾 [bold cyan]{total_commits}[/bold cyan]\n[dim]commits[/dim]",
        f"🔀 [bold cyan]{switches}[/bold cyan]\n[dim]switches[/dim]",
        f"🎯 [bold {score_color}]{focus_score}/100[/bold {score_color}]\n[dim][{score_color}]{score_bar}[/{score_color}][/dim]",
    )

    console.print(
        Panel(
            stats_table,
            title=f"[bold blue]DevPulse[/bold blue]  [dim]·[/dim]  [white]{date_str}[/white]",
            border_style="blue",
            box=box.ROUNDED,
            padding=(1, 1),
        )
    )

    # Projects section
    if time_data:
        proj_table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1), show_edge=False)
        proj_table.add_column("Project", style="bold cyan", min_width=20)
        proj_table.add_column("", min_width=24)  # bar
        proj_table.add_column("Time", justify="right", style="white")
        proj_table.add_column("%", justify="right", style="dim")

        total_m = max(total_minutes, 1)
        bar_colors = ["green", "blue", "magenta", "yellow", "cyan"]
        sorted_projects = sorted(
            time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
        )
        for i, (proj, stats) in enumerate(sorted_projects[:8]):
            mins = stats["total_minutes"]
            pct = mins / total_m
            color = bar_colors[i % len(bar_colors)]
            bar = _bar(pct, 22)
            proj_table.add_row(
                proj[:20],
                f"[{color}]{bar}[/{color}]",
                _fmt_duration(mins),
                f"{pct*100:.0f}%",
            )

        console.print(
            Panel(proj_table, title="[bold]📁 Projects[/bold]", border_style="cyan", box=box.ROUNDED)
        )
    else:
        console.print(
            Panel(
                "[dim]No project activity yet today.[/dim]",
                title="[bold]Projects[/bold]",
                box=box.ROUNDED,
            )
        )

    # Toil section
    if toil_patterns:
        toil_table = Table(show_header=False, box=None, padding=(0, 1))
        toil_table.add_column("Icon", width=2)
        toil_table.add_column("Pattern", style="yellow")
        toil_table.add_column("Count", justify="right", style="dim")

        for i, p in enumerate(toil_patterns[:4]):
            cmds = " → ".join(p.get("commands", []))
            if len(cmds) > 60:
                cmds = cmds[:57] + "…"
            toil_table.add_row("🔄", cmds, f"×{p['count']}")

        hint = Text(
            "  Run: devpulse suggest <id>  to generate an automation",
            style="dim italic",
        )
        from rich.console import Group
        toil_content = Group(toil_table, hint)
        console.print(
            Panel(toil_content, title="[bold]Toil detected[/bold]", box=box.ROUNDED)
        )

    # Deep work blocks
    blocks = ctx.get("deep_work_blocks", [])
    if blocks:
        block_table = Table(show_header=False, box=None, padding=(0, 1))
        block_table.add_column("Time", style="cyan", min_width=13)
        block_table.add_column("Project", style="bold", min_width=14)
        block_table.add_column("Duration", style="green", min_width=8)
        block_table.add_column("Bar")

        max_dur = max(b["duration_minutes"] for b in blocks)
        for b in sorted(blocks, key=lambda x: x["duration_minutes"], reverse=True)[:5]:
            bar = _bar(b["duration_minutes"] / max(max_dur, 1), 16)
            block_table.add_row(
                f"{b['start']} - {b['end']}",
                b["project"][:14],
                _fmt_duration(b["duration_minutes"]),
                f"[blue]{bar}[/blue]",
            )

        console.print(
            Panel(block_table, title="[bold]Deep work blocks[/bold]", box=box.ROUNDED)
        )


# ---------------------------------------------------------------------------
# Week dashboard
# ---------------------------------------------------------------------------

def render_week() -> None:
    """Print the 'devpulse week' summary."""
    now = datetime.now()
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")

    time_data = time_tracker.compute_time_per_project(since=week_ago)
    ctx = context_switch.compute_context_switches(since=week_ago)
    toil_patterns = toil_analyzer.get_ranked_patterns()[:5]

    console.print(
        Panel(
            "",
            title=f"[bold blue]DevPulse · Weekly Summary · {now.strftime('%b %-d')}[/bold blue]",
            box=box.ROUNDED,
        )
    )

    # Weekly project time bar chart
    if time_data:
        total_m = max(sum(p["total_minutes"] for p in time_data.values()), 1)
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column("Project", style="bold", min_width=18)
        table.add_column("Bar", min_width=30)
        table.add_column("Hours", justify="right", style="cyan")

        for proj, stats in sorted(
            time_data.items(), key=lambda x: x[1]["total_minutes"], reverse=True
        )[:8]:
            mins = stats["total_minutes"]
            bar = _bar(mins / total_m, 28)
            table.add_row(proj[:18], f"[green]{bar}[/green]", f"{mins/60:.1f}h")

        console.print(
            Panel(table, title="[bold]Time per project[/bold]", box=box.ROUNDED)
        )

    # Focus summary
    focus_text = Text()
    # Check if data quality is limited (all projects are "unknown" = backfill data)
    all_unknown = set(time_data.keys()) <= {"unknown"}
    if all_unknown and time_data:
        focus_text.append(
            "  ⚠  Project names are 'unknown' because data came from shell history backfill\n"
            "     (cwd not stored in history). Context switching requires live hook data.\n"
            "     Run a few commands in different project directories to see accurate metrics.\n\n",
            style="yellow",
        )
    focus_text.append(f"  Context switches: {ctx['switches']}\n")
    focus_text.append(f"  Fragmentation score: {ctx['fragmentation_score']}/100\n")
    focus_text.append(f"  Switches/day: {ctx.get('switches_per_day', 0):.1f}  ")
    focus_text.append(f"Switches/hour: {ctx.get('switches_per_hour', 0):.2f}\n")
    if ctx["top_transitions"] and not all_unknown:
        focus_text.append("  Most common transitions:\n")
        for t in ctx["top_transitions"][:3]:
            focus_text.append(
                f"    {t['from_project']} → {t['to_project']}: {t['count']}x\n"
            )
    console.print(
        Panel(focus_text, title="[bold]Focus quality[/bold]", box=box.ROUNDED)
    )

    # Top toil patterns
    if toil_patterns:
        table2 = Table(
            "ID", "Pattern", "Count", "Est. wasted", box=box.SIMPLE, show_header=True
        )
        table2.columns[0].style = "dim"
        table2.columns[2].style = "yellow"
        table2.columns[3].style = "red"
        for p in toil_patterns:
            cmds = " → ".join(p.get("commands", []))[:55]
            wasted = toil_analyzer.estimate_time_wasted(p)
            table2.add_row(
                str(p.get("id", "?")),
                cmds,
                f"×{p['count']}",
                f"~{wasted:.1f}h",
            )
        console.print(
            Panel(table2, title="[bold]Top toil patterns[/bold]", box=box.ROUNDED)
        )
