"""Stat card widget — k9s-style boxed metric with label, value, delta."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static


class StatCard(Vertical):
    """A single stat card. Use update_card(label, value, delta, delta_class)."""

    DEFAULT_CSS = """
    StatCard {
        background: #0d0e11;
        border: round #23252a;
        padding: 0 1;
        height: 7;
        width: 1fr;
    }
    StatCard.accent {
        border-top: heavy #5e6ad2;
    }
    StatCard > .stat-label {
        color: #8a8f98;
        text-style: bold;
        margin-top: 1;
    }
    StatCard > .stat-value {
        color: #f7f8f8;
        text-style: bold;
    }
    StatCard > .stat-delta {
        color: #62666d;
    }
    StatCard > .stat-delta.up   { color: #27a644; }
    StatCard > .stat-delta.down { color: #e87b5a; }
    StatCard > .stat-delta.warn { color: #d97706; }
    """

    def __init__(
        self,
        label: str = "",
        value: str = "-",
        delta: str = "",
        delta_class: str = "",
        value_color: str = "",
        accent: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._delta = delta
        self._delta_class = delta_class
        self._value_color = value_color
        self._accent = accent

    def compose(self) -> ComposeResult:
        yield Static(self._label, classes="stat-label")
        value_widget = Static(self._value, classes="stat-value")
        if self._value_color:
            value_widget.styles.color = self._value_color
        yield value_widget
        delta_classes = f"stat-delta {self._delta_class}".strip()
        yield Static(self._delta, classes=delta_classes)
        if self._accent:
            self.add_class("accent")

    def update_card(
        self,
        label: str | None = None,
        value: str | None = None,
        delta: str | None = None,
        delta_class: str | None = None,
        value_color: str | None = None,
    ) -> None:
        try:
            children = list(self.query(Static))
            if not children:
                return
            if label is not None and len(children) > 0:
                children[0].update(label)
            if value is not None and len(children) > 1:
                children[1].update(value)
                if value_color:
                    children[1].styles.color = value_color
            if delta is not None and len(children) > 2:
                children[2].update(delta)
            if delta_class is not None and len(children) > 2:
                children[2].set_classes(f"stat-delta {delta_class}".strip())
        except Exception:
            pass


class StatRow(Horizontal):
    """A horizontal row of stat cards."""

    DEFAULT_CSS = """
    StatRow {
        height: 7;
        width: 100%;
        margin: 0 0 1 0;
    }
    StatRow > StatCard {
        margin: 0 1 0 0;
    }
    StatRow > StatCard:last-of-type {
        margin: 0;
    }
    """
