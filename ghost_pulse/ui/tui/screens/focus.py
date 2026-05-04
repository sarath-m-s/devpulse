"""Focus screen — context switching, deep work blocks, heatmap."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from ghost_pulse.ui.tui import data as tui_data
from ghost_pulse.ui.tui.vim_scroll import VimVerticalScroll
from ghost_pulse.ui.tui.widgets import Heatmap, Panel, StatCard, StatRow, SwitchChart


class FocusScreen(VimVerticalScroll):
    """Focus analysis view."""

    DEFAULT_CSS = """
    FocusScreen {
        padding: 1 1;
        background: #010102;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="focus-title")
        yield StatRow(
            StatCard(label="Avg focus block", value="-", accent=True),
            StatCard(label="Best focus day",  value="-"),
            StatCard(label="Worst day",       value="-"),
            StatCard(label="Today switches",  value="-"),
            id="focus-stat-row",
        )
        with Panel("FOCUS BLOCKS — this week"):
            yield SwitchChart(id="focus-block-chart", height_lines=5,
                              bar_color_override="#27a644")
        with Panel("CONTEXT SWITCHES — this week"):
            yield SwitchChart(id="focus-switch-chart", height_lines=5)
        with Panel("DEEP WORK BLOCKS TODAY"):
            yield Static("", id="focus-blocks", classes="muted")
        with Panel("PROJECT TRANSITIONS"):
            yield Static("", id="focus-transitions", classes="muted")
        with Panel("ACTIVITY HEATMAP"):
            yield Heatmap(id="focus-heatmap")

    async def refresh_data(self) -> None:
        try:
            data = tui_data.fetch_focus()
            heatmap = tui_data.fetch_heatmap()
        except Exception as exc:
            self.query_one("#focus-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        self.query_one("#focus-title", Static).update(
            "\n[bold #f7f8f8]Focus Analysis[/]  [dim]context switching and deep work[/dim]\n"
        )

        t = data.get("today", {})
        w = data.get("week", {})
        cards = list(self.query(StatCard))
        if len(cards) >= 4:
            avg = w.get("avg_focus_block", 0)
            cards[0].update_card(value=f"{avg}m" if avg else "-", delta="this week")
            cards[1].update_card(
                value=w.get("best_day", "-"),
                value_color="#27a644",
                delta="fewest switches",
            )
            cards[2].update_card(
                value=w.get("worst_day", "-"),
                value_color="#e87b5a",
                delta="most switches",
            )
            sw = t.get("switches", 0)
            cards[3].update_card(
                value=str(sw),
                delta=f"focus {t.get('focus_score', 0)}/100",
                value_color="#e87b5a" if sw > 20 else "#f7f8f8",
            )

        # Focus block chart (green override)
        focus_chart = self.query_one("#focus-block-chart", SwitchChart)
        focus_chart.set_items([
            {"day": d["day"], "value": d["longest_block"], "is_today": d.get("is_today", False)}
            for d in data.get("daily_focus", [])
        ])

        # Context switch chart
        sw_chart = self.query_one("#focus-switch-chart", SwitchChart)
        sw_chart.set_items([
            {"day": d["day"], "value": d["switches"], "is_today": d.get("is_today", False)}
            for d in data.get("daily_switches", [])
        ])

        # Deep work blocks
        blocks_widget = self.query_one("#focus-blocks", Static)
        blocks = t.get("deep_work_blocks", [])
        if blocks:
            blocks.sort(key=lambda b: b.get("duration_minutes", 0), reverse=True)
            lines = []
            for b in blocks[:6]:
                lines.append(
                    f"  [dim]{b.get('start', '')}–{b.get('end', '')}[/dim]  "
                    f"[bold #f7f8f8]{b.get('project', '')[:20]:<20}[/bold #f7f8f8]  "
                    f"[#27a644]{tui_data.fmt_dur(b.get('duration_minutes', 0))}[/#27a644]"
                )
            blocks_widget.update("\n".join(lines))
        else:
            blocks_widget.update("[dim]No deep work blocks today[/dim]")

        # Transitions
        trans_widget = self.query_one("#focus-transitions", Static)
        transitions = w.get("top_transitions", [])
        if transitions:
            max_c = max(tr["count"] for tr in transitions) or 1
            lines = []
            for tr in transitions:
                bw = int(tr["count"] / max_c * 24)
                fill = (
                    f"[#5e6ad2]{'█' * bw}[/#5e6ad2]"
                    f"[dim #23252a]{'░' * (24 - bw)}[/]"
                )
                lines.append(
                    f"  [bold]{tr['from_project'][:16]:<16}[/bold] "
                    f"[dim]→[/dim] "
                    f"[bold]{tr['to_project'][:16]:<16}[/bold]  "
                    f"{fill}  [dim]×{tr['count']}[/dim]"
                )
            trans_widget.update("\n".join(lines))
        else:
            trans_widget.update("[dim]No transitions recorded[/dim]")

        # Heatmap
        hm = self.query_one("#focus-heatmap", Heatmap)
        hm.set_data(
            heatmap.get("hours", []),
            heatmap.get("days", []),
            heatmap.get("grid", []),
        )
