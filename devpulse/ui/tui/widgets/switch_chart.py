"""Vertical bar chart for context switches / daily metrics."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget


_BLOCKS = "▁▂▃▄▅▆▇█"


def _color_for(value: int, low: int = 10, high: int = 20) -> str:
    if value == 0:
        return "grey30"
    if value < low:
        return "#27a644"  # green
    if value < high:
        return "#d97706"  # warn
    return "#e87b5a"  # danger


class SwitchChart(Widget):
    """Vertical bars: list of {day, value, is_today} -> small bar chart."""

    DEFAULT_CSS = """
    SwitchChart {
        height: auto;
        background: transparent;
    }
    """

    def __init__(
        self,
        items: list[dict] | None = None,
        height_lines: int = 6,
        color_thresholds: tuple[int, int] = (10, 20),
        bar_color_override: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.items = items or []
        self.height_lines = height_lines
        self.thresholds = color_thresholds
        self.bar_color_override = bar_color_override

    def set_items(self, items: list[dict]) -> None:
        self.items = items
        self.refresh(layout=True)

    def render(self) -> Text:
        if not self.items:
            return Text("No data", style="dim")

        max_v = max((i.get("value", 0) for i in self.items), default=0) or 1
        n_lines = self.height_lines

        # Build lines from top to bottom
        lines: list[Text] = []
        for line_idx in range(n_lines, 0, -1):
            line = Text()
            for item in self.items:
                v = item.get("value", 0)
                bar_h = (v / max_v) * n_lines if v > 0 else 0
                if bar_h >= line_idx:
                    color = self.bar_color_override or _color_for(v, *self.thresholds)
                    if item.get("is_today"):
                        color = "#5e6ad2"
                    line.append("  █  ", style=color)
                elif bar_h >= line_idx - 0.5 and bar_h > 0:
                    color = self.bar_color_override or _color_for(v, *self.thresholds)
                    if item.get("is_today"):
                        color = "#5e6ad2"
                    line.append("  ▄  ", style=color)
                else:
                    line.append("     ")
            lines.append(line)

        # Day labels
        labels_line = Text()
        values_line = Text()
        for item in self.items:
            day = str(item.get("day", ""))[:3]
            val = str(item.get("value", 0))
            labels_line.append(f" {day:^4}", style="grey50")
            values_line.append(f" {val:^4}", style="grey70")

        # Compose
        out = Text()
        for line in lines:
            out.append(line)
            out.append("\n")
        out.append(labels_line)
        out.append("\n")
        out.append(values_line)
        return out

    def get_content_height(self, container, viewport, width: int) -> int:
        return self.height_lines + 2
