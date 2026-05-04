"""Ghost Pulse TUI — k9s-inspired interactive terminal dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Static

from ghost_pulse.ui.tui import data as tui_data


_TAB_ORDER = [
    ("today",    "Today",    "Today's Pulse"),
    ("week",     "Week",     "This Week"),
    ("projects", "Projects", "Projects"),
    ("toil",     "Toil",     "Toil Detector"),
    ("fixes",    "Fixes",    "Error fix KB"),
    ("focus",    "Focus",    "Focus Analysis"),
    ("insights", "Insights", "AI Insights"),
    ("profile",  "Profile",  "Developer Profile"),
    ("config",   "Config",   "Configuration"),
]

# id → (short_label, full_title)
_TAB_META = {tid: (lbl, title) for tid, lbl, title in _TAB_ORDER}
_TAB_IDS  = [tid for tid, _, _ in _TAB_ORDER]


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
    }
    TabBar > .tab {
        padding: 0 1;
        color: #62666d;
        height: 1;
        min-width: 10;
    }
    TabBar > .tab.active {
        background: #5e6ad2;
        color: #ffffff;
        text-style: bold;
    }
    TabBar > .tab-sep {
        color: #23252a;
        width: 1;
        height: 1;
    }
    """


class KeyBar(Static):
    """Bottom key-hint bar — shows current view + all keybindings."""

    DEFAULT_CSS = """
    KeyBar {
        dock: bottom;
        height: 1;
        background: #0d0e11;
        color: #62666d;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__("", **kwargs)

    def render_hint(self, tab_num: int, tab_title: str) -> str:
        return (
            f"[bold #5e6ad2]#{tab_num}[/] [#8a8f98]{tab_title}[/]  "
            f"[dim #23252a]│[/]  "
            f"[#5e6ad2]1-9[/#5e6ad2] screens  "
            f"[#5e6ad2]tab[/#5e6ad2] next  "
            f"[#5e6ad2]r[/#5e6ad2] refresh  "
            f"[#5e6ad2]j/k[/#5e6ad2] scroll  "
            f"[#5e6ad2]q[/#5e6ad2] quit  "
            f"[#5e6ad2]?[/#5e6ad2] help"
        )

    def set_view(self, tab_num: int, tab_title: str) -> None:
        self.update(self.render_hint(tab_num, tab_title))


class GhostPulseTUI(App):
    """The main Ghost Pulse interactive TUI app."""

    TITLE = "Ghost Pulse"
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
        Binding("5", "switch_tab('fixes')",    "Fixes",    show=False),
        Binding("6", "switch_tab('focus')",    "Focus",    show=False),
        Binding("7", "switch_tab('insights')", "Insights", show=False),
        Binding("8", "switch_tab('profile')",  "Profile",  show=False),
        Binding("9", "switch_tab('config')",   "Config",   show=False),
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
            from ghost_pulse.ui.tui.screens.today    import TodayScreen
            from ghost_pulse.ui.tui.screens.week     import WeekScreen
            from ghost_pulse.ui.tui.screens.projects import ProjectsScreen
            from ghost_pulse.ui.tui.screens.toil     import ToilScreen
            from ghost_pulse.ui.tui.screens.fixes    import FixesScreen
            from ghost_pulse.ui.tui.screens.focus    import FocusScreen
            from ghost_pulse.ui.tui.screens.insights import InsightsScreen
            from ghost_pulse.ui.tui.screens.profile  import ProfileScreen
            from ghost_pulse.ui.tui.screens.config   import ConfigScreen

            screen_classes = {
                "today":    TodayScreen,
                "week":     WeekScreen,
                "projects": ProjectsScreen,
                "toil":     ToilScreen,
                "fixes":    FixesScreen,
                "focus":    FocusScreen,
                "insights": InsightsScreen,
                "profile":  ProfileScreen,
                "config":   ConfigScreen,
            }
            for tab_id, _, _ in _TAB_ORDER:
                screen = screen_classes[tab_id](id=tab_id)
                self._screens[tab_id] = screen
                yield screen
        yield KeyBar(id="key-bar")

    async def on_mount(self) -> None:
        from ghost_pulse import db
        db.init_db()

        tab_bar = self.query_one("#tab-bar", TabBar)
        for i, (tab_id, label, _) in enumerate(_TAB_ORDER, 1):
            is_active = tab_id == self._current_tab
            classes = "tab active" if is_active else "tab"
            tab_bar.mount(Static(f" {i} {label} ", classes=classes, id=f"tab-{tab_id}"))
            if i < len(_TAB_ORDER):
                tab_bar.mount(Static("│", classes="tab-sep"))

        self._update_key_bar()
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
        meta = _TAB_META.get(self._current_tab)
        if meta:
            tab_label, view_title = meta
        else:
            tab_label, view_title = "?", self._current_tab.title()

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
        left = f"[bold #5e6ad2]⬡ Ghost Pulse[/] [dim #23252a]─[/] [bold #f7f8f8]{view_title}[/]"
        return f"{left}  {right}  [dim]{now}[/dim]"

    def _update_header(self) -> None:
        try:
            self.query_one("#header-bar", HeaderBar).update(self._render_header())
        except Exception:
            pass

    def _update_key_bar(self) -> None:
        try:
            tab_num = _TAB_IDS.index(self._current_tab) + 1
        except ValueError:
            tab_num = 1
        meta = _TAB_META.get(self._current_tab)
        tab_title = meta[1] if meta else self._current_tab.title()
        try:
            self.query_one("#key-bar", KeyBar).set_view(tab_num, tab_title)
        except Exception:
            pass

    # ── Tab switching ────────────────────────────────────────────────

    def action_switch_tab(self, tab_id: str) -> None:
        if tab_id not in set(_TAB_IDS):
            return
        self._set_active_tab(tab_id)

    def action_next_tab(self) -> None:
        try:
            idx = _TAB_IDS.index(self._current_tab)
        except ValueError:
            idx = 0
        self._set_active_tab(_TAB_IDS[(idx + 1) % len(_TAB_IDS)])

    def action_prev_tab(self) -> None:
        try:
            idx = _TAB_IDS.index(self._current_tab)
        except ValueError:
            idx = 0
        self._set_active_tab(_TAB_IDS[(idx - 1) % len(_TAB_IDS)])

    def _set_active_tab(self, tab_id: str) -> None:
        self._current_tab = tab_id
        try:
            self.query_one("#content", ContentSwitcher).current = tab_id
        except Exception:
            pass
        try:
            tab_bar = self.query_one("#tab-bar", TabBar)
            for tid, _, _ in _TAB_ORDER:
                w = tab_bar.query_one(f"#tab-{tid}", Static)
                w.set_classes("tab active" if tid == tab_id else "tab")
        except Exception:
            pass
        self._update_header()
        self._update_key_bar()
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
        lines = [
            "1 Today  2 Week  3 Projects  4 Toil  5 Fixes  6 Focus  7 AI  8 Profile  9 Config",
            "tab / shift+tab  cycle screens",
            "j / k  scroll up/down    r  refresh    q  quit",
            "Toil: Enter / a  generate alias    s/z/a  save alias",
            "Fixes: i  focus search box    Enter  run lookup",
            "Insights: i  ask question    R  re-run",
        ]
        self.notify("\n".join(lines), timeout=8)
