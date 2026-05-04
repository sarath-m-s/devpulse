"""Fix knowledge base — RAG status, open windows, history, command lookup."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Input, Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import Panel, StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimDataTable, VimVerticalScroll


class FixesScreen(VimVerticalScroll):
    """Error → fix knowledge: stats, open windows, saved fixes, suggest search."""

    DEFAULT_CSS = """
    FixesScreen {
        padding: 1 1;
        background: #010102;
    }
    FixesScreen Input {
        background: #0d0e11;
        border: round #23252a;
        height: 3;
        margin: 0 0 1 0;
    }
    FixesScreen Input:focus {
        border: round #5e6ad2;
    }
    FixesScreen VimDataTable {
        height: auto;
        max-height: 12;
        background: #0d0e11;
    }
    """

    BINDINGS = [
        Binding("i", "focus_suggest", "Search"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("", id="fix-title")
        yield StatRow(
            StatCard(label="Embeddings", value="-", accent=True),
            StatCard(label="Fix records", value="-"),
            StatCard(label="Vectorized", value="-"),
            StatCard(label="Open windows", value="-"),
            id="fix-stat-row",
        )
        with Panel("OPEN FIX WINDOWS — after a failed command; close with: devpulse fix-done"):
            yield Static(
                "[dim]No open windows. Run a failing command in a git repo to open one.[/dim]",
                id="fix-open",
            )
        with Panel("SAVED FIXES — j/k scroll"):
            yield VimDataTable(
                id="fix-history",
                zebra_stripes=True,
                cursor_type="row",
            )
        with Panel("LOOK UP A FIX — type a failing command, Enter  [i] focus"):
            yield Input(placeholder='e.g. pytest tests/  or  docker build -t myapp .', id="fix-suggest-in")
            yield Static(
                "[dim]Searches exact → fuzzy → semantic (if embeddings available).[/dim]",
                id="fix-suggest-hint",
            )
        with Panel("SUGGESTIONS"):
            yield Static(
                "[dim]Results appear here.[/dim]",
                id="fix-suggest-out",
            )

    def on_mount(self) -> None:
        table = self.query_one("#fix-history", DataTable)
        table.add_column("id", key="id", width=4)
        table.add_column("pattern", key="pat", width=32)
        table.add_column("summary", key="sum", width=28)
        table.add_column("project", key="proj", width=14)
        table.add_column("src", key="src", width=6)

    async def refresh_data(self) -> None:
        self._load_overview()
        self._load_history()

    @work(thread=True, exclusive=True)
    def _load_overview(self) -> None:
        data = tui_data.fetch_fix_overview()
        self.app.call_from_thread(self._apply_overview, data)

    def _apply_overview(self, data: dict) -> None:
        self.query_one("#fix-title", Static).update(
            "\n[bold #f7f8f8]Error fix knowledge base[/]  [dim]RAG + shell history[/dim]\n"
        )
        cards = list(self.query(StatCard))
        if len(cards) >= 4:
            en = data.get("embedding_name", "none")
            ok = data.get("embedding_ok", False)
            cards[0].update_card(
                value=en,
                value_color="#27a644" if ok else "#e87b5a",
                delta="ready" if ok else "install ollama or pip install devpulse[embeddings]",
            )
            cards[1].update_card(
                value=str(data.get("fix_records_total", 0)),
                delta="in KB",
            )
            we = data.get("fix_records_with_embedding", 0)
            cards[2].update_card(
                value=str(we),
                delta="with vectors",
            )
            n_open = data.get("windows_open", 0)
            cards[3].update_card(
                value=str(n_open),
                value_color="#d97706" if n_open else "#8a8f98",
                delta="need fix-done" if n_open else "none",
            )

        lines: list[str] = []
        wins = data.get("open_windows") or []
        if not wins:
            lines.append("[dim]No open fix windows.[/dim]")
        else:
            for w in wins:
                pid = w.get("id", "")
                pat = (w.get("pattern") or "?").replace("\n", " ")[:70]
                proj = w.get("project") or "—"
                nc = w.get("commands_count", 0)
                lines.append(
                    f"  [#d97706]#{pid}[/#d97706] [yellow]{pat}[/yellow]\n"
                    f"      [dim]{proj} · {nc} cmds logged[/dim]"
                )
        self.query_one("#fix-open", Static).update("\n".join(lines) if lines else "[dim]—[/dim]")

    @work(thread=True, exclusive=True)
    def _load_history(self) -> None:
        rows = tui_data.fetch_fix_history(limit=40)
        self.app.call_from_thread(self._fill_history, rows)

    def _fill_history(self, rows: list[dict]) -> None:
        table = self.query_one("#fix-history", DataTable)
        table.clear()
        for r in rows:
            pat = (r.get("error_pattern") or "")[:60]
            sm = (r.get("fix_summary") or "—")[:40]
            table.add_row(
                str(r.get("id", "")),
                pat,
                sm,
                (r.get("project") or "—")[:14],
                (r.get("source") or "")[:6],
            )

    def action_focus_suggest(self) -> None:
        self.query_one("#fix-suggest-in", Input).focus()

    @work(thread=True, exclusive=True)
    def _run_suggest(self, cmd: str, exit_code: int) -> None:
        try:
            sugg = tui_data.run_fix_suggest(cmd, exit_code=exit_code, top_k=5)
        except Exception as exc:
            self.app.call_from_thread(self._set_suggest_error, str(exc))
            return
        self.app.call_from_thread(self._set_suggest_result, cmd, sugg)

    def _set_suggest_error(self, msg: str) -> None:
        self.query_one("#fix-suggest-out", Static).update(f"[red]{msg}[/red]")

    def _set_suggest_result(self, cmd: str, sugg: list[dict]) -> None:
        out = self.query_one("#fix-suggest-out", Static)
        if not sugg:
            out.update(
                f"[dim]No matches for:[/dim] [yellow]{cmd[:70]}[/yellow]\n"
                "[dim]Record fixes with `devpulse fix-done` after you solve an error.[/dim]"
            )
            return
        tier_sym = {"exact": "🎯", "fuzzy": "🔍", "semantic": "🧠"}
        parts: list[str] = [f"[bold]Query[/bold] [yellow]{cmd[:65]}[/yellow]\n"]
        for i, s in enumerate(sugg, 1):
            tier = s.get("tier", "")
            sym = tier_sym.get(tier, "•")
            sc = int(float(s.get("score", 0)) * 100)
            parts.append(f"  {i}. {sym} [dim]{tier} {sc}%[/dim]")
            if s.get("fix_summary"):
                parts.append(f"     [green]{s['fix_summary']}[/green]")
            if s.get("fix_commands"):
                fc = " → ".join(s["fix_commands"][:4])
                parts.append(f"     [cyan]{fc}[/cyan]")
            if s.get("project"):
                parts.append(f"     [dim]project {s['project']}[/dim]")
            parts.append("")
        out.update("\n".join(parts).strip())

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "fix-suggest-in":
            return
        raw = event.value.strip()
        if not raw:
            return
        exit_code = 1
        cmd = raw
        # Allow "127 cmd" prefix for exit code
        parts = raw.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) <= 3:
            exit_code = int(parts[0])
            cmd = parts[1].strip()
        self.query_one("#fix-suggest-out", Static).update("[dim]Searching…[/dim]")
        self._run_suggest(cmd, exit_code)
