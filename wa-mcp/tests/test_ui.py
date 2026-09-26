"""Tests for wa_cli.ui — in-house gradient renderer over rich.

Covers hex interpolation boundaries, empty/1-char edge cases, badge state
mapping, table construction, spinner wrapping, and the error panel. No ANSI
snapshotting per the plan — assertions target plain text and Text.spans.
"""
from __future__ import annotations

import re

import pytest
from hypothesis import given, settings, strategies as st
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from wa_cli import ui

_HEX_IN_STYLE = re.compile(r"#([0-9a-f]{6})")


def _style_rgb(style: object) -> tuple[int, int, int]:
    """Pull the (r, g, b) tuple out of a span's style string, e.g. '#ff0000'."""
    match = _HEX_IN_STYLE.search(str(style))
    assert match, f"no hex color found in style {style!r}"
    hex_str = match.group(1)
    return int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16)


_valid_hex_color = st.builds(
    lambda r, g, b: f"#{r:02x}{g:02x}{b:02x}",
    st.integers(0, 255),
    st.integers(0, 255),
    st.integers(0, 255),
)

# rich.text.Text() strips a handful of ASCII control codes (BEL, BS, VT, FF,
# CR) on construction — see rich.control.strip_control_codes. That's a
# rich-level sanitization, not something gradient_text can or should
# override, so the "text survives round-trip" property is scoped to exclude
# just those five codepoints; everything else (incl. \n, \t, emoji, CJK,
# RTL, zero-width chars) is fair game.
def _text_rich_preserves(min_size: int = 0) -> st.SearchStrategy[str]:
    return st.text(
        alphabet=st.characters(blacklist_characters="\x07\x08\x0b\x0c\x0d"),
        min_size=min_size,
        max_size=200,
    )


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


# --- gradient_text: exotic string edge cases -------------------------------


@pytest.mark.parametrize(
    "s",
    [
        "",
        "x",
        "a" * 10_000,
        "😀🎉🚀",
        "你好世界",
        "café résumé naïve",  # combining/precomposed accents
        "مرحبا بالعالم",  # Arabic, RTL
        "שלום עולם",  # Hebrew, RTL
        "line1\nline2\ttabbed",
        "back\\slash \"quoted\" 'text'",
        "​",  # zero-width space
        "a​b​c",  # zero-width chars interspersed
    ],
    ids=[
        "empty",
        "single-char",
        "very-long-10000",
        "emoji",
        "cjk",
        "accents",
        "arabic-rtl",
        "hebrew-rtl",
        "newlines-tabs",
        "backslashes-quotes",
        "zero-width-space",
        "zero-width-interspersed",
    ],
)
def test_gradient_text_handles_exotic_strings(s):
    result = ui.gradient_text(s, "#ff0000", "#0000ff")
    assert result.plain == s
    assert len(result.plain) == len(s)
    if s:
        assert len(result.spans) == len(s)
    else:
        assert len(result.spans) == 0


def test_gradient_text_long_string_spans_are_contiguous_and_ordered():
    s = "a" * 10_000
    result = ui.gradient_text(s, "#000000", "#ffffff")
    for i, span in enumerate(result.spans):
        assert span.start == i
        assert span.end == i + 1


def test_gradient_text_start_equals_end_no_interpolation():
    result = ui.gradient_text("gradient", "#336699", "#336699")
    expected = (0x33, 0x66, 0x99)
    for span in result.spans:
        assert _style_rgb(span.style) == expected


def test_gradient_text_uppercase_and_lowercase_hex_equivalent():
    lower = ui.gradient_text("abcd", "#ff0000", "#00ff00")
    upper = ui.gradient_text("abcd", "#FF0000", "#00FF00")
    lower_rgbs = [_style_rgb(sp.style) for sp in lower.spans]
    upper_rgbs = [_style_rgb(sp.style) for sp in upper.spans]
    assert lower_rgbs == upper_rgbs


