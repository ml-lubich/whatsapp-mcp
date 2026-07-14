"""In-house gradient text renderer over rich.

Small helper that linearly interpolates hex colors across a string and
emits a `rich.text.Text` with per-character styles (charm/gradient-string
aesthetic) — no external gradient dependency. Also provides the gradient
banner, status badges, gradient-accented tables, a spinner wrapper, an
error panel, and the module-level `console` used by the rest of the CLI.
"""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

console = Console()

_BANNER_START = "#25D366"  # WhatsApp green
_BANNER_END = "#128C7E"  # WhatsApp teal

_BADGE_STYLES: dict[str, tuple[str, str]] = {
    "up": ("●", "green"),
    "down": ("○", "dim red"),
    "degraded": ("◐", "yellow"),
    "warn": ("◐", "yellow"),
}


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lerp_channel(start: int, end: int, t: float) -> int:
    return round(start + (end - start) * t)


def gradient_text(s: str, start_hex: str, end_hex: str) -> Text:
    """Linearly interpolate `start_hex` -> `end_hex` across each character of `s`.

    Handles the empty string (no spans) and single-char strings (uses the
    start color, avoiding division by zero) explicitly.
    """
    text = Text(s)
    n = len(s)
    if n == 0:
        return text
    if n == 1:
        r, g, b = _hex_to_rgb(start_hex)
        text.stylize(f"#{r:02x}{g:02x}{b:02x}", 0, 1)
        return text

    start_rgb = _hex_to_rgb(start_hex)
    end_rgb = _hex_to_rgb(end_hex)
    for i in range(n):
        t = i / (n - 1)
        r = _lerp_channel(start_rgb[0], end_rgb[0], t)
        g = _lerp_channel(start_rgb[1], end_rgb[1], t)
        b = _lerp_channel(start_rgb[2], end_rgb[2], t)
        text.stylize(f"#{r:02x}{g:02x}{b:02x}", i, i + 1)
    return text


def banner() -> Text:
    """Gradient ASCII banner for the `wa` CLI."""
    return gradient_text("wa · WhatsApp CLI", _BANNER_START, _BANNER_END)


def badge(state: str) -> Text:
    """Status badge for a given state: up, down, degraded, or warn."""
    try:
        symbol, style = _BADGE_STYLES[state]
    except KeyError:
        valid = ", ".join(sorted(_BADGE_STYLES))
        raise ValueError(f"unknown badge state: {state!r} (expected one of: {valid})") from None
    return Text(f"{symbol} {state}", style=style)


def styled_table(title: str, columns: list[str]) -> Table:
    """A rich Table with a gradient-accented title and the given columns."""
    table = Table(title=gradient_text(title, _BANNER_START, _BANNER_END))
    for column in columns:
        table.add_column(column)
    return table


def spinner(msg: str) -> Status:
    """Wrap `rich.status.Status` for use as a context manager while a
    request/operation is in flight."""
    return console.status(msg)


def error_panel(msg: str) -> Panel:
    """Red-accented panel for reporting command failures."""
    return Panel(Text(msg, style="red"), title="Error", border_style="red")
