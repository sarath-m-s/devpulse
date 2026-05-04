"""Activity heatmap widget — hours x days grid."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget


# Rich/Textual do not reliably parse #RRGGBBAA — use opaque 6-digit hex tiers.
_INTENSITY_COLORS = ["grey23", "#3d4666", "#4c57a8", "#5e6ad2"]
_INTENSITY_CHARS = ["·", "▒", "▓", "█"]


class Heatmap(Widget):
    """Grid: rows = days, columns = hours, color/char = intensity 0-3."""

    DEFAULT_CSS = """
    Heatmap {
        height: auto;
        background: transparent;
    }
    """

    def __init__(
        self,
        hours: list[int] | None = None,
        days: list[str] | None = None,
        grid: list[list[int]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.hours = hours or []
        self.days = days or []
        self.grid = grid or []

    def set_data(self, hours: list[int], days: list[str], grid: list[list[int]]) -> None:
        self.hours = hours
        self.days = days
        self.grid = grid
        self.refresh(layout=True)

    def render(self) -> Text:
        if not self.grid:
            return Text("No data", style="dim")

        out = Text()
        # Header row
        out.append("    ", style="dim")
        for h in self.hours:
            out.append(f" {h:>2}", style="dim")
        out.append("\n")

        for day, row in zip(self.days, self.grid):
            out.append(f"{day:>4}", style="grey70")
            for v in row:
                v = max(0, min(3, int(v)))
                out.append("  ")
                out.append(_INTENSITY_CHARS[v] * 1, style=_INTENSITY_COLORS[v])
            out.append("\n")

        return out

    def get_content_height(self, container, viewport, width: int) -> int:
        return len(self.days) + 2 if self.days else 1
