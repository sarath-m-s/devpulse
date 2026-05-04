"""Today screen — stat cards, project bars, activity, deep work, predicted next, errors."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.widgets import Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import BarChart, StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimVerticalScroll


def _fmt_dur(m: int | float) -> str:
    return tui_data.fmt_dur(m)


class TodayScreen(VimVerticalScroll):
    """The Today's Pulse view."""

    DEFAULT_CSS = """
    TodayScreen {
        padding: 1 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="today-page-title")
        yield StatRow(
            StatCard(label="Total dev time", value="-", delta="", accent=True),
            StatCard(label="Commands", value="-"),
            StatCard(label="Commits", value="-"),
            StatCard(label="Context switches", value="-"),
            StatCard(label="Focus score", value="-"),
            id="today-stat-row",
        )

        yield Static("PROJECTS ── time by project", classes="section-bar")
        yield BarChart(empty_msg="No project activity recorded today", label_width=18, bar_width=30)

        yield Static("DEEP WORK BLOCKS", classes="section-bar")
        yield Static("", id="today-blocks", classes="muted")

        yield Static("RECENT ACTIVITY", classes="section-bar")
        yield Static("", id="today-activity", classes="muted")

        yield Static("PREDICTED NEXT", classes="section-bar")
        yield Static("", id="today-predicted", classes="muted")

        yield Static("RECURRING ERRORS", classes="section-bar")
        yield Static("", id="today-errors", classes="muted")

    async def refresh_data(self) -> None:
        try:
            t = tui_data.fetch_today()
            events = tui_data.fetch_activity(limit=10)
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
        title = self.query_one("#today-page-title", Static)
        title.update(
            f"\n[bold]Today's Pulse[/bold]  [dim]{date_str} · {time_str}[/dim]\n"
        )

        # Stat cards
        cards = list(self.query(StatCard))
        if len(cards) >= 5:
            cards[0].update_card(value=_fmt_dur(t["total_minutes"]), delta=f"across {t['project_count']} projects")
            cards[1].update_card(value=str(t["total_cmds"]), delta="today")
            cards[2].update_card(value=str(t["total_commits"]), delta="commits")
            sw = t["switches"]
            cards[3].update_card(
                value=str(sw),
                value_color="#e87b5a" if sw > 20 else "white",
                delta=f"frag {t['fragmentation_score']:.0f}/100",
            )
            fc = t["focus_score"]
            color = "#27a644" if fc >= 70 else "#d97706" if fc >= 40 else "#e87b5a"
            cards[4].update_card(value=f"{fc}/100", value_color=color, delta="higher = better")

        # Project bars
        projs = t.get("projects", [])
        bar = self.query_one(BarChart)
        bar.set_rows([
            {"label": p["name"], "value": _fmt_dur(p["minutes"]), "pct": p["pct"]}
            for p in projs if p["minutes"] > 0
        ])

        # Deep work
        blocks_widget = self.query_one("#today-blocks", Static)
        blocks = t.get("deep_work_blocks", [])
        if blocks:
            blocks.sort(key=lambda b: b.get("duration_minutes", 0), reverse=True)
            lines = []
            for b in blocks[:5]:
                lines.append(
                    f" [dim]{b.get('start','')}–{b.get('end','')}[/dim]  "
                    f"[bold]{b.get('project','')[:18]:<18}[/bold]  "
                    f"[#27a644]{_fmt_dur(b.get('duration_minutes', 0))}[/#27a644]"
                )
            blocks_widget.update("\n".join(lines))
        else:
            blocks_widget.update("[dim]No deep work blocks recorded yet today[/dim]")

        # Activity
        activity_widget = self.query_one("#today-activity", Static)
        if events:
            lines = []
            for e in events:
                color = e.get("color", "white")
                text = e.get("text", "")[:80]
                proj = e.get("proj", "")[:14]
                time = e.get("time", "")
                lines.append(
                    f" [dim]{time:<6}[/dim] [{color}]●[/{color}] {text:<80}  "
                    f"[dim]{proj}[/dim]"
                )
            activity_widget.update("\n".join(lines))
        else:
            activity_widget.update("[dim]No recent activity[/dim]")

        # Predicted next
        pred_widget = self.query_one("#today-predicted", Static)
        if predicted:
            cmds_str = " && ".join(predicted["commands"])[:80]
            conf_pct = f"{predicted['confidence']*100:.0f}%"
            pred_widget.update(
                f" [bold #fbbf24]⚡[/bold #fbbf24] [bold]{predicted['project']}[/bold]: "
                f"[white]{cmds_str}[/white]  [dim]({conf_pct} confidence)[/dim]\n"
                f" [dim]Run: devpulse next {predicted['project']}[/dim]"
            )
        else:
            pred_widget.update("[dim]No prediction available — keep using your shell to build patterns[/dim]")

        # Errors
        errors_widget = self.query_one("#today-errors", Static)
        if errors:
            lines = []
            for e in errors:
                resolved = "✓" if e.get("resolved") else "○"
                lines.append(
                    f" [#e87b5a]●[/#e87b5a] {e['pattern']:<50}  "
                    f"[#e87b5a]×{e['count']}[/#e87b5a]  "
                    f"[dim]{resolved} {e['fix']}[/dim]"
                )
            errors_widget.update("\n".join(lines))
        else:
            errors_widget.update("[dim]No recurring errors[/dim]")
