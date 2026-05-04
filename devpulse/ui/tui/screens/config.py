"""Config screen — settings toggles, daemon control, save action."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox, Select, Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimVerticalScroll


_PROVIDER_OPTIONS = [("ollama (local)", "ollama"), ("claude", "claude"), ("groq", "groq"), ("openai", "openai"), ("none", "none")]


class ConfigScreen(VimVerticalScroll):
    """Configuration view with toggles, save, daemon controls."""

    DEFAULT_CSS = """
    ConfigScreen { padding: 1 1; }
    ConfigScreen Checkbox {
        background: $surface;
        margin: 0 0 0 1;
    }
    ConfigScreen Select {
        background: $surface;
        width: 30;
    }
    ConfigScreen Button {
        margin: 0 1 0 0;
    }
    ConfigScreen .config-row {
        height: 3;
        margin: 0 0 0 0;
        align: left middle;
    }
    ConfigScreen .config-row Static {
        width: 38;
        padding: 1 0 0 1;
    }
    ConfigScreen .button-row {
        height: 3;
        margin: 1 0 1 0;
    }
    """

    BINDINGS = [
        Binding("s", "save", "Save"),
        Binding("R", "restart_daemon", "Restart"),
        Binding("S", "stop_daemon", "Stop"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("", id="cfg-title")

        yield StatRow(
            StatCard(label="Daemon", value="-"),
            StatCard(label="PID", value="-"),
            StatCard(label="Events today", value="-"),
            StatCard(label="DB size", value="-"),
            id="cfg-stat-row",
        )

        yield Static("LLM PROVIDER", classes="section-bar")
        with Horizontal(classes="config-row"):
            yield Static("Provider")
            yield Select(
                options=_PROVIDER_OPTIONS,
                value="ollama",
                id="cfg-llm-provider",
                allow_blank=False,
            )

        yield Static("COLLECTION", classes="section-bar")
        with Horizontal(classes="config-row"):
            yield Static("Shell hook")
            yield Checkbox(value=True, id="cfg-shell")
        with Horizontal(classes="config-row"):
            yield Static("Git watcher")
            yield Checkbox(value=True, id="cfg-git")
        with Horizontal(classes="config-row"):
            yield Static("Window focus")
            yield Checkbox(value=True, id="cfg-window")
        with Horizontal(classes="config-row"):
            yield Static("File watcher")
            yield Checkbox(value=False, id="cfg-file")

        yield Static("PRIVACY", classes="section-bar")
        with Horizontal(classes="config-row"):
            yield Static("Local-only mode")
            yield Checkbox(value=True, id="cfg-local")

        yield Static("ACTIONS — [s] save, [R] restart daemon, [S] stop daemon", classes="section-bar")
        with Horizontal(classes="button-row"):
            yield Button("Save changes", id="cfg-save", variant="primary")
            yield Button("Restart daemon", id="cfg-restart")
            yield Button("Stop daemon", id="cfg-stop", variant="error")

        yield Static("", id="cfg-feedback", classes="muted")

    async def refresh_data(self) -> None:
        try:
            cfg = tui_data.load_cfg()
            st = tui_data.fetch_status()
        except Exception as exc:
            self.query_one("#cfg-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        self.query_one("#cfg-title", Static).update(
            "\n[bold]Configuration[/bold]  [dim]~/.devpulse/config.toml[/dim]\n"
        )

        cards = list(self.query(StatCard))
        if len(cards) >= 4:
            running = st.get("daemon_running", False)
            cards[0].update_card(
                value="running" if running else "stopped",
                value_color="#27a644" if running else "#e87b5a",
                delta="background process",
            )
            cards[1].update_card(value=str(st.get("pid") or "-"))
            cards[2].update_card(value=str(st.get("total_events", 0)))
            cards[3].update_card(value=st.get("db_size", "-"))

        # Provider
        try:
            self.query_one("#cfg-llm-provider", Select).value = (
                cfg.get("llm", {}).get("provider", "ollama") or "ollama"
            )
        except Exception:
            pass

        collectors = cfg.get("collectors", {})
        try:
            self.query_one("#cfg-shell", Checkbox).value = collectors.get("shell", True) is not False
            self.query_one("#cfg-git", Checkbox).value = collectors.get("git", True) is not False
            self.query_one("#cfg-window", Checkbox).value = collectors.get("window", True) is not False
            self.query_one("#cfg-file", Checkbox).value = collectors.get("file_watcher", False) is True
            self.query_one("#cfg-local", Checkbox).value = (
                cfg.get("general", {}).get("local_only", True) is not False
            )
        except Exception:
            pass

    def action_save(self) -> None:
        try:
            updates = {
                "llm.provider": self.query_one("#cfg-llm-provider", Select).value,
                "collectors.shell": self.query_one("#cfg-shell", Checkbox).value,
                "collectors.git": self.query_one("#cfg-git", Checkbox).value,
                "collectors.window": self.query_one("#cfg-window", Checkbox).value,
                "collectors.file_watcher": self.query_one("#cfg-file", Checkbox).value,
                "general.local_only": self.query_one("#cfg-local", Checkbox).value,
            }
            tui_data.save_cfg(updates)
            self._set_feedback("[#27a644]✓ Configuration saved[/#27a644]")
        except Exception as exc:
            self._set_feedback(f"[red]Save failed: {exc}[/red]")

    def action_restart_daemon(self) -> None:
        self._set_feedback("[dim]⏳ Restarting daemon…[/dim]")
        self._do_restart()

    def action_stop_daemon(self) -> None:
        self._set_feedback("[dim]⏳ Stopping daemon…[/dim]")
        self._do_stop()

    @work(thread=True, exclusive=True)
    def _do_restart(self) -> None:
        msg = tui_data.restart_daemon()
        self.app.call_from_thread(self._after_daemon_action, msg)

    @work(thread=True, exclusive=True)
    def _do_stop(self) -> None:
        msg = tui_data.stop_daemon()
        self.app.call_from_thread(self._after_daemon_action, msg)

    def _after_daemon_action(self, msg: str) -> None:
        self._set_feedback(f"[#5e6ad2]●[/#5e6ad2] {msg}")
        self.call_later(self.refresh_data)

    def _set_feedback(self, text: str) -> None:
        self.query_one("#cfg-feedback", Static).update(text)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cfg-save":
            self.action_save()
        elif bid == "cfg-restart":
            self.action_restart_daemon()
        elif bid == "cfg-stop":
            self.action_stop_daemon()
