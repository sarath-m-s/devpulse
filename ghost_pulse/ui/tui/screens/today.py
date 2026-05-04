"""Today screen — k9s-inspired pulse view with timeline, activity feed."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from textual.app import ComposeResult
from textual.widgets import Static

from ghost_pulse.ui.tui import data as tui_data
from ghost_pulse.ui.tui.widgets import BarChart, Panel, StatCard, StatRow
from ghost_pulse.ui.tui.vim_scroll import VimVerticalScroll


def _fmt(m: int | float) -> str:
    return tui_data.fmt_dur(m)


_EVENT_ICONS = {
    "git_commit":       "[#27a644]●[/]",
    "git_branch_switch": "[#fbbf24]⇄[/]",
    "window_focus":     "[#a78bfa]◎[/]",
    "file_change":      "[#d97706]Δ[/]",
}


def _activity_icon(etype: str, data: dict) -> str:
    if etype == "shell_cmd":
        exit_code = (data or {}).get("exit_code", 0)
        return "[#5e6ad2]►[/]" if exit_code == 0 else "[#e87b5a]✗[/]"
    return _EVENT_ICONS.get(etype, "[dim]·[/]")


_HOURS = list(range(8, 20))   # 08–19 inclusive
_PROJ_COLORS = [
    "#5e6ad2", "#fbbf24", "#4ade80", "#f472b6",
    "#22d3ee", "#fb923c", "#a78bfa", "#34d399",
]


def _build_timeline(events: list[dict]) -> str:
    """Build a text timeline: project row × hour columns."""
    # Map project → set of active hour slots
    proj_hours: dict[str, set[int]] = defaultdict(set)
    for ev in events:
        time_str = ev.get("time", "")
        proj = ev.get("proj") or "unknown"
        try:
            hour = int(time_str.split(":")[0])
        except (ValueError, IndexError, AttributeError):
            continue
        if hour in _HOURS:
            proj_hours[proj].add(hour)

    if not proj_hours:
        return "[dim]No activity data for timeline[/dim]"

    # Sort projects by number of active hours desc
    sorted_projs = sorted(proj_hours.items(), key=lambda x: len(x[1]), reverse=True)[:8]

    # Header
    hour_header = "                    " + "".join(f"{h:^3}" for h in _HOURS)
    lines = [f"[dim]{hour_header}[/dim]"]

    for i, (proj, active_hours) in enumerate(sorted_projs):
        color = _PROJ_COLORS[i % len(_PROJ_COLORS)]
        label = proj[:18].ljust(18)
        bar = ""
        for h in _HOURS:
            bar += f"[{color}]██[/{color}]" if h in active_hours else "[dim #23252a]░░[/dim #23252a]"
            bar += " "
        lines.append(f"[{color}]{label}[/{color}]  {bar}")

    return "\n".join(lines)


class TodayScreen(VimVerticalScroll):
    """Today's Pulse view."""

    DEFAULT_CSS = """
    TodayScreen {
        padding: 1 1;
        background: #010102;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="today-page-title")
        yield StatRow(
            StatCard(label="Dev time",        value="-", accent=True),
            StatCard(label="Commands",        value="-"),
            StatCard(label="Commits",         value="-"),
            StatCard(label="Ctx switches",    value="-"),
            StatCard(label="Focus score",     value="-"),
            id="today-stat-row",
        )
        with Panel("TIME BY PROJECT"):
            yield BarChart(empty_msg="No project activity recorded today",
                           label_width=18, bar_width=30, id="today-bar-chart")
        with Panel("ACTIVITY TIMELINE  08–19"):
            yield Static("", id="today-timeline", classes="muted")
        with Panel("RECENT ACTIVITY"):
            yield Static("", id="today-activity", classes="muted")
        with Panel("DEEP WORK BLOCKS"):
            yield Static("", id="today-blocks", classes="muted")
        with Panel("PREDICTED NEXT"):
            yield Static("", id="today-predicted", classes="muted")
        with Panel("RECURRING ERRORS"):
            yield Static("", id="today-errors", classes="muted")

    async def refresh_data(self) -> None:
        try:
            t = tui_data.fetch_today()
            events = tui_data.fetch_activity(limit=500)
            recent_events = events[:20]
            predicted = tui_data.fetch_predicted_next()
            errors = tui_data.fetch_recurring_errors()
        except Exception as exc:
            self.query_one("#today-page-title", Static).update(
                f"[red]Error loading data: {exc}[/red]"
            )
            return

        # Title
        now = datetime.now()
        date_str = now.strftime("%A, %B %-d, %Y")
        time_str = now.strftime("%H:%M")
        fc = t.get("focus_score", 0)
        bar_filled = int(fc / 100 * 20)
        focus_bar = (
            f"[#5e6ad2]{'█' * bar_filled}[/#5e6ad2]"
            f"[dim #23252a]{'░' * (20 - bar_filled)}[/]"
        )
        self.query_one("#today-page-title", Static).update(
            f"\n[bold #f7f8f8]Today's Pulse[/]  [dim]{date_str}[/dim]  "
            f"[dim]{time_str}[/dim]  focus {focus_bar} [dim]{fc}/100[/dim]\n"
        )

        # Stat cards
        cards = list(self.query(StatCard))
        if len(cards) >= 5:
            cards[0].update_card(
                value=_fmt(t["total_minutes"]),
                delta=f"{t['project_count']} projects",
            )
            cards[1].update_card(value=str(t["total_cmds"]), delta="today")
            cards[2].update_card(value=str(t["total_commits"]), delta="commits")
            sw = t["switches"]
            cards[3].update_card(
                value=str(sw),
                value_color="#e87b5a" if sw > 20 else "#f7f8f8",
                delta=f"frag {t['fragmentation_score']:.0f}/100",
            )
            color = "#27a644" if fc >= 70 else "#d97706" if fc >= 40 else "#e87b5a"
            cards[4].update_card(value=f"{fc}/100", value_color=color, delta="higher = better")

        # Project bars
        projs = t.get("projects", [])
        try:
            bar = self.query_one("#today-bar-chart", BarChart)
            bar.set_rows([
                {"label": p["name"], "value": _fmt(p["minutes"]), "pct": p["pct"]}
                for p in projs if p["minutes"] > 0
            ])
        except Exception:
            pass

        # Timeline
        try:
            tl_widget = self.query_one("#today-timeline", Static)
            tl_widget.update(_build_timeline(events))
        except Exception:
            pass

        # Activity feed
        activity_widget = self.query_one("#today-activity", Static)
        if recent_events:
            lines = []
            for e in recent_events[:15]:
                etype = e.get("type", "")
                data_raw = {}
                icon = _activity_icon(etype, data_raw)
                text = e.get("text", "")[:72]
                proj = e.get("proj", "")[:14]
                time = e.get("time", "")
                lines.append(
                    f"  [dim]{time:<6}[/dim] {icon} {text:<72}  [dim]{proj}[/dim]"
                )
            activity_widget.update("\n".join(lines))
        else:
            activity_widget.update("[dim]No recent activity[/dim]")

        # Deep work blocks
        blocks_widget = self.query_one("#today-blocks", Static)
        blocks = t.get("deep_work_blocks", [])
        if blocks:
            blocks.sort(key=lambda b: b.get("duration_minutes", 0), reverse=True)
            lines = []
            for b in blocks[:6]:
                lines.append(
                    f"  [dim]{b.get('start', '')}–{b.get('end', '')}[/dim]  "
                    f"[bold #f7f8f8]{b.get('project', '')[:20]:<20}[/bold #f7f8f8]  "
                    f"[#27a644]{_fmt(b.get('duration_minutes', 0))}[/#27a644]"
                )
            blocks_widget.update("\n".join(lines))
        else:
            blocks_widget.update("[dim]No deep work blocks recorded yet today[/dim]")

        # Predicted next
        pred_widget = self.query_one("#today-predicted", Static)
        if predicted:
            cmds_str = " && ".join(predicted["commands"])[:80]
            conf_pct = f"{predicted['confidence'] * 100:.0f}%"
            pred_widget.update(
                f"  [bold #fbbf24]⚡[/bold #fbbf24] [bold]{predicted['project']}[/bold]: "
                f"[#f7f8f8]{cmds_str}[/#f7f8f8]  [dim]({conf_pct} confidence)[/dim]\n"
                f"  [dim]Run: ghost next {predicted['project']}[/dim]"
            )
        else:
            pred_widget.update("[dim]No prediction available — keep using your shell to build patterns[/dim]")

        # Recurring errors
        errors_widget = self.query_one("#today-errors", Static)
        if errors:
            lines = []
            for err in errors:
                resolved = "[#27a644]✓[/#27a644]" if err.get("resolved") else "[dim]○[/dim]"
                lines.append(
                    f"  [#e87b5a]●[/#e87b5a] {err['pattern']:<50}  "
                    f"[#e87b5a]×{err['count']}[/#e87b5a]  "
                    f"{resolved} [dim]{err['fix']}[/dim]"
                )
            errors_widget.update("\n".join(lines))
        else:
            errors_widget.update("[dim]No recurring errors[/dim]")
