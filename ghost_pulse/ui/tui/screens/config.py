"""Config screen — settings toggles, daemon control, save action."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Select, Static, Switch

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import Panel, StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimVerticalScroll


_PROVIDER_OPTIONS = [
    ("ollama  (local, free)", "ollama"),
    ("claude  (Anthropic)",   "claude"),
    ("groq    (fast, free tier)", "groq"),
    ("openai  (GPT-4o)",      "openai"),
    ("none    (disable LLM)", "none"),
]


class ConfigScreen(VimVerticalScroll):
    """Configuration view with toggles, save, daemon controls."""

    DEFAULT_CSS = """
    ConfigScreen {
        padding: 1 1;
        background: #010102;
    }

    /* Provider select */
    ConfigScreen Select {
        background: #0d0e11;
        border: round #23252a;
        width: 36;
        height: 3;
    }
    ConfigScreen Select:focus {
        border: round #5e6ad2;
    }

    /* Toggle rows */
    ConfigScreen .toggle-row {
        height: 4;
        align: left middle;
        padding: 0 0 0 1;
    }
    ConfigScreen .toggle-label {
        width: 24;
        color: #8a8f98;
        padding: 1 0 0 0;
    }
    ConfigScreen .toggle-desc {
        color: #62666d;
        padding: 1 0 0 2;
        width: 1fr;
    }

    /* Switch widget styling */
    ConfigScreen Switch {
        height: 3;
        width: 8;
        background: transparent;
        border: none;
        padding: 1 0 0 0;
    }
    ConfigScreen Switch.-on .switch--slider {
        color: #27a644;
    }

    /* Buttons */
    ConfigScreen Button {
        background: #0d0e11;
        border: round #23252a;
        color: #8a8f98;
        margin: 0 1 0 0;
        height: 3;
    }
    ConfigScreen Button.-primary {
        background: #1b1c21;
        border: round #5e6ad2;
        color: #5e6ad2;
    }
    ConfigScreen Button.-error {
        border: round #e87b5a;
        color: #e87b5a;
    }
    ConfigScreen Button:hover {
        border: round #5e6ad2;
        color: #f7f8f8;
    }
    ConfigScreen .button-row {
        height: 3;
        margin: 1 0 1 0;
    }
    ConfigScreen .provider-row {
        height: 4;
        align: left middle;
        padding: 0 0 0 1;
    }
    ConfigScreen .provider-label {
        width: 24;
        color: #8a8f98;
        padding: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("s", "save",           "Save"),
        Binding("R", "restart_daemon", "Restart"),
        Binding("S", "stop_daemon",    "Stop"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("", id="cfg-title")
        yield StatRow(
            StatCard(label="Daemon",       value="-"),
            StatCard(label="PID",          value="-"),
            StatCard(label="Events today", value="-"),
            StatCard(label="DB size",      value="-"),
            id="cfg-stat-row",
        )

        with Panel("LLM PROVIDER"):
            with Horizontal(classes="provider-row"):
                yield Static("Provider", classes="provider-label")
                yield Select(
                    options=_PROVIDER_OPTIONS,
                    value="ollama",
                    id="cfg-llm-provider",
                    allow_blank=False,
                )

        with Panel("DATA COLLECTION"):
            with Horizontal(classes="toggle-row"):
                yield Static("Shell history", classes="toggle-label")
                yield Switch(value=True, id="cfg-shell", animate=False)
                yield Static("Track every command you run", classes="toggle-desc")
            with Horizontal(classes="toggle-row"):
                yield Static("Git watcher", classes="toggle-label")
                yield Switch(value=True, id="cfg-git", animate=False)
                yield Static("Commits, branch switches, diffs", classes="toggle-desc")
            with Horizontal(classes="toggle-row"):
                yield Static("Window focus", classes="toggle-label")
                yield Switch(value=True, id="cfg-window", animate=False)
                yield Static("App / browser tab switches", classes="toggle-desc")
            with Horizontal(classes="toggle-row"):
                yield Static("File watcher", classes="toggle-label")
                yield Switch(value=False, id="cfg-file", animate=False)
                yield Static("File-save events (high volume)", classes="toggle-desc")

        with Panel("PRIVACY"):
            with Horizontal(classes="toggle-row"):
                yield Static("Local-only mode", classes="toggle-label")
                yield Switch(value=True, id="cfg-local", animate=False)
                yield Static("No data leaves your machine", classes="toggle-desc")

        with Panel("DAEMON — [s] save  [R] restart  [S] stop"):
            with Horizontal(classes="button-row"):
                yield Button("Save changes",   id="cfg-save",    variant="primary")
                yield Button("Restart daemon", id="cfg-restart")
                yield Button("Stop daemon",    id="cfg-stop",    variant="error")
            yield Static("", id="cfg-feedback", classes="muted")

    async def refresh_data(self) -> None:
        try:
            cfg = tui_data.load_cfg()
            st  = tui_data.fetch_status()
        except Exception as exc:
            self.query_one("#cfg-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        self.query_one("#cfg-title", Static).update(
            "\n[bold #f7f8f8]Configuration[/]  [dim]~/.devpulse/config.toml[/dim]\n"
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

        # Provider — set value then force a refresh so it renders in the closed state
        provider_val = cfg.get("llm", {}).get("provider", "ollama") or "ollama"
        try:
            sel = self.query_one("#cfg-llm-provider", Select)
            sel.value = provider_val
            sel.refresh()
        except Exception:
            pass

        # Toggles — config uses "collectors" key
        collectors = cfg.get("collectors", cfg.get("collection", {}))
        try:
            self.query_one("#cfg-shell",  Switch).value = bool(collectors.get("shell", True))
            self.query_one("#cfg-git",    Switch).value = bool(collectors.get("git", True))
            self.query_one("#cfg-window", Switch).value = bool(
                collectors.get("window_tracker", collectors.get("window", True))
            )
            self.query_one("#cfg-file",   Switch).value = bool(collectors.get("file_watcher", False))
            self.query_one("#cfg-local",  Switch).value = bool(
                cfg.get("general", {}).get("local_only", True)
            )
        except Exception:
            pass

    def action_save(self) -> None:
        try:
            updates = {
                "llm.provider":              self.query_one("#cfg-llm-provider", Select).value,
                "collectors.shell":          self.query_one("#cfg-shell",  Switch).value,
                "collectors.git":            self.query_one("#cfg-git",    Switch).value,
                "collectors.window_tracker": self.query_one("#cfg-window", Switch).value,
                "collectors.file_watcher":   self.query_one("#cfg-file",   Switch).value,
                "general.local_only":        self.query_one("#cfg-local",  Switch).value,
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
        try:
            self.query_one("#cfg-feedback", Static).update(text)
        except Exception:
            pass

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "cfg-save":
            self.action_save()
        elif bid == "cfg-restart":
            self.action_restart_daemon()
        elif bid == "cfg-stop":
            self.action_stop_daemon()
