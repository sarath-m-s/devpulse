"""Activity heatmap widget — hours × days grid, k9s palette."""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget


# Intensity levels: 0=dark bg, 1=low, 2=mid, 3=primary
_INTENSITY_COLORS = ["#1a1b20", "#2d3654", "#3d4f8a", "#5e6ad2"]
_INTENSITY_CHARS  = ["░░", "▒▒", "▓▓", "██"]


class Heatmap(Widget):
    """Grid: rows = days, columns = hours, color/char = intensity 0-3.

    Cell format: ██ (2 wide) with 1 space gap between cells.
    Header row shows hours right-aligned.
    Day labels are right-aligned at 4 chars.
    """

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
            return Text("  No data", style="dim")

        out = Text()

        # Header row: "     h.9 .10 .11 ..."
        out.append("     ", style="dim")
        for h in self.hours:
            label = f".{h}" if h >= 10 else f"h.{h}"
            out.append(f"{label:<3} ", style="dim #62666d")
        out.append("\n")

        for day, row in zip(self.days, self.grid):
            out.append(f"{day:>4} ", style="grey70")
            for v in row:
                v = max(0, min(3, int(v)))
                out.append(_INTENSITY_CHARS[v], style=_INTENSITY_COLORS[v])
                out.append(" ")
            out.append("\n")

        return out

    def get_content_height(self, container, viewport, width: int) -> int:
        return len(self.days) + 2 if self.days else 1
