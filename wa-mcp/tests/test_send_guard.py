"""Tests for the send-time anti-repeat guard: `wa send --force` and the
printed recent-thread context.

Mirrors test_main.py's isolation contract: no network, no real store DBs —
every test monkeypatches db.resolve_chat_jid / db.recent_messages / api.send_message.
"""

from __future__ import annotations

import difflib
from datetime import datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from typer.testing import CliRunner

from wa_cli import api, db, main

runner = CliRunner()

RECIPIENT = "14157863858@s.whatsapp.net"


def _msg(text: str, is_from_me: bool, minutes_ago: int = 1) -> db.Message:
    return db.Message(
        is_from_me=is_from_me,
        timestamp=datetime.now() - timedelta(minutes=minutes_ago),
        text=text,
    )


def _patch_db_path(monkeypatch, tmp_path):
    # An existing (but otherwise unused) file — just needs .exists() to be True
    # so the guard proceeds to call db.resolve_chat_jid / db.recent_messages,
    # which are monkeypatched separately in each test.
    fake_db = tmp_path / "messages.db"
    fake_db.write_text("")
    monkeypatch.setattr(main.config, "messages_db", lambda: fake_db)


def _patch_recent(monkeypatch, tmp_path, messages):
    _patch_db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(db, "resolve_chat_jid", lambda db_path, recipient: RECIPIENT)
    monkeypatch.setattr(db, "recent_messages", lambda db_path, chat_jid, limit=10: messages)


def _patch_send(monkeypatch, ok: bool = True, detail: str = "sent ok"):
    calls = []

    def fake_send(recipient, message, *, base_url):
        calls.append((recipient, message))
        return ok, detail

    monkeypatch.setattr(api, "send_message", fake_send)
    return calls


# --- duplicate guard ---


def test_send_blocks_exact_duplicate_without_force(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert "already sent" in result.output.lower()
    assert calls == []


def test_send_near_duplicate_blocked_without_force(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday night?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday nite?"])

    assert result.exit_code != 0
    assert calls == []


def test_send_force_overrides_duplicate_and_sends(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?", "--force"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Hey are we still on for Friday?")]


def test_send_new_message_sends_normally(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Completely different message about Saturday"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Completely different message about Saturday")]


def test_send_only_blocks_on_our_own_outbound_messages(monkeypatch, tmp_path):
    # Identical text from THEM (not us) must not trigger the duplicate guard.
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=False)])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Hey are we still on for Friday?")]


# --- recent-thread context printed ---


def test_send_prints_recent_thread_on_success(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("earlier message", is_from_me=False)])
    _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "brand new text"])

    assert result.exit_code == 0
    assert "earlier message" in result.output


def test_send_prints_recent_thread_on_block(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [_msg("Hey are we still on for Friday?", is_from_me=True)])
    _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert "Hey are we still on for Friday?" in result.output


# --- resilience: context fetch failures never block a real send ---


def test_send_db_read_failure_still_sends(monkeypatch, tmp_path):
    _patch_db_path(monkeypatch, tmp_path)

    def _raise(db_path, recipient):
        raise RuntimeError("db read error")

    monkeypatch.setattr(db, "resolve_chat_jid", _raise)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "some message text"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "some message text")]


def test_send_missing_db_still_sends(monkeypatch, tmp_path):
    monkeypatch.setattr(main.config, "messages_db", lambda: tmp_path / "does-not-exist.db")
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "some message text"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "some message text")]


def test_send_unresolved_chat_jid_still_sends(monkeypatch, tmp_path):
    """db.resolve_chat_jid returning None (recipient not found in any known
    chat) is a normal, non-exceptional miss — the guard must treat it the
    same as "no context available" and never block the actual send."""
    _patch_db_path(monkeypatch, tmp_path)
    monkeypatch.setattr(db, "resolve_chat_jid", lambda db_path, recipient: None)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "some message text"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "some message text")]


# --- _normalize_text edge cases ---

