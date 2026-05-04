"""Toil screen — pattern detection with inline LLM suggest + save action."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, DataTable, Static

from ghost_pulse.ui.tui import data as tui_data
from ghost_pulse.ui.tui.widgets import Panel, StatCard, StatRow
from ghost_pulse.ui.tui.vim_scroll import VimDataTable, VimVerticalScroll


class ToilScreen(VimVerticalScroll):
    """Toil detector view — select a pattern and generate + save an alias."""

    DEFAULT_CSS = """
    ToilScreen {
        padding: 1 1;
        background: #010102;
    }
    ToilScreen VimDataTable {
        height: auto;
        max-height: 14;
        background: #0d0e11;
    }
    ToilScreen .save-row {
        height: 3;
        margin: 1 0 0 0;
        display: none;
    }
    ToilScreen .save-row.visible {
        display: block;
    }
    ToilScreen Button {
        background: #0d0e11;
        border: round #23252a;
        color: #8a8f98;
        margin: 0 1 0 0;
        min-width: 18;
        height: 3;
    }
    ToilScreen Button.-primary {
        border: round #5e6ad2;
        color: #5e6ad2;
    }
    ToilScreen Button.-success {
        border: round #27a644;
        color: #27a644;
    }
    ToilScreen Button:hover {
        border: round #5e6ad2;
        color: #f7f8f8;
    }
    ToilScreen .code-block {
        background: #0d0e11;
        border: round #23252a;
        padding: 0 2;
        color: #fbbf24;
        height: auto;
        min-height: 3;
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("a",   "generate_selected", "Generate alias", show=False),
        Binding("z",   "save_zshrc",        "→ ~/.zshrc",    show=False),
        Binding("A",   "save_aliases",      "→ ~/.aliases",  show=False),
        Binding("s",   "save_scripts",      "→ scripts dir", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._patterns: list[dict] = []
        self._generated_code: str = ""
        self._generated_pattern_id: int | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="toil-title")
        yield StatRow(
            StatCard(label="Patterns found",    value="-"),
            StatCard(label="Total repetitions", value="-"),
            StatCard(label="Est. time wasted",  value="-", accent=True),
            id="toil-stat-row",
        )
        with Panel("DETECTED PATTERNS — j/k navigate  ↵/a generate alias"):
            yield VimDataTable(id="toil-table", zebra_stripes=True, cursor_type="row")
        with Panel("GENERATED ALIAS"):
            yield Static(
                "[dim]Select a pattern above and press [bold]Enter[/bold] or [bold]a[/bold] "
                "to generate an alias via LLM[/dim]",
                id="toil-output",
                classes="muted",
            )
            yield Static("", id="toil-code", classes="code-block")
            with Horizontal(classes="save-row", id="toil-save-row"):
                yield Button("z  Add to ~/.zshrc",   id="btn-zshrc",   variant="primary")
                yield Button("A  Add to ~/.aliases",  id="btn-aliases")
                yield Button("s  Save to scripts/",   id="btn-scripts")
            yield Static("", id="toil-save-feedback", classes="muted")

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

    # ── Enter key on DataTable row triggers generation ────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """DataTable fires this when user presses Enter on a highlighted row."""
        self.action_generate_selected()

    def action_generate_selected(self) -> None:
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
        self._generated_pattern_id = pid
        self._generated_code = ""
        self._hide_save_buttons()
        self.query_one("#toil-output", Static).update(
            f"  [#5e6ad2]⏳[/#5e6ad2] Asking LLM to generate alias for pattern #{pid}…\n"
            f"  [dim]{pattern['label'][:80]}[/dim]"
        )
        self.query_one("#toil-code", Static).update("")
        self.query_one("#toil-save-feedback", Static).update("")
        self._run_suggest(pid)

    @work(thread=True, exclusive=True)
    def _run_suggest(self, pattern_id: int) -> None:
        result = tui_data.generate_toil_script(pattern_id)
        self.app.call_from_thread(self._show_generated, result)

    def _show_generated(self, result: dict) -> None:
        if "error" in result:
            self.query_one("#toil-output", Static).update(
                f"[red]Error: {result['error']}[/red]"
            )
            return

        code = result.get("code", "")
        name = result.get("name")
        pid = result.get("pattern_id")
        self._generated_code = code
        self._generated_pattern_id = pid

        name_str = f"  [dim]alias name: [bold #fbbf24]{name}[/bold #fbbf24][/dim]\n" if name else ""
        self.query_one("#toil-output", Static).update(
            f"  [#27a644]✓ Generated[/#27a644]  [bold]Pattern #{pid}[/bold]\n"
            f"{name_str}"
            f"  [dim]Save with:[/dim] "
            f"[#5e6ad2]z[/#5e6ad2] ~/.zshrc  "
            f"[#5e6ad2]A[/#5e6ad2] ~/.aliases  "
            f"[#5e6ad2]s[/#5e6ad2] scripts/"
        )
        self.query_one("#toil-code", Static).update(f"  {code}")
        self._show_save_buttons()

    def _show_save_buttons(self) -> None:
        try:
            row = self.query_one("#toil-save-row")
            row.add_class("visible")
        except Exception:
            pass

    def _hide_save_buttons(self) -> None:
        try:
            row = self.query_one("#toil-save-row")
            row.remove_class("visible")
        except Exception:
            pass

    # ── Save actions ─────────────────────────────────────────────────

    def action_save_zshrc(self) -> None:
        self._do_save("zshrc")

    def action_save_aliases(self) -> None:
        self._do_save("aliases")

    def action_save_scripts(self) -> None:
        self._do_save("scripts")

    def _do_save(self, destination: str) -> None:
        if not self._generated_code:
            self.app.notify("No generated alias yet — press Enter or a first", severity="warning")
            return
        self.query_one("#toil-save-feedback", Static).update(
            f"  [dim]⏳ Saving to {destination}…[/dim]"
        )
        self._run_save(self._generated_code, destination, self._generated_pattern_id)

    @work(thread=True, exclusive=True)
    def _run_save(self, code: str, destination: str, pattern_id: int | None) -> None:
        msg = tui_data.save_toil_script(code, destination, pattern_id)
        self.app.call_from_thread(self._after_save, msg)

    def _after_save(self, msg: str) -> None:
        color = "#27a644" if msg.startswith("✓") else "#e87b5a"
        self.query_one("#toil-save-feedback", Static).update(
            f"  [{color}]{msg}[/{color}]"
        )

    # ── Button clicks ────────────────────────────────────────────────

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "btn-zshrc":
            self.action_save_zshrc()
        elif bid == "btn-aliases":
            self.action_save_aliases()
        elif bid == "btn-scripts":
            self.action_save_scripts()
