"""DevPulse TUI — k9s-inspired interactive terminal dashboard.

Launched via `devpulse tui`. Provides 8 navigable screens covering every
DevPulse feature (Today, Week, Projects, Toil, Focus, Insights, Profile,
Config) with vim-style keybindings and live data refresh.
"""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import ContentSwitcher, Footer, Static

from devpulse.ui.tui import data as tui_data


_TAB_ORDER = [
    ("today", "Today"),
    ("week", "Week"),
    ("projects", "Projects"),
    ("toil", "Toil"),
    ("focus", "Focus"),
    ("insights", "Insights"),
    ("profile", "Profile"),
    ("config", "Config"),
]


class HeaderBar(Static):
    """Top bar: logo, daemon status, event count, time."""

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """


class TabBar(Horizontal):
    """The numbered tab navigation bar below the header."""

    DEFAULT_CSS = """
    TabBar {
        dock: top;
        height: 1;
        background: $surface;
        padding: 0 1;
    }
    TabBar > .tab {
        padding: 0 1;
        color: $text-muted;
        margin: 0 1 0 0;
    }
    TabBar > .tab.active {
        color: $primary;
        text-style: bold underline;
    }
    """


class DevPulseTUI(App):
    """The main DevPulse interactive TUI app."""

    TITLE = "DevPulse"
    SUB_TITLE = "k9s-style dashboard"

    CSS = """
    Screen {
        background: $background;
    }

    .panel-title {
        color: $text;
        text-style: bold;
        padding: 0 1;
        background: $boost;
    }

    .section-bar {
        color: $primary;
        text-style: bold;
        padding: 0 1;
        background: $boost;
        margin: 1 0 0 0;
    }

    .muted { color: $text-muted; }
    .dim { color: $text-disabled; }
    .accent { color: $primary; }
    .ok { color: $success; }
    .warn { color: $warning; }
    .bad { color: $error; }

    ContentSwitcher {
        height: 1fr;
        padding: 0 1;
    }

    .scroll-content {
        height: auto;
        padding: 0 0 1 0;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "switch_tab('today')", "Today", show=False),
        Binding("2", "switch_tab('week')", "Week", show=False),
        Binding("3", "switch_tab('projects')", "Projects", show=False),
        Binding("4", "switch_tab('toil')", "Toil", show=False),
        Binding("5", "switch_tab('focus')", "Focus", show=False),
        Binding("6", "switch_tab('insights')", "Insights", show=False),
        Binding("7", "switch_tab('profile')", "Profile", show=False),
        Binding("8", "switch_tab('config')", "Config", show=False),
        Binding("tab", "next_tab", "Next view", show=False),
        Binding("shift+tab", "prev_tab", "Prev view", show=False),
        Binding("r", "refresh", "Refresh"),
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("?", "help", "Help"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_tab = "today"
        self._refresh_timer = None
        self._screens: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield HeaderBar(self._render_header(), id="header-bar")
        yield TabBar(id="tab-bar")
        with ContentSwitcher(initial="today", id="content"):
            from devpulse.ui.tui.screens.today import TodayScreen
            from devpulse.ui.tui.screens.week import WeekScreen
            from devpulse.ui.tui.screens.projects import ProjectsScreen
            from devpulse.ui.tui.screens.toil import ToilScreen
            from devpulse.ui.tui.screens.focus import FocusScreen
            from devpulse.ui.tui.screens.insights import InsightsScreen
            from devpulse.ui.tui.screens.profile import ProfileScreen
            from devpulse.ui.tui.screens.config import ConfigScreen

            screen_classes = {
                "today": TodayScreen,
                "week": WeekScreen,
                "projects": ProjectsScreen,
                "toil": ToilScreen,
                "focus": FocusScreen,
                "insights": InsightsScreen,
                "profile": ProfileScreen,
                "config": ConfigScreen,
            }
            for tab_id, _ in _TAB_ORDER:
                screen = screen_classes[tab_id](id=tab_id)
                self._screens[tab_id] = screen
                yield screen
        yield Footer()

    async def on_mount(self) -> None:
        """Initialise after mount: build tab bar, set up auto-refresh."""
        from devpulse import db
        db.init_db()

        tab_bar = self.query_one("#tab-bar", TabBar)
        for i, (tab_id, label) in enumerate(_TAB_ORDER, 1):
            classes = "tab active" if tab_id == self._current_tab else "tab"
            tab_bar.mount(Static(f"[{i}]{label}", classes=classes, id=f"tab-{tab_id}"))

        # Refresh header every second, current screen data every 30s
        self.set_interval(1.0, self._update_header)
        self._refresh_timer = self.set_interval(30.0, self.action_refresh)

        # Initial data load for the active screen
        await self._refresh_current_screen()
        self.call_later(self._focus_active_tab)

    def _focus_active_tab(self) -> None:
        """Give the current tab's root panel focus so j/k reach VimVerticalScroll bindings."""
        try:
            self.query_one(f"#{self._current_tab}").focus(scroll=False)
        except Exception:
            pass

    # ── Header ───────────────────────────────────────────────────────

    def _render_header(self) -> str:
        try:
            st = tui_data.fetch_status()
            running = st.get("daemon_running", False)
            events = st.get("total_events", 0)
            db_size = st.get("db_size", "")
            status_str = "[green]daemon: running[/green]" if running else "[red]daemon: stopped[/red]"
        except Exception:
            status_str = "[yellow]daemon: ?[/yellow]"
            events = 0
            db_size = ""
        now = datetime.now().strftime("%a %b %-d %H:%M:%S")
        return (
            f" [bold #5e6ad2]●[/bold #5e6ad2] [bold]DevPulse[/bold]  "
            f"{status_str}  "
            f"[dim]{events} events today · {db_size}[/dim]"
            f"  [dim]· {now}[/dim]"
        )

    def _update_header(self) -> None:
        try:
            self.query_one("#header-bar", HeaderBar).update(self._render_header())
        except Exception:
            pass

    # ── Tab switching ────────────────────────────────────────────────

    def action_switch_tab(self, tab_id: str) -> None:
        if tab_id not in {t for t, _ in _TAB_ORDER}:
            return
        self._set_active_tab(tab_id)

    def action_next_tab(self) -> None:
        ids = [t for t, _ in _TAB_ORDER]
        try:
            idx = ids.index(self._current_tab)
        except ValueError:
            idx = 0
        self._set_active_tab(ids[(idx + 1) % len(ids)])

    def action_prev_tab(self) -> None:
        ids = [t for t, _ in _TAB_ORDER]
        try:
            idx = ids.index(self._current_tab)
        except ValueError:
            idx = 0
        self._set_active_tab(ids[(idx - 1) % len(ids)])

    def _set_active_tab(self, tab_id: str) -> None:
        prev = self._current_tab
        self._current_tab = tab_id
        try:
            switcher = self.query_one("#content", ContentSwitcher)
            switcher.current = tab_id
        except Exception:
            pass
        # Update tab bar styles
        try:
            tab_bar = self.query_one("#tab-bar", TabBar)
            for tid, _ in _TAB_ORDER:
                w = tab_bar.query_one(f"#tab-{tid}", Static)
                w.set_classes("tab active" if tid == tab_id else "tab")
        except Exception:
            pass
        # Refresh data on the new screen
        self.call_later(self._refresh_current_screen)
        self.call_later(self._focus_active_tab)

    async def _refresh_current_screen(self) -> None:
        screen = self._screens.get(self._current_tab)
        if screen and hasattr(screen, "refresh_data"):
            try:
                await screen.refresh_data()
            except Exception as exc:
                self.notify(f"Refresh error: {exc}", severity="error", timeout=4)

    # ── Refresh ──────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self.call_later(self._refresh_current_screen)
        self._update_header()

    def action_help(self) -> None:
        self.notify(
            "Keys: 1-8 view, j/k scroll, r refresh, / filter, q quit, "
            "Enter action, s save, R re-run, i input",
            timeout=6,
        )
