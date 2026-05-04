"""Projects screen — sortable table, top stat cards, branch activity."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimDataTable, VimVerticalScroll


_COLORS = ["#5e6ad2", "#fbbf24", "#4ade80", "#f472b6", "#22d3ee", "#fb923c", "#a78bfa", "#34d399"]


class ProjectsScreen(VimVerticalScroll):
    """Projects view."""

    DEFAULT_CSS = """
    ProjectsScreen { padding: 1 1; }
    ProjectsScreen VimDataTable {
        height: auto;
        max-height: 16;
        background: $surface;
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="proj-title")
        yield StatRow(
            StatCard(label="-", value="-", accent=True),
            StatCard(label="-", value="-"),
            StatCard(label="-", value="-"),
            id="proj-stat-row",
        )
        yield Static("ALL PROJECTS — sortable table", classes="section-bar")
        yield VimDataTable(id="proj-table", zebra_stripes=True, cursor_type="row")
        yield Static("ACTIVE BRANCHES", classes="section-bar")
        yield Static("", id="branch-list", classes="muted")

    async def on_mount(self) -> None:
        table = self.query_one(VimDataTable)
        table.add_columns("Project", "Today", "Week", "Month", "Commits", "Share %")

    async def refresh_data(self) -> None:
        try:
            projs = tui_data.fetch_projects()
            branches = tui_data.fetch_branches()
        except Exception as exc:
            self.query_one("#proj-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        title = self.query_one("#proj-title", Static)
        title.update(
            f"\n[bold]Projects[/bold]  [dim]{len(projs)} tracked projects[/dim]\n"
        )

        cards = list(self.query(StatCard))
        for i, card in enumerate(cards):
            if i < len(projs):
                p = projs[i]
                card.update_card(
                    label=p["name"],
                    value=tui_data.fmt_dur(p["week_minutes"]),
                    delta=f"this week · {p['week_commits']} commits",
                    value_color=_COLORS[i % len(_COLORS)],
                )
            else:
                card.update_card(label="—", value="-", delta="")

        table = self.query_one(VimDataTable)
        table.clear()
        for i, p in enumerate(projs):
            color = _COLORS[i % len(_COLORS)]
            share_filled = int(p["pct"] / 100 * 16)
            share = f"[{color}]{'█' * share_filled}[/{color}]{'░' * (16 - share_filled)} {p['pct']:.0f}%"
            table.add_row(
                f"[{color}]●[/{color}] {p['name']}",
                tui_data.fmt_dur(p["today_minutes"]),
                tui_data.fmt_dur(p["week_minutes"]),
                tui_data.fmt_dur(p["month_minutes"]),
                str(p["month_commits"]),
                share,
            )

        branch_widget = self.query_one("#branch-list", Static)
        if branches:
            lines = []
            proj_names = [p["name"] for p in projs]
            for b in branches:
                pidx = proj_names.index(b["project"]) if b["project"] in proj_names else 0
                color = _COLORS[pidx % len(_COLORS)]
                commits_str = f" · {b['commits']} commits" if b["commits"] else ""
                lines.append(
                    f" [dim]{b.get('when',''):<10}[/dim] [{color}]●[/{color}] "
                    f"[bold]{b['branch']:<28}[/bold] [dim]{b['project'][:14]}{commits_str}[/dim]"
                )
            branch_widget.update("\n".join(lines))
        else:
            branch_widget.update("[dim]No branch activity this week[/dim]")
