"""Profile screen — developer fingerprint with energy map, workflow, focus."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import Panel, StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimVerticalScroll


_HOUR_BLOCKS = "▁▂▃▄▅▆▇█"


def _hourly_sparkline(hourly: list[dict]) -> str:
    if not hourly:
        return ""
    counts = [h.get("commands", 0) for h in hourly]
    max_v = max(counts) or 1
    return "".join(_HOUR_BLOCKS[min(7, int(c / max_v * 7))] for c in counts)


def _hour_labels_bar(hourly: list[dict]) -> str:
    """Build hour label string matching sparkline width."""
    if not hourly:
        return "0    4    8    12   16   20   23"
    # One char per hour entry
    n = len(hourly)
    labels = [""] * n
    for i in range(n):
        if i % 4 == 0:
            labels[i] = str(i)
    result = ""
    for lbl in labels:
        result += lbl if lbl else " "
    return result


class ProfileScreen(VimVerticalScroll):
    """Developer fingerprint view."""

    DEFAULT_CSS = """
    ProfileScreen {
        padding: 1 1;
        background: #010102;
    }
    """

    BINDINGS = [
        Binding("R", "regenerate_profile", "Regenerate"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("", id="prof-title")
        yield StatRow(
            StatCard(label="Workflow style",  value="-", accent=True),
            StatCard(label="Best day",        value="-"),
            StatCard(label="Focus trend",     value="-"),
            StatCard(label="Avg focus block", value="-"),
            id="prof-stat-row",
        )
        with Panel("ENERGY MAP"):
            yield Static("", id="prof-energy", classes="muted")
        with Panel("PEAK HOURS"):
            yield Static("", id="prof-peak", classes="muted")
        with Panel("WORKFLOW FINGERPRINT"):
            yield Static("", id="prof-workflow", classes="muted")
        with Panel("FOCUS PATTERN"):
            yield Static("", id="prof-focus", classes="muted")
        yield Static("[dim]  Press [R] to regenerate the profile (may take a moment)[/dim]",
                     classes="muted")

    async def refresh_data(self) -> None:
        self.query_one("#prof-title", Static).update(
            "\n[bold #f7f8f8]Developer Profile[/]  [dim]your unique 30-day fingerprint[/dim]\n"
        )
        self.query_one("#prof-energy", Static).update("[dim]⏳ Computing fingerprint…[/dim]")
        self._load_profile()

    def action_regenerate_profile(self) -> None:
        self.query_one("#prof-energy", Static).update("[dim]⏳ Regenerating profile…[/dim]")
        self._load_profile()

    @work(thread=True, exclusive=True)
    def _load_profile(self) -> None:
        result = tui_data.fetch_profile(days=30)
        self.app.call_from_thread(self._render_profile, result)

    def _render_profile(self, result: dict) -> None:
        if "error" in result:
            self.query_one("#prof-energy", Static).update(
                f"[red]Error: {result['error']}[/red]"
            )
            return

        energy   = result.get("energy", {})
        workflow = result.get("workflow", {})
        focus    = result.get("focus", {})

        # Stat cards
        cards = list(self.query(StatCard))
        if len(cards) >= 4:
            cards[0].update_card(
                value=workflow.get("coder_style", "-").replace("-", " "),
                value_color="#5e6ad2",
                delta=f"{workflow.get('morning_activity_pct', 0)}% morning",
            )
            cards[1].update_card(
                value=energy.get("best_day", "-"),
                value_color="#27a644",
                delta="most active",
            )
            trend = focus.get("trend", "stable")
            trend_color = (
                "#27a644" if trend == "improving"
                else "#e87b5a" if trend == "worsening"
                else "#fbbf24"
            )
            cards[2].update_card(value=trend, value_color=trend_color, delta="last 30 days")
            avg = focus.get("avg_focus_block_min", 0)
            cards[3].update_card(value=f"{avg}m" if avg else "-", delta="deep work")

        # Energy map with sparkline
        hourly = energy.get("hourly", [])
        spark = _hourly_sparkline(hourly)
        hour_labels = _hour_labels_bar(hourly)
        self.query_one("#prof-energy", Static).update(
            f"  [#5e6ad2]{spark}[/#5e6ad2]\n"
            f"  [dim]{hour_labels}[/dim]\n"
            f"  [dim]Total commands: {energy.get('total_commands', 0)}  "
            f"Total commits: {energy.get('total_commits', 0)}[/dim]"
        )

        # Peak / low hours
        peak = energy.get("peak_hours", [])
        low  = energy.get("low_energy_hours", [])
        peak_str = ", ".join(f"{h:02d}:00" for h in peak) if peak else "—"
        low_str  = ", ".join(f"{h:02d}:00" for h in low)  if low  else "—"
        self.query_one("#prof-peak", Static).update(
            f"  [#27a644]●[/#27a644] [bold]Peak hours:[/bold] {peak_str}  "
            f"[dim](highest activity)[/dim]\n"
            f"  [#e87b5a]●[/#e87b5a] [bold]Low energy:[/bold] {low_str}  "
            f"[dim](>20% error rate)[/dim]"
        )

        # Workflow fingerprint
        tools = workflow.get("tools_top_5", [])
        wf_lines = [
            f"  [bold]Top tools:[/bold] {' · '.join(tools) if tools else '—'}",
            f"  [bold]Commits/day:[/bold] {workflow.get('commits_per_day', 0)}  "
            f"  [bold]Avg commit size:[/bold] {workflow.get('avg_commit_size_lines', 0)} lines",
            f"  [bold]Avg projects/day:[/bold] {workflow.get('avg_projects_per_day', 0)}  "
            f"  [bold]Morning activity:[/bold] {workflow.get('morning_activity_pct', 0)}%",
        ]
        self.query_one("#prof-workflow", Static).update("\n".join(wf_lines))

        # Focus pattern
        distractors = focus.get("top_distractors", [])
        fc_lines = [
            f"  [bold]Total deep blocks:[/bold] {focus.get('total_deep_blocks', 0)}  "
            f"  [bold]Longest:[/bold] {focus.get('longest_focus_block_min', 0)}m",
            f"  [bold]Best focus day:[/bold] {focus.get('best_focus_day', '—')}  "
            f"  [bold]Switches/day:[/bold] {focus.get('switches_per_day', 0):.1f}",
            f"  [bold]Top distractors:[/bold] "
            f"{', '.join(distractors) if distractors else '—'}",
        ]
        self.query_one("#prof-focus", Static).update("\n".join(fc_lines))