def test_gradient_text_mixed_case_hex_equivalent():
    mixed = ui.gradient_text("abcd", "#Ff00Aa", "#00fF00")
    canonical = ui.gradient_text("abcd", "#ff00aa", "#00ff00")
    mixed_rgbs = [_style_rgb(sp.style) for sp in mixed.spans]
    canonical_rgbs = [_style_rgb(sp.style) for sp in canonical.spans]
    assert mixed_rgbs == canonical_rgbs


def test_gradient_text_hex_without_hash_prefix_matches_with_prefix():
    with_hash = ui.gradient_text("abcd", "#ff0000", "#0000ff")
    without_hash = ui.gradient_text("abcd", "ff0000", "0000ff")
    with_rgbs = [_style_rgb(sp.style) for sp in with_hash.spans]
    without_rgbs = [_style_rgb(sp.style) for sp in without_hash.spans]
    assert with_rgbs == without_rgbs


def test_gradient_text_single_char_hex_without_hash():
    result = ui.gradient_text("x", "ff0000", "0000ff")
    assert _style_rgb(result.spans[0].style) == (0xFF, 0x00, 0x00)


# --- gradient_text: property-based invariants (hypothesis) -----------------


@settings(max_examples=100)
@given(s=_text_rich_preserves(), start=_valid_hex_color, end=_valid_hex_color)
def test_gradient_text_property_length_invariant(s, start, end):
    result = ui.gradient_text(s, start, end)
    assert len(result.plain) == len(s)
    assert result.plain == s
    if s:
        assert len(result.spans) == len(s)
    else:
        assert len(result.spans) == 0


@settings(max_examples=100)
@given(s=_text_rich_preserves(min_size=1), start=_valid_hex_color, end=_valid_hex_color)
def test_gradient_text_property_span_boundaries(s, start, end):
    result = ui.gradient_text(s, start, end)
    for i, span in enumerate(result.spans):
        assert (span.start, span.end) == (i, i + 1)


@settings(max_examples=100)
@given(s=_text_rich_preserves(min_size=2), start=_valid_hex_color, end=_valid_hex_color)
def test_gradient_text_property_first_and_last_color_exact(s, start, end):
    result = ui.gradient_text(s, start, end)

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    assert _style_rgb(result.spans[0].style) == hex_to_rgb(start)
    assert _style_rgb(result.spans[-1].style) == hex_to_rgb(end)


@settings(max_examples=50)
@given(start=_valid_hex_color)
def test_gradient_text_property_single_char_uses_start_color(start):
    result = ui.gradient_text("x", start, "#123456")

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)

    assert _style_rgb(result.spans[0].style) == hex_to_rgb(start)


@settings(max_examples=100)
@given(s=_text_rich_preserves(min_size=1), start=_valid_hex_color, end=_valid_hex_color)
def test_gradient_text_property_style_is_six_hex_digits(s, start, end):
    result = ui.gradient_text(s, start, end)
    for span in result.spans:
        assert re.fullmatch(r"#[0-9a-f]{6}", str(span.style))


def test_gradient_text_two_char_string_uses_exact_endpoints_no_blend():
    # n=2 means t is 0.0 for the first char and 1.0 for the second — there's
    # no room for an interpolated in-between value, so both spans must be
    # the exact endpoint colors, not an averaged/rounded blend.
    result = ui.gradient_text("ab", "#102030", "#a0b0c0")
    assert _style_rgb(result.spans[0].style) == (0x10, 0x20, 0x30)
    assert _style_rgb(result.spans[1].style) == (0xA0, 0xB0, 0xC0)


def test_gradient_text_mixed_hash_prefix_start_and_end():
    with_hash_start = ui.gradient_text("abc", "#ff0000", "0000ff")
    both_hash = ui.gradient_text("abc", "#ff0000", "#0000ff")
    with_rgbs = [_style_rgb(sp.style) for sp in with_hash_start.spans]
    both_rgbs = [_style_rgb(sp.style) for sp in both_hash.spans]
    assert with_rgbs == both_rgbs


# --- badge: invalid/edge-case states ----------------------------------------


