"""Horizontal bar chart widget."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget


_BAR_COLORS = ["#5e6ad2", "#fbbf24", "#4ade80", "#f472b6", "#22d3ee", "#fb923c", "#a78bfa", "#34d399"]


class BarChart(Widget):
    """A horizontal bar chart. Each row: label, bar, value."""

    DEFAULT_CSS = """
    BarChart {
        height: auto;
        background: transparent;
    }
    """

    def __init__(
        self,
        rows: list[dict] | None = None,
        bar_width: int = 28,
        label_width: int = 18,
        empty_msg: str = "No data",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.rows = rows or []
        self.bar_width = bar_width
        self.label_width = label_width
        self.empty_msg = empty_msg

    def set_rows(self, rows: list[dict]) -> None:
        """Each row needs: label, value (str display), pct (0-100)."""
        self.rows = rows
        self.refresh(layout=True)

    def render(self) -> Text:
        if not self.rows:
            return Text(self.empty_msg, style="dim")

        out = Text()
        for i, r in enumerate(self.rows):
            label = str(r.get("label", ""))[: self.label_width].ljust(self.label_width)
            pct = float(r.get("pct", 0))
            value = str(r.get("value", ""))
            color = r.get("color") or _BAR_COLORS[i % len(_BAR_COLORS)]
            filled = round(pct / 100 * self.bar_width)
            filled = max(0, min(filled, self.bar_width))
            empty = self.bar_width - filled

            out.append(label, style="bold")
            out.append("  ")
            out.append("█" * filled, style=color)
            out.append("░" * empty, style="grey23")
            out.append(f"  {value:>10}\n", style="dim")

        return out

    def get_content_width(self, container, viewport) -> int:
        return self.label_width + self.bar_width + 14

    def get_content_height(self, container, viewport, width: int) -> int:
        return max(1, len(self.rows)) if self.rows else 1
