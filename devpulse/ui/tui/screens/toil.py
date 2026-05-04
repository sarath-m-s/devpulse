"""Toil screen — pattern detection with inline LLM suggest action."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import Panel, StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimDataTable, VimVerticalScroll


class ToilScreen(VimVerticalScroll):
    """Toil detector view with Apply / Suggest action."""

    DEFAULT_CSS = """
    ToilScreen {
        padding: 1 1;
        background: #010102;
    }
    ToilScreen VimDataTable {
        height: auto;
        max-height: 18;
        background: #0d0e11;
    }
    """

    BINDINGS = [
        Binding("enter", "apply_selected", "Suggest"),
        Binding("a",     "apply_selected", "Suggest", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._patterns: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="toil-title")
        yield StatRow(
            StatCard(label="Patterns found",    value="-"),
            StatCard(label="Total repetitions", value="-"),
            StatCard(label="Est. time wasted",  value="-", accent=True),
            id="toil-stat-row",
        )
        with Panel("DETECTED PATTERNS — j/k to navigate, Enter/a to generate alias"):
            yield VimDataTable(id="toil-table", zebra_stripes=True, cursor_type="row")
        with Panel("LLM SUGGESTION"):
            yield Static(
                "[dim]Select a pattern and press Enter or [a] to generate an alias/script[/dim]",
                id="toil-output",
                classes="muted",
            )

    async def on_mount(self) -> None:
        table = self.query_one(VimDataTable)
        table.add_columns("ID", "Pattern", "×Count", "~Wasted")

    async def refresh_data(self) -> None:
        try:
            patterns = tui_data.fetch_toil()
        except Exception as exc:
            self.query_one("#toil-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        self._patterns = patterns

        self.query_one("#toil-title", Static).update(
            "\n[bold #f7f8f8]Toil Detector[/]  [dim]repeated command sequences[/dim]\n"
        )

        total_reps = sum(p["count"] for p in patterns)
        total_wasted = sum(p["wasted_hours"] for p in patterns)

        cards = list(self.query(StatCard))
        if len(cards) >= 3:
            cards[0].update_card(
                value=str(len(patterns)),
                value_color="#d97706" if patterns else "#f7f8f8",
                delta="automated detection",
            )
            cards[1].update_card(value=str(total_reps), delta="all patterns")
            cards[2].update_card(
                value=f"{total_wasted:.1f}h",
                value_color="#e87b5a",
                delta="if not automated",
            )

        table = self.query_one(VimDataTable)
        table.clear()
        if not patterns:
            table.add_row("—", "[dim]No toil patterns detected yet[/dim]", "—", "—")
            return
        for p in patterns:
            label = p["label"]
            if len(label) > 65:
                label = label[:62] + "…"
            table.add_row(
                str(p["id"]),
                label,
                f"×{p['count']}",
                f"~{p['wasted_hours']}h",
            )

    def action_apply_selected(self) -> None:
        if not self._patterns:
            self.app.notify("No patterns to apply", severity="warning")
            return
        table = self.query_one(VimDataTable)
        cursor = table.cursor_row
        if cursor is None or cursor < 0 or cursor >= len(self._patterns):
            self.app.notify("Select a row first (j/k to navigate)", severity="information")
            return
        pattern = self._patterns[cursor]
        pid = pattern["id"]
        self.query_one("#toil-output", Static).update(
            f"  [#5e6ad2]⏳[/#5e6ad2] Generating alias for pattern #{pid} via LLM…\n"
            f"  [dim]Pattern: {pattern['label'][:80]}[/dim]"
        )
        self._run_suggest(pid)

    @work(thread=True, exclusive=True)
    def _run_suggest(self, pattern_id: int) -> None:
        result = tui_data.generate_toil_script(pattern_id)
        self.app.call_from_thread(self._update_suggest_output, pattern_id, result)

    def _update_suggest_output(self, pattern_id: int, result: str) -> None:
        out = self.query_one("#toil-output", Static)
        if not result:
            out.update(f"[red]No output for pattern #{pattern_id}[/red]")
            return
        text = result.strip()
        if len(text) > 1500:
            text = text[:1500] + "\n…(truncated)"
        out.update(
            f"  [#27a644]✓ Generated[/#27a644]  [bold]Suggestion for pattern #{pattern_id}[/bold]\n\n"
            f"  [#fbbf24]{text}[/#fbbf24]"
        )