@pytest.mark.parametrize(
    "state",
    ["", "  ", "None", "null", "üp", "up ", " up", "UP", "Up", "DOWN", "x" * 500, "\n", "\t"],
    ids=[
        "empty",
        "whitespace",
        "literal-None-string",
        "literal-null-string",
        "unicode-lookalike",
        "trailing-space",
        "leading-space",
        "uppercase-UP",
        "titlecase-Up",
        "uppercase-DOWN",
        "very-long",
        "newline",
        "tab",
    ],
)
def test_badge_invalid_states_raise_value_error(state):
    with pytest.raises(ValueError) as exc_info:
        ui.badge(state)
    message = str(exc_info.value)
    for valid_state in ("up", "down", "degraded", "warn"):
        assert valid_state in message


def test_badge_case_sensitive_lookup_is_strict():
    # Lowercase "up" is valid; any case variant must raise since the
    # underlying dict lookup is exact-match, not case-insensitive.
    ui.badge("up")
    with pytest.raises(ValueError):
        ui.badge("UP")


def test_badge_error_message_includes_bogus_value_repr():
    with pytest.raises(ValueError) as exc_info:
        ui.badge("totally-bogus")
    assert "totally-bogus" in str(exc_info.value)


def test_badge_error_message_valid_states_sorted():
    with pytest.raises(ValueError) as exc_info:
        ui.badge("bogus")
    message = str(exc_info.value)
    valid_part = message.split("expected one of: ")[1].rstrip(")")
    states = [s.strip() for s in valid_part.split(",")]
    assert states == sorted(states)


@settings(max_examples=100)
@given(state=st.text(max_size=50).filter(lambda s: s not in ("up", "down", "degraded", "warn")))
def test_badge_property_any_non_valid_string_raises(state):
    with pytest.raises(ValueError):
        ui.badge(state)


# --- styled_table: edge cases -----------------------------------------------


def test_styled_table_empty_columns_list():
    result = ui.styled_table("Empty", [])
    assert isinstance(result, Table)
    assert len(result.columns) == 0


def test_styled_table_single_column():
    result = ui.styled_table("Solo", ["OnlyCol"])
    assert len(result.columns) == 1


def test_styled_table_many_columns():
    columns = [f"col{i}" for i in range(50)]
    result = ui.styled_table("Wide", columns)
    assert len(result.columns) == 50
    header_names = [
        col.header.plain if isinstance(col.header, Text) else str(col.header)
        for col in result.columns
    ]
    assert header_names == columns


def test_styled_table_unicode_columns_and_title():
    result = ui.styled_table("联系人 📇", ["名前", "電話番号"])
    title_text = result.title.plain if isinstance(result.title, Text) else str(result.title)
    assert "联系人" in title_text
    header_names = [
        col.header.plain if isinstance(col.header, Text) else str(col.header)
        for col in result.columns
    ]
    assert "名前" in header_names
    assert "電話番号" in header_names


def test_styled_table_duplicate_column_names_both_kept():
    result = ui.styled_table("Dupes", ["Name", "Name"])
    assert len(result.columns) == 2
    header_names = [
        col.header.plain if isinstance(col.header, Text) else str(col.header)
        for col in result.columns
    ]
    assert header_names == ["Name", "Name"]


def test_styled_table_empty_title():
    result = ui.styled_table("", ["A", "B"])
    title_text = result.title.plain if isinstance(result.title, Text) else str(result.title)
    assert title_text == ""


def test_styled_table_very_long_title():
    title = "T" * 5000
    result = ui.styled_table(title, ["A"])
    title_text = result.title.plain if isinstance(result.title, Text) else str(result.title)
    assert title_text == title


def test_styled_table_add_row_extra_value_does_not_corrupt_existing_columns():
    # rich does not raise on a row with more values than columns — it grows
    # an extra unnamed column instead of raising or dropping data. Verify
    # that actual behavior rather than assuming a raise: the original
    # column's data must survive intact.
    result = ui.styled_table("Mismatch", ["Only"])
    result.add_row("value", "extra")
    assert len(result.columns) == 2
    assert result.columns[0]._cells == ["value"]
    assert result.columns[1]._cells == ["extra"]


