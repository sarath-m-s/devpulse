"""Insights screen — LLM workflow insights, Ask DevPulse, quick queries."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Button, Input, Static

from devpulse.ui.tui import data as tui_data
from devpulse.ui.tui.widgets import Panel, StatCard, StatRow
from devpulse.ui.tui.vim_scroll import VimVerticalScroll


_QUICK_QUERIES = [
    "What project should I focus on tomorrow?",
    "When is my best time for deep work?",
    "What command should I automate next?",
    "How did this week compare to last?",
]


class InsightsScreen(VimVerticalScroll):
    """AI Insights view. LLM insights load once per session to avoid repeated API calls."""

    DEFAULT_CSS = """
    InsightsScreen {
        padding: 1 1;
        background: #010102;
    }
    InsightsScreen Input {
        background: #0d0e11;
        border: round #23252a;
        height: 3;
        margin: 0 0 1 0;
    }
    InsightsScreen Input:focus {
        border: round #5e6ad2;
    }
    InsightsScreen .quick-row {
        height: auto;
        margin: 0 0 1 0;
    }
    InsightsScreen Button {
        background: #0d0e11;
        border: round #23252a;
        color: #8a8f98;
        margin: 0 1 0 0;
        min-width: 6;
        height: 3;
    }
    InsightsScreen Button:hover {
        background: #1b1c21;
        color: #f7f8f8;
        border: round #5e6ad2;
    }
    """

    BINDINGS = [
        Binding("i", "focus_input",    "Ask"),
        Binding("R", "rerun_insights", "Re-run"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._insights_loaded: bool = False  # only call LLM once per session

    def compose(self) -> ComposeResult:
        yield Static("", id="ins-title")
        yield StatRow(
            StatCard(label="LLM Provider", value="-", accent=True),
            StatCard(label="Model",        value="-"),
            StatCard(label="Status",       value="-"),
            StatCard(label="Data range",   value="30d"),
            id="ins-stat-row",
        )
        with Panel("WORKFLOW INSIGHTS — [R] regenerate"):
            yield Static("[dim]Loading insights from LLM…[/dim]", id="ins-list", classes="muted")
        with Panel("ASK DEVPULSE"):
            yield Input(
                placeholder="e.g. What project should I focus on tomorrow?",
                id="ins-input",
            )
            with Horizontal(classes="quick-row", id="ins-quick-buttons"):
                for i, q in enumerate(_QUICK_QUERIES):
                    label = (q[:28] + "…") if len(q) > 30 else q
                    yield Button(label=label, id=f"qq-{i}")
        with Panel("RESPONSE"):
            yield Static(
                "[dim]Type a question above and press Enter to ask the LLM[/dim]",
                id="ins-response",
                classes="muted",
            )

    async def refresh_data(self) -> None:
        try:
            provider, cfg = tui_data.get_llm_provider()
        except Exception as exc:
            self.query_one("#ins-title", Static).update(f"[red]Error: {exc}[/red]")
            return

        self.query_one("#ins-title", Static).update(
            "\n[bold #f7f8f8]AI Insights[/]  [dim]natural language analysis[/dim]\n"
        )

        provider_name = provider.name
        model_name = cfg.get("llm", {}).get("model", "auto")
        available = provider.is_available()

        cards = list(self.query(StatCard))
        if len(cards) >= 4:
            cards[0].update_card(
                value=provider_name,
                value_color="#5e6ad2",
                delta="local · free" if provider_name == "ollama" else "cloud",
            )
            cards[1].update_card(value=model_name or "auto", delta=provider_name)
            cards[2].update_card(
                value="ready" if available else "offline",
                value_color="#27a644" if available else "#e87b5a",
                delta="LLM availability",
            )
            cards[3].update_card(value="30d", delta="activity analyzed")

        if not available:
            self.query_one("#ins-list", Static).update(
                "[#e87b5a]●[/#e87b5a] No LLM provider available.\n"
                "[dim]Configure one in the Config screen (8) or run "
                "`devpulse config set llm.provider ollama`[/dim]"
            )
            self._insights_loaded = False  # allow retry once provider is configured
            return

        # Only call the LLM on the first visit — press R to re-run manually
        if not self._insights_loaded:
            self.query_one("#ins-list", Static).update("[dim]⏳ Loading insights from LLM…[/dim]")
            self._load_insights()
        # else: keep the existing rendered insights and do nothing

    def action_rerun_insights(self) -> None:
        self._insights_loaded = False
        self.query_one("#ins-list", Static).update("[dim]⏳ Regenerating insights…[/dim]")
        self._load_insights()

    def action_focus_input(self) -> None:
        self.query_one("#ins-input", Input).focus()

    @work(thread=True, exclusive=True)
    def _load_insights(self) -> None:
        result = tui_data.fetch_insights()
        self.app.call_from_thread(self._set_insights, result)

    def _set_insights(self, result: dict) -> None:
        list_widget = self.query_one("#ins-list", Static)
        text = result.get("insights", "")
        if not text:
            list_widget.update("[dim]No insights returned[/dim]")
            return
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        markers = ["#27a644", "#d97706", "#5e6ad2"]
        out = []
        for i, line in enumerate(lines):
            color = markers[i % len(markers)]
            cleaned = line.lstrip("0123456789.- *)").strip()
            out.append(f"  [{color}]●[/{color}] {cleaned}")
        list_widget.update("\n".join(out))
        self._insights_loaded = True  # don't re-run until user presses R

    # ── Input / button handling ──────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "ins-input":
            return
        q = event.value.strip()
        if not q:
            return
        self._trigger_ask(q)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if not event.button.id or not event.button.id.startswith("qq-"):
            return
        idx = int(event.button.id.split("-")[1])
        if idx < 0 or idx >= len(_QUICK_QUERIES):
            return
        q = _QUICK_QUERIES[idx]
        self.query_one("#ins-input", Input).value = q
        self._trigger_ask(q)

    def _trigger_ask(self, question: str) -> None:
        resp = self.query_one("#ins-response", Static)
        resp.update(
            f"  [#5e6ad2]⏳[/#5e6ad2] [dim]{question}[/dim]\n  [dim]thinking…[/dim]"
        )
        self._ask_llm(question)

    @work(thread=True, exclusive=True)
    def _ask_llm(self, question: str) -> None:
        answer = tui_data.ask_llm(question)
        self.app.call_from_thread(self._set_answer, question, answer)

    def _set_answer(self, question: str, answer: str) -> None:
        resp = self.query_one("#ins-response", Static)
        resp.update(
            f"  [#5e6ad2]Q[/#5e6ad2] [dim]{question}[/dim]\n\n"
            f"  [#27a644]A[/#27a644] {answer}"
        )
