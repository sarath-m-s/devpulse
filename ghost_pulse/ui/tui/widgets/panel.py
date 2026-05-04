"""Panel widget — k9s-style bordered container with title."""

from __future__ import annotations

from textual.containers import Vertical


class Panel(Vertical):
    """Bordered panel with a title displayed via border_title.

    Usage::

        with Panel("MY SECTION"):
            yield SomeWidget()

    Or compose and set title later::

        p = Panel()
        p.border_title = "▌ TITLE"
    """

    DEFAULT_CSS = """
    Panel {
        border: round #23252a;
        border-title-color: #8a8f98;
        border-title-style: bold;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }
    Panel:focus-within {
        border: round #5e6ad2;
    }
    """

    def __init__(self, title: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        if title:
            self.border_title = f"▌ {title}"