def test_styled_table_renders_with_unicode_content():
    result = ui.styled_table("Chats", ["Name", "Message"])
    result.add_row("Alice 👩", "こんにちは")
    console = Console(record=True, width=100)
    console.print(result)
    output = console.export_text()
    assert "Alice" in output
    assert "こんにちは" in output


def test_styled_table_does_not_mutate_caller_columns_list():
    columns = ["A", "B", "C"]
    ui.styled_table("Title", columns)
    assert columns == ["A", "B", "C"]


def test_styled_table_title_is_gradient_colored():
    result = ui.styled_table("Contacts", ["JID"])
    title = result.title
    assert isinstance(title, Text)
    # gradient_text always produces one span per character for n >= 2.
    assert len(title.spans) == len("Contacts")
    expected_start = ui._BANNER_START.lstrip("#").lower()
    expected_rgb = tuple(int(expected_start[i : i + 2], 16) for i in (0, 2, 4))
    assert _style_rgb(title.spans[0].style) == expected_rgb


# --- spinner: edge cases -----------------------------------------------


def test_spinner_empty_message():
    with ui.spinner("") as status:
        assert isinstance(status, Status)


def test_spinner_unicode_message():
    with ui.spinner("送信中… 🚀") as status:
        assert isinstance(status, Status)


def test_spinner_very_long_message():
    msg = "working " * 1000
    with ui.spinner(msg) as status:
        assert isinstance(status, Status)


def test_spinner_sequential_use_does_not_raise():
    with ui.spinner("first"):
        pass
    with ui.spinner("second"):
        pass


def test_spinner_nested_context_managers_do_not_raise():
    with ui.spinner("outer"):
        with ui.spinner("inner"):
            pass


def test_spinner_each_call_returns_new_status_object():
    a = ui.spinner("a")
    b = ui.spinner("b")
    assert a is not b


def test_spinner_whitespace_only_message():
    with ui.spinner("   ") as status:
        assert isinstance(status, Status)


# --- error_panel: edge cases --------------------------------------------


def test_error_panel_empty_message():
    console = Console(record=True, width=80)
    console.print(ui.error_panel(""))
    output = console.export_text()
    assert "Error" in output


def test_error_panel_unicode_emoji_message():
    console = Console(record=True, width=80)
    console.print(ui.error_panel("bridge down 🔥 接続エラー"))
    output = console.export_text()
    assert "接続エラー" in output


def test_error_panel_multiline_message():
    console = Console(record=True, width=80)
    console.print(ui.error_panel("line one\nline two\nline three"))
    output = console.export_text()
    assert "line one" in output
    assert "line two" in output
    assert "line three" in output


def test_error_panel_very_long_message():
    msg = "x" * 3000
    console = Console(record=True, width=80)
    console.print(ui.error_panel(msg))
    output = console.export_text()
    assert "x" in output


def test_error_panel_markup_like_text_rendered_literally():
    # Panel wraps a plain Text(msg, style="red") — not Text.from_markup —
    # so bracketed markup-looking text must show up verbatim, not be
    # interpreted as rich console markup.
    console = Console(record=True, width=80)
    console.print(ui.error_panel("has [bold]markup-looking[/bold] text"))
    output = console.export_text()
    assert "[bold]markup-looking[/bold]" in output


def test_error_panel_title_is_error():
    result = ui.error_panel("anything")
    title_text = result.title.plain if isinstance(result.title, Text) else str(result.title)
    assert title_text == "Error"


def test_error_panel_red_border_style():
    result = ui.error_panel("anything")
    assert result.border_style == "red"


def test_error_panel_whitespace_only_message():
    console = Console(record=True, width=80)
    console.print(ui.error_panel("   "))
    output = console.export_text()
    assert "Error" in output


# --- console: module singleton behavior -------------------------------


def test_console_singleton_across_reimports():
    import importlib

    from wa_cli import ui as ui_first_import

    reloaded = importlib.import_module("wa_cli.ui")
    assert reloaded.console is ui_first_import.console
    assert reloaded.console is ui.console


def test_console_singleton_same_object_via_module_attribute():
    import sys

    module = sys.modules["wa_cli.ui"]
    assert module.console is ui.console