_NORMALIZE_CASES = [
    pytest.param("hello world", "hello world", id="already-normalized"),
    pytest.param("Hello   World", "hello world", id="mixed-case-extra-internal-spaces"),
    pytest.param("  \t Hello\nWorld \t ", "hello world", id="leading-trailing-tabs-newlines"),
    pytest.param("HELLO\t\tWORLD", "hello world", id="all-caps-tabs"),
    pytest.param(" \n Mixed \t CASE  \n text \n", "mixed case text", id="mixed-whitespace-kinds-one-string"),
    pytest.param("Straße", "strasse", id="german-sharp-s-casefold"),
    pytest.param("STRASSE", "strasse", id="german-sharp-s-equivalent-upper"),
    pytest.param("Straße Hello", "strasse hello", id="mixed-sharp-s-and-ascii"),
    pytest.param("İstanbul", "i̇stanbul", id="turkish-dotted-i-casefold"),
    pytest.param("école", "école", id="combining-acute-accent-untouched"),
    pytest.param("ＡＢＣ", "ａｂｃ", id="fullwidth-latin-casefolds-to-fullwidth-lower"),
    pytest.param("man‍❤️woman", "man‍❤️woman", id="zwj-emoji-sequence-untouched"),
    pytest.param("hello world", "hello world", id="non-breaking-space-collapsed"),
    pytest.param("hello\x0bworld", "hello world", id="vertical-tab-collapsed"),
    pytest.param("hello\x0cworld", "hello world", id="form-feed-collapsed"),
    pytest.param("…", "…", id="ellipsis-char-non-letter-untouched"),
    pytest.param("\U0001D400\U0001D401", "\U0001D400\U0001D401", id="math-bold-letters-not-casefolded"),
    pytest.param("MIXED مرحبا Hello", "mixed مرحبا hello", id="mixed-latin-and-rtl-in-one-string"),
    pytest.param("مرحبا بالعالم", "مرحبا بالعالم", id="rtl-arabic-untouched"),
    pytest.param("  مرحبا   بالعالم  ", "مرحبا بالعالم", id="rtl-arabic-extra-whitespace"),
    pytest.param("😀🎉 Hello", "😀🎉 hello", id="emoji-not-casefolded-away"),
    pytest.param("😀   🎉", "😀 🎉", id="emoji-only-whitespace-collapsed"),
    pytest.param("", "", id="empty-string"),
    pytest.param("   ", "", id="whitespace-only"),
    pytest.param("\t\n\t", "", id="tabs-and-newlines-only"),
]


@pytest.mark.parametrize("raw, expected", _NORMALIZE_CASES)
def test_normalize_text_cases(raw, expected):
    assert main._normalize_text(raw) == expected


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(text=st.text(max_size=200))
def test_normalize_text_is_idempotent(text):
    once = main._normalize_text(text)
    twice = main._normalize_text(once)
    assert once == twice


@settings(max_examples=100, deadline=None)
@given(text=st.text(max_size=200))
def test_normalize_text_always_single_spaced_and_casefolded(text):
    normalized = main._normalize_text(text)
    # No run of internal whitespace survives, and casefold is idempotent.
    assert "  " not in normalized
    assert normalized == normalized.casefold()
    assert normalized == normalized.strip()


# --- difflib duplicate-ratio boundary: derived, not hand-picked ---


def _direct_block_decision(existing_text: str, candidate_text: str) -> bool:
    """Recompute the guard's decision independently of `_find_duplicate_outbound`."""
    norm_existing = main._normalize_text(existing_text)
    norm_candidate = main._normalize_text(candidate_text)
    if norm_existing == norm_candidate:
        return True
    ratio = difflib.SequenceMatcher(None, norm_existing, norm_candidate).ratio()
    return ratio >= main.DUPLICATE_RATIO_THRESHOLD


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    base=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=60),
    suffix=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=15),
)
def test_duplicate_guard_boundary_matches_direct_difflib_computation(base, suffix):
    """The guard's block/allow call must exactly match an independent
    difflib.SequenceMatcher computation over the normalized strings — the
    boundary pairs are derived from hypothesis, never hand-picked guesses.
    """
    existing = base
    candidate = base + suffix
    msg = db.Message(is_from_me=True, timestamp=datetime.now(), text=existing)

    result = main._find_duplicate_outbound(candidate, [msg])
    expected_block = _direct_block_decision(existing, candidate)

    assert (result is not None) == expected_block


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@given(
    existing=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=60),
    candidate=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=60),
)
def test_duplicate_guard_boundary_matches_direct_difflib_computation_arbitrary_pairs(existing, candidate):
    """Same cross-check as above, but over independently-generated (not
    base+suffix) string pairs, so near-misses on both sides of 0.92 are hit.
    """
    msg = db.Message(is_from_me=True, timestamp=datetime.now(), text=existing)

    result = main._find_duplicate_outbound(candidate, [msg])
    expected_block = _direct_block_decision(existing, candidate)

    assert (result is not None) == expected_block


