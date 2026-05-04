"""Textual scroll / table helpers with j/k support (arrow keys are built-in)."""

from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.containers import ScrollableContainer, VerticalScroll
from textual.widgets import DataTable


class VimVerticalScroll(VerticalScroll):
    """Like VerticalScroll, but also scrolls with j / k when this widget is focused."""

    BINDINGS: ClassVar[list[BindingType]] = [
        *ScrollableContainer.BINDINGS,
        Binding("j", "scroll_down", "", show=False),
        Binding("k", "scroll_up", "", show=False),
    ]


class VimDataTable(DataTable):
    """DataTable with j / k for row cursor (same as down / up)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        *DataTable.BINDINGS,
        Binding("j", "cursor_down", "", show=False),
        Binding("k", "cursor_up", "", show=False),
    ]
