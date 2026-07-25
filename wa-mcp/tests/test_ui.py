"""Tests for wa_cli.ui — in-house gradient renderer over rich.

Covers hex interpolation boundaries, empty/1-char edge cases, badge state
mapping, table construction, spinner wrapping, and the error panel. No ANSI
snapshotting per the plan — assertions target plain text and Text.spans.
"""
from __future__ import annotations

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from wa_cli import ui


# --- gradient_text ---------------------------------------------------------


def test_gradient_text_returns_text_instance():
    result = ui.gradient_text("hello", "#ff0000", "#0000ff")
    assert isinstance(result, Text)


def test_gradient_text_preserves_plain_string():
    s = "WhatsApp"
    result = ui.gradient_text(s, "#ff0000", "#00ff00")
    assert result.plain == s


def test_gradient_text_first_char_is_start_color():
    result = ui.gradient_text("abcdef", "#ff0000", "#00ff00")
    spans = result.spans
    assert len(spans) == len("abcdef")
    first_style = spans[0].style
    assert "#ff0000" in str(first_style)


def test_gradient_text_last_char_is_end_color():
    result = ui.gradient_text("abcdef", "#ff0000", "#00ff00")
    spans = result.spans
    last_style = spans[-1].style
    assert "#00ff00" in str(last_style)


def test_gradient_text_length_matches_input_length():
    for s in ["", "x", "hello world", "a" * 50]:
        result = ui.gradient_text(s, "#000000", "#ffffff")
        assert len(result.plain) == len(s)


def test_gradient_text_empty_string_no_divzero():
    result = ui.gradient_text("", "#ff0000", "#0000ff")
    assert isinstance(result, Text)
    assert result.plain == ""
    assert len(result.spans) == 0


def test_gradient_text_single_char_uses_start_color():
    result = ui.gradient_text("x", "#ff0000", "#0000ff")
    assert result.plain == "x"
    assert len(result.spans) == 1
    assert "#ff0000" in str(result.spans[0].style)


def test_gradient_text_interpolates_midpoint_color():
    # Odd-length string so there's an exact middle character; verify the
    # interpolated RGB is (approximately) the arithmetic mean of endpoints.
    result = ui.gradient_text("abc", "#000000", "#ffffff")
    mid_style = str(result.spans[1].style)
    # midpoint of 0x00 -> 0xff over 3 chars (index 1 of 0,1,2) is 0x7f/0x80
    assert "#7f7f7f" in mid_style or "#808080" in mid_style


def test_gradient_text_each_span_covers_one_character():
    result = ui.gradient_text("gradient", "#123456", "#abcdef")
    for i, span in enumerate(result.spans):
        assert span.start == i
        assert span.end == i + 1


# --- banner ------------------------------------------------------------


def test_banner_returns_text_instance():
    result = ui.banner()
    assert isinstance(result, Text)


def test_banner_is_nonempty():
    result = ui.banner()
    assert len(result.plain) > 0


def test_banner_mentions_wa_or_whatsapp():
    result = ui.banner().plain.lower()
    assert "wa" in result or "whatsapp" in result


# --- badge ---------------------------------------------------------------


def test_badge_up_state():
    result = ui.badge("up")
    assert isinstance(result, Text)
    assert "up" in result.plain
    assert "●" in result.plain
    style_str = str(result.spans[0].style) if result.spans else str(result.style)
    assert "green" in style_str


def test_badge_down_state():
    result = ui.badge("down")
    assert "down" in result.plain
    assert "○" in result.plain
    style_str = str(result.spans[0].style) if result.spans else str(result.style)
    assert "red" in style_str
    assert "dim" in style_str


def test_badge_degraded_state():
    result = ui.badge("degraded")
    assert "degraded" in result.plain
    assert "◐" in result.plain
    style_str = str(result.spans[0].style) if result.spans else str(result.style)
    assert "yellow" in style_str


def test_badge_warn_state_same_as_degraded():
    result = ui.badge("warn")
    assert "warn" in result.plain
    assert "◐" in result.plain
    style_str = str(result.spans[0].style) if result.spans else str(result.style)
    assert "yellow" in style_str


def test_badge_unknown_state_raises():
    with pytest.raises(ValueError):
        ui.badge("bogus-state")


@pytest.mark.parametrize("state", ["up", "down", "degraded", "warn"])
def test_badge_renders_without_raising(state):
    console = Console(record=True, width=80)
    console.print(ui.badge(state))
    output = console.export_text()
    assert output.strip() != ""


# --- styled_table ----------------------------------------------------------


def test_styled_table_returns_table_instance():
    result = ui.styled_table("Contacts", ["JID", "Name"])
    assert isinstance(result, Table)


def test_styled_table_has_title():
    result = ui.styled_table("Contacts", ["JID", "Name"])
    assert result.title is not None
    title_text = result.title.plain if isinstance(result.title, Text) else str(result.title)
    assert "Contacts" in title_text


def test_styled_table_has_all_columns():
    columns = ["JID", "Name", "Push name", "Business name"]
    result = ui.styled_table("Contacts", columns)
    header_names = [col.header.plain if isinstance(col.header, Text) else str(col.header)
                    for col in result.columns]
    for col in columns:
        assert any(col in h for h in header_names)


def test_styled_table_renders_without_raising():
    result = ui.styled_table("Chats", ["Name", "JID", "Last activity", "Kind"])
    result.add_row("Alice", "123@s.whatsapp.net", "2026-07-11", "direct")
    console = Console(record=True, width=100)
    console.print(result)
    output = console.export_text()
    assert "Alice" in output


# --- spinner ---------------------------------------------------------------


def test_spinner_returns_status_instance():
    result = ui.spinner("Sending...")
    assert isinstance(result, Status)


def test_spinner_can_be_used_as_context_manager():
    with ui.spinner("Working...") as status:
        assert isinstance(status, Status)


# --- error_panel -------------------------------------------------------


def test_error_panel_returns_panel_instance():
    result = ui.error_panel("bridge not running")
    assert isinstance(result, Panel)


def test_error_panel_contains_message():
    console = Console(record=True, width=100)
    console.print(ui.error_panel("bridge not running — try `wa up`"))
    output = console.export_text()
    assert "bridge not running" in output


def test_error_panel_renders_without_raising():
    console = Console(record=True, width=80)
    console.print(ui.error_panel("some error message"))
    output = console.export_text()
    assert output.strip() != ""


# --- module-level console ---------------------------------------------


def test_module_level_console_exists():
    assert isinstance(ui.console, Console)