@settings(max_examples=50, deadline=None)
@given(base=st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=20, max_size=80))
def test_duplicate_ratio_decreases_monotonically_as_edit_distance_increases(base):
    """As we truncate progressively more characters off the end (increasing
    edit distance from the original), the difflib ratio against the
    original must never increase — and the guard's block decision, which is
    a direct threshold on that ratio, must fall from blocked to allowed at
    most once as edit distance grows, never flip back.
    """
    norm_base = main._normalize_text(base)
    prev_ratio = 1.0
    seen_allow = False
    for cut in range(0, len(base) - 5, 5):
        candidate = base[: len(base) - cut] if cut else base
        ratio = difflib.SequenceMatcher(None, norm_base, main._normalize_text(candidate)).ratio()
        assert ratio <= prev_ratio + 1e-9
        prev_ratio = ratio

        blocked = ratio >= main.DUPLICATE_RATIO_THRESHOLD
        if not blocked:
            seen_allow = True
        elif seen_allow:
            pytest.fail("block decision flipped back to blocked after becoming allowed")


# --- RECENT_CONTEXT_LIMIT boundary: exactly 10 prior messages ---


def test_duplicate_guard_inspects_all_ten_messages_at_exact_limit(monkeypatch, tmp_path):
    """With exactly RECENT_CONTEXT_LIMIT (10) prior messages and the
    duplicate sitting in the very last slot, the guard must still find it —
    proving all 10 are inspected, not just a leading subset.
    """
    assert main.RECENT_CONTEXT_LIMIT == 10
    messages = [_msg(f"unrelated message number {i}", is_from_me=(i % 2 == 0), minutes_ago=20 - i) for i in range(9)]
    messages.append(_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=1))
    assert len(messages) == 10

    _patch_recent(monkeypatch, tmp_path, messages)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert calls == []


def test_duplicate_guard_ignores_their_messages_among_ten(monkeypatch, tmp_path):
    """Same 10-message boundary, but the matching text is from THEM in every
    slot except position 0 (ours, no match) — confirms every one of the 10
    is checked for is_from_me, and a match from them never blocks.
    """
    messages = [_msg("no match here", is_from_me=True, minutes_ago=20)]
    messages += [_msg("Hey are we still on for Friday?", is_from_me=False, minutes_ago=9 - i) for i in range(9)]
    assert len(messages) == 10

    _patch_recent(monkeypatch, tmp_path, messages)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "Hey are we still on for Friday?")]


def test_duplicate_guard_finds_match_in_middle_of_ten(monkeypatch, tmp_path):
    """The duplicate sits at position 5 of 10 (neither first nor last) —
    guards against an off-by-one that only checks the ends of the list."""
    messages = [_msg(f"filler {i}", is_from_me=(i % 2 == 0), minutes_ago=20 - i) for i in range(5)]
    messages.append(_msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=8))
    messages += [_msg(f"filler {i}", is_from_me=(i % 2 == 0), minutes_ago=7 - i) for i in range(4)]
    assert len(messages) == 10

    _patch_recent(monkeypatch, tmp_path, messages)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert calls == []


def test_duplicate_guard_works_with_fewer_than_ten_recent_messages(monkeypatch, tmp_path):
    """The guard doesn't require exactly RECENT_CONTEXT_LIMIT messages — a
    short (new) thread with only 3 prior messages must still be checked."""
    messages = [_msg("hi", is_from_me=False, minutes_ago=3), _msg("Hey are we still on for Friday?", is_from_me=True, minutes_ago=2)]
    _patch_recent(monkeypatch, tmp_path, messages)
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "Hey are we still on for Friday?"])

    assert result.exit_code != 0
    assert calls == []


def test_duplicate_guard_no_recent_messages_never_blocks(monkeypatch, tmp_path):
    _patch_recent(monkeypatch, tmp_path, [])
    calls = _patch_send(monkeypatch)

    result = runner.invoke(main.app, ["send", RECIPIENT, "anything at all"])

    assert result.exit_code == 0
    assert calls == [(RECIPIENT, "anything at all")]


def test_find_duplicate_outbound_exact_match_after_normalization_short_circuits(monkeypatch):
    """Two strings that normalize identically (differ only in case and
    whitespace) hit the exact-match branch directly — this always implies a
    difflib ratio of 1.0, but exercises that code path explicitly rather
    than relying on the ratio fallthrough."""
    msg = db.Message(is_from_me=True, timestamp=datetime.now(), text="  HELLO   there  ")
    result = main._find_duplicate_outbound("hello there", [msg])
    assert result is msg


def test_find_duplicate_outbound_returns_none_when_no_messages():
    assert main._find_duplicate_outbound("anything", []) is None
