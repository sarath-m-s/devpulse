"""Week screen — k9s-inspired 7-day rolling window view."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from ghost_pulse.ui.tui import data as tui_data
from ghost_pulse.ui.tui.vim_scroll import VimVerticalScroll
from ghost_pulse.ui.tui.widgets import BarChart, Panel, StatCard, StatRow, SwitchChart


class WeekScreen(VimVerticalScroll):
    """This Week view."""

    DEFAULT_CSS = """
    WeekScreen {
        padding: 1 1;
        background: #010102;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="week-title")
        yield StatRow(
            StatCard(label="Total this week", value="-", accent=True),
            StatCard(label="Commits",         value="-"),
            StatCard(label="Avg focus block", value="-"),
            StatCard(label="Best day",        value="-"),
            id="week-stat-row",
        )
        with Panel("DAILY HOURS"):
            yield SwitchChart(id="week-hours-chart", height_lines=6)
        with Panel("PROJECTS THIS WEEK"):
            yield BarChart(empty_msg="No project activity this week",
                           label_width=18, bar_width=30, id="week-bar-chart")
        with Panel("CONTEXT SWITCHES"):
            yield SwitchChart(id="week-switch-chart", height_lines=6)
        with Panel("TOP PROJECT TRANSITIONS"):
            yield Static("", id="week-transitions", classes="muted")

    async def refresh_data(self) -> None:
        try:
            w = tui_data.fetch_week()
        except Exception as exc:
            self.query_one("#week-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        self.query_one("#week-title", Static).update(
            "\n[bold #f7f8f8]This Week[/]  [dim]7-day rolling window[/dim]\n"
        )

        cards = list(self.query(StatCard))
        if len(cards) >= 4:
            cards[0].update_card(
                value=f"{w['total_hours']}h",
                delta=tui_data.fmt_dur(w["total_minutes"]),
            )
            cards[1].update_card(value=str(w["total_commits"]), delta="this week")
            avg = w.get("avg_focus_block", 0)
            cards[2].update_card(value=f"{avg}m" if avg else "-", delta="this week")
            best = w.get("best_day")
            if best:
                cards[3].update_card(
                    value=best["day"],
                    value_color="#27a644",
                    delta=f"{best['hours']}h · {best['commits']} commits",
                )
            else:
                cards[3].update_card(value="-")

        daily = w.get("daily", [])

        # Daily hours chart
        hours_chart = self.query_one("#week-hours-chart", SwitchChart)
        hours_chart.bar_color_override = None
        hours_chart.set_items([
            {"day": d["day"], "value": d["hours"], "is_today": d.get("is_today", False)}
            for d in daily
        ])

        # Project bars
        bar = self.query_one("#week-bar-chart", BarChart)
        bar.set_rows([
            {"label": p["name"], "value": f"{p['hours']}h", "pct": p["pct"]}
            for p in w.get("projects", [])
        ])

        # Context switch chart
        sw_chart = self.query_one("#week-switch-chart", SwitchChart)
        sw_chart.set_items([
            {"day": d["day"], "value": d["switches"], "is_today": d.get("is_today", False)}
            for d in daily
        ])

        # Transitions
        trans_widget = self.query_one("#week-transitions", Static)
        transitions = w.get("top_transitions", [])
        if transitions:
            max_c = max(t["count"] for t in transitions) or 1
            lines = []
            for t in transitions:
                bar_w = int(t["count"] / max_c * 24)
                fill = f"[#5e6ad2]{'█' * bar_w}[/#5e6ad2][dim #23252a]{'░' * (24 - bar_w)}[/]"
                lines.append(
                    f"  [bold]{t['from_project'][:16]:<16}[/bold] "
                    f"[dim]→[/dim] "
                    f"[bold]{t['to_project'][:16]:<16}[/bold]  "
                    f"{fill}  [dim]×{t['count']}[/dim]"
                )
            trans_widget.update("\n".join(lines))
        else:
            trans_widget.update("[dim]No project transitions this week[/dim]")
