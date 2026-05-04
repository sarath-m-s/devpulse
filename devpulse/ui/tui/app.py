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
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Static

from devpulse.ui.tui import data as tui_data


_TAB_ORDER = [
    ("today",    "Today"),
    ("week",     "Week"),
    ("projects", "Projects"),
    ("toil",     "Toil"),
    ("focus",    "Focus"),
    ("insights", "Insights"),
    ("profile",  "Profile"),
    ("config",   "Config"),
]

_VIEW_TITLES = {
    "today":    "Today's Pulse",
    "week":     "This Week",
    "projects": "Projects",
    "toil":     "Toil Detector",
    "focus":    "Focus Analysis",
    "insights": "AI Insights",
    "profile":  "Developer Profile",
    "config":   "Configuration",
}


class HeaderBar(Static):
    """Top bar: logo + current view title on left, daemon + events + time on right."""

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        background: #0d0e11;
        color: #f7f8f8;
        padding: 0 1;
    }
    """


class TabBar(Horizontal):
    """Numbered tab navigation bar directly below the header."""

    DEFAULT_CSS = """
    TabBar {
        dock: top;
        height: 1;
        background: #0d0e11;
        padding: 0 0;
        border-bottom: tall #23252a;
    }
    TabBar > .tab {
        padding: 0 2;
        color: #62666d;
        height: 1;
    }
    TabBar > .tab.active {
        background: #5e6ad2;
        color: #f7f8f8;
        text-style: bold;
    }
    """


class KeyBar(Static):
    """Bottom key-hint bar."""

    DEFAULT_CSS = """
    KeyBar {
        dock: bottom;
        height: 1;
        background: #0d0e11;
        color: #62666d;
        padding: 0 1;
    }
    """

    _HINT = (
        "[dim #5e6ad2][1-8][/] view  "
        "[dim #5e6ad2][tab][/] next  "
        "[dim #5e6ad2][r][/] refresh  "
        "[dim #5e6ad2][j/k][/] scroll  "
        "[dim #5e6ad2][q][/] quit  "
        "[dim #5e6ad2][?][/] help  "
        "[dim #5e6ad2][enter][/] action"
    )

    def __init__(self, **kwargs) -> None:
        super().__init__(self._HINT, **kwargs)


class DevPulseTUI(App):
    """The main DevPulse interactive TUI app."""

    TITLE = "DevPulse"
    SUB_TITLE = "k9s-style dashboard"

    CSS = """
    Screen {
        background: #010102;
        layers: base overlay;
    }

    ContentSwitcher {
        height: 1fr;
        padding: 0 1;
        background: #010102;
    }

    .muted   { color: #8a8f98; }
    .dim     { color: #62666d; }
    .accent  { color: #5e6ad2; }
    .ok      { color: #27a644; }
    .warn    { color: #d97706; }
    .bad     { color: #e87b5a; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("1", "switch_tab('today')",    "Today",    show=False),
        Binding("2", "switch_tab('week')",     "Week",     show=False),
        Binding("3", "switch_tab('projects')", "Projects", show=False),
        Binding("4", "switch_tab('toil')",     "Toil",     show=False),
        Binding("5", "switch_tab('focus')",    "Focus",    show=False),
        Binding("6", "switch_tab('insights')", "Insights", show=False),
        Binding("7", "switch_tab('profile')",  "Profile",  show=False),
        Binding("8", "switch_tab('config')",   "Config",   show=False),
        Binding("tab",       "next_tab", "Next view", show=False),
        Binding("shift+tab", "prev_tab", "Prev view", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("q", "quit",    "Quit",    show=False),
        Binding("ctrl+c", "quit", "Quit",  show=False),
        Binding("?", "help",   "Help",     show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_tab = "today"
        self._screens: dict[str, object] = {}

    def compose(self) -> ComposeResult:
        yield HeaderBar(self._render_header(), id="header-bar")
        yield TabBar(id="tab-bar")
        with ContentSwitcher(initial="today", id="content"):
            from devpulse.ui.tui.screens.today    import TodayScreen
            from devpulse.ui.tui.screens.week     import WeekScreen
            from devpulse.ui.tui.screens.projects import ProjectsScreen
            from devpulse.ui.tui.screens.toil     import ToilScreen
            from devpulse.ui.tui.screens.focus    import FocusScreen
            from devpulse.ui.tui.screens.insights import InsightsScreen
            from devpulse.ui.tui.screens.profile  import ProfileScreen
            from devpulse.ui.tui.screens.config   import ConfigScreen

            screen_classes = {
                "today":    TodayScreen,
                "week":     WeekScreen,
                "projects": ProjectsScreen,
                "toil":     ToilScreen,
                "focus":    FocusScreen,
                "insights": InsightsScreen,
                "profile":  ProfileScreen,
                "config":   ConfigScreen,
            }
            for tab_id, _ in _TAB_ORDER:
                screen = screen_classes[tab_id](id=tab_id)
                self._screens[tab_id] = screen
                yield screen
        yield KeyBar(id="key-bar")

    async def on_mount(self) -> None:
        from devpulse import db
        db.init_db()

        tab_bar = self.query_one("#tab-bar", TabBar)
        for i, (tab_id, label) in enumerate(_TAB_ORDER, 1):
            classes = "tab active" if tab_id == self._current_tab else "tab"
            tab_bar.mount(Static(f"[{i}]{label}", classes=classes, id=f"tab-{tab_id}"))

        self.set_interval(1.0, self._update_header)
        self.set_interval(30.0, self.action_refresh)

        await self._refresh_current_screen()
        self.call_later(self._focus_active_tab)

    def _focus_active_tab(self) -> None:
        try:
            self.query_one(f"#{self._current_tab}").focus(scroll=False)
        except Exception:
            pass

    # ── Header ───────────────────────────────────────────────────────

    def _render_header(self) -> str:
        view_title = _VIEW_TITLES.get(self._current_tab, self._current_tab.title())
        try:
            st = tui_data.fetch_status()
            running = st.get("daemon_running", False)
            events = st.get("total_events", 0)
            db_size = st.get("db_size", "")
            if running:
                status_pill = "[bold #27a644 on #1a2e1e] ● running [/]"
            else:
                status_pill = "[bold #e87b5a on #2e1a1a] ● stopped [/]"
            right = f"{status_pill} [dim]{events} events · {db_size}[/dim]"
        except Exception:
            right = "[dim #d97706]daemon: ?[/dim]"
        now = datetime.now().strftime("%a %b %-d  %H:%M:%S")
        left = f"[bold #5e6ad2]⬡ DevPulse[/] [dim #23252a]─[/] [bold #f7f8f8]{view_title}[/]"
        return f"{left}  {right}  [dim]{now}[/dim]"

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
        self._current_tab = tab_id
        try:
            self.query_one("#content", ContentSwitcher).current = tab_id
        except Exception:
            pass
        try:
            tab_bar = self.query_one("#tab-bar", TabBar)
            for tid, _ in _TAB_ORDER:
                w = tab_bar.query_one(f"#tab-{tid}", Static)
                w.set_classes("tab active" if tid == tab_id else "tab")
        except Exception:
            pass
        self._update_header()
        self.call_later(self._refresh_current_screen)
        self.call_later(self._focus_active_tab)

    async def _refresh_current_screen(self) -> None:
        screen = self._screens.get(self._current_tab)
        if screen and hasattr(screen, "refresh_data"):
            try:
                await screen.refresh_data()
            except Exception as exc:
                self.notify(f"Refresh error: {exc}", severity="error", timeout=4)

    # ── Actions ──────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self.call_later(self._refresh_current_screen)
        self._update_header()

    def action_help(self) -> None:
        self.notify(
            "Keys: [1-8] switch view  [tab/shift+tab] cycle  [j/k] scroll  "
            "[r] refresh  [enter/a] action  [s] save  [R] re-run  [q] quit",
            timeout=7,
        )
