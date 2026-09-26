"""Tests for wa_cli.config: repo-root discovery, state-dir resolution, paths."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wa_cli import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure no ambient WA_REPO/XDG_STATE_HOME/HOME leaks into a test."""
    monkeypatch.delenv("WA_REPO", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)


# --- repo_root() ---


def test_repo_root_resolves_dir_containing_whatsapp_bridge():
    root = config.repo_root()
    assert (root / "whatsapp-bridge").is_dir()


def test_repo_root_honors_wa_repo_env_override(tmp_path, monkeypatch):
    fake_root = tmp_path / "fake-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    root = config.repo_root()

    assert root == fake_root


def test_repo_root_wa_repo_override_wins_even_if_invalid_path_shape(tmp_path, monkeypatch):
    # WA_REPO always wins per spec, even pointing somewhere unusual, as long as it exists.
    fake_root = tmp_path / "another-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.repo_root() == fake_root


# --- state_dir() ---


def test_state_dir_uses_xdg_state_home_when_set(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result == xdg / "wa-cli"


def test_state_dir_defaults_to_home_dot_wa_cli(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    result = config.state_dir()

    assert result == tmp_path / ".wa-cli"


def test_state_dir_is_created_on_disk(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result.is_dir()


def test_state_dir_creation_is_idempotent(tmp_path, monkeypatch):
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    first = config.state_dir()
    second = config.state_dir()

    assert first == second
    assert second.is_dir()


def test_state_dir_is_outside_repo_tree(tmp_path, monkeypatch):
    """Critic amendment A3(a): the resolved state dir must never live inside the
    repo working tree (e.g. must not resolve under wa-cli/ or the repo root),
    since it holds runtime pidfiles/logs that must not be committed or confused
    with tracked source."""
    xdg = tmp_path / "xdg-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()
    repo_root = config.repo_root()

    assert repo_root not in result.parents
    assert result != repo_root
    # also verify against the real on-disk repo root independent of env fakery
    real_repo_root = importlib.import_module("wa_cli.config").__file__
    assert str(result).startswith(str(tmp_path))
    assert not str(result).startswith(str(os.path.dirname(os.path.dirname(real_repo_root))))


# --- derived state-dir paths ---


def test_bridge_pidfile_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_pidfile() == config.state_dir() / "bridge.pid"


def test_bridge_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_log() == config.state_dir() / "bridge.log"


def test_mcp_pidfile_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.mcp_pidfile() == config.state_dir() / "mcp.pid"


def test_mcp_log_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.mcp_log() == config.state_dir() / "mcp.log"


# --- bridge_binary() / DB paths / bridge_url() ---


def test_bridge_binary_path(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    result = config.bridge_binary()

    assert result == fake_root / "whatsapp-bridge" / "whatsapp-bridge"


def test_contacts_db_path(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    result = config.contacts_db()

    assert result == fake_root / "whatsapp-bridge" / "store" / "whatsapp.db"


def test_messages_db_path(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    result = config.messages_db()

    assert result == fake_root / "whatsapp-bridge" / "store" / "messages.db"


def test_bridge_url_constant():
    assert config.bridge_url() == "http://localhost:8080"


def test_state_dir_falls_back_to_path_home_when_home_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    monkeypatch.setattr(config.Path, "home", classmethod(lambda cls: tmp_path))

    result = config.state_dir()

    assert result == tmp_path / ".wa-cli"
    assert result.is_dir()


# --- repo_root(): WA_REPO edge cases ---


def test_repo_root_wa_repo_empty_string_falls_through_to_walk_up(monkeypatch):
    # os.environ.get("WA_REPO") returns "" which is falsy, so `if override:`
    # does NOT take the override branch -- it falls through to the walk-up
    # logic instead of returning a broken empty Path. This is the documented
    # (if implicit) behavior; verify it explicitly.
    monkeypatch.setenv("WA_REPO", "")

    root = config.repo_root()

    assert (root / "whatsapp-bridge").is_dir()


def test_repo_root_wa_repo_whitespace_only_returned_literally(monkeypatch):
    # Unlike "", a whitespace-only string IS truthy, so it takes the override
    # branch and is returned as a literal (broken) relative Path -- no
    # stripping, no validation. Documenting current behavior, not asserting
    # it is desirable.
    monkeypatch.setenv("WA_REPO", "   ")

    assert config.repo_root() == Path("   ")


def test_repo_root_wa_repo_relative_path_returned_as_is(monkeypatch):
    monkeypatch.setenv("WA_REPO", "relative/repo/path")

    assert config.repo_root() == Path("relative/repo/path")
    assert not config.repo_root().is_absolute()


def test_repo_root_wa_repo_trailing_slash_normalized(tmp_path, monkeypatch):
    fake_root = tmp_path / "trailing-slash-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root) + "/")

    assert config.repo_root() == fake_root


def test_repo_root_wa_repo_with_spaces(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo with spaces"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.repo_root() == fake_root


def test_repo_root_wa_repo_with_unicode(tmp_path, monkeypatch):
    fake_root = tmp_path / "リポジトリ-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.repo_root() == fake_root


def test_repo_root_wa_repo_nonexistent_path_not_validated(tmp_path, monkeypatch):
    # repo_root() does not check existence of a WA_REPO override -- it just
    # returns Path(override). Assert that documented behavior explicitly.
    missing = tmp_path / "does-not-exist-at-all"
    assert not missing.exists()
    monkeypatch.setenv("WA_REPO", str(missing))

    assert config.repo_root() == missing


def test_repo_root_wa_repo_pointing_to_file_not_directory(tmp_path, monkeypatch):
    a_file = tmp_path / "im-a-file.txt"
    a_file.write_text("not a repo")
    monkeypatch.setenv("WA_REPO", str(a_file))

    assert config.repo_root() == a_file


def test_repo_root_wa_repo_with_double_internal_slashes_collapsed(tmp_path, monkeypatch):
    fake_root = tmp_path / "x" / "y"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    override = f"{tmp_path}//x//y"
    monkeypatch.setenv("WA_REPO", override)

    assert config.repo_root() == fake_root


def test_repo_root_wa_repo_dot_segments_not_normalized(tmp_path, monkeypatch):
    # Path() does not call os.path.normpath / resolve() -- "." and ".."
    # segments in a WA_REPO override are preserved literally, not collapsed.
    override = str(tmp_path / "x" / "." / "y" / ".." / "y")
    monkeypatch.setenv("WA_REPO", override)

    assert config.repo_root() == Path(override)
    assert ".." in config.repo_root().parts


def test_repo_root_wa_repo_override_returns_path_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_REPO", str(tmp_path))

    result = config.repo_root()

    assert isinstance(result, Path)
    assert not isinstance(result, str)


def test_repo_root_wa_repo_override_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("WA_REPO", str(tmp_path))

    assert config.repo_root() == config.repo_root()


def test_repo_root_raises_runtime_error_when_no_whatsapp_bridge_dir_found(monkeypatch):
    monkeypatch.delenv("WA_REPO", raising=False)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)

    with pytest.raises(RuntimeError, match="could not locate repo root"):
        config.repo_root()


def test_repo_root_runtime_error_message_mentions_wa_repo_as_the_fix(monkeypatch):
    monkeypatch.delenv("WA_REPO", raising=False)
    monkeypatch.setattr(Path, "is_dir", lambda self: False)

    with pytest.raises(RuntimeError, match="WA_REPO"):
        config.repo_root()


# --- state_dir(): XDG_STATE_HOME / HOME edge cases ---


def test_state_dir_xdg_state_home_empty_string_falls_back_to_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))

    result = config.state_dir()

    assert result == tmp_path / ".wa-cli"


def test_state_dir_xdg_state_home_whitespace_only_used_literally(tmp_path, monkeypatch):
    # Whitespace-only IS truthy, so it's used as a literal (relative)
    # directory name. chdir into tmp_path first so the mkdir it triggers
    # can't escape the sandbox.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", "   ")

    result = config.state_dir()

    assert result == Path("   ") / "wa-cli"
    assert (tmp_path / "   " / "wa-cli").is_dir()


def test_state_dir_xdg_state_home_relative_path_resolved_against_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")

    result = config.state_dir()

    assert result == Path("relative-state") / "wa-cli"
    assert (tmp_path / "relative-state" / "wa-cli").is_dir()


def test_state_dir_xdg_state_home_trailing_slash_no_double_slash(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path) + "/")

    result = config.state_dir()

    assert result == tmp_path / "wa-cli"
    assert "//" not in str(result)


def test_state_dir_xdg_state_home_with_spaces(tmp_path, monkeypatch):
    xdg = tmp_path / "state with spaces"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result == xdg / "wa-cli"
    assert result.is_dir()


def test_state_dir_xdg_state_home_with_unicode(tmp_path, monkeypatch):
    xdg = tmp_path / "状態ディレクトリ"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result == xdg / "wa-cli"
    assert result.is_dir()


def test_state_dir_xdg_state_home_dot_segments_not_normalized(tmp_path, monkeypatch):
    override = str(tmp_path / "a" / "." / "b" / ".." / "b")
    monkeypatch.setenv("XDG_STATE_HOME", override)

    result = config.state_dir()

    assert result == Path(override) / "wa-cli"
    assert ".." in result.parts


def test_state_dir_xdg_wins_over_home_when_both_set(tmp_path, monkeypatch):
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result == xdg / "wa-cli"
    assert result != home / ".wa-cli"


def test_state_dir_home_with_trailing_slash_no_double_slash(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path) + "/")

    result = config.state_dir()

    assert result == tmp_path / ".wa-cli"
    assert "//" not in str(result)


def test_state_dir_return_type_is_pathlib_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert isinstance(config.state_dir(), Path)


def test_state_dir_idempotent_across_many_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    results = [config.state_dir() for _ in range(50)]

    assert all(r == results[0] for r in results)
    assert results[0].is_dir()


def test_state_dir_no_stale_caching_across_differing_env(tmp_path, monkeypatch):
    # Nothing in state_dir() should memoize a result -- changing the env
    # between calls must change the returned path each time.
    first_xdg = tmp_path / "first"
    second_xdg = tmp_path / "second"

    monkeypatch.setenv("XDG_STATE_HOME", str(first_xdg))
    first = config.state_dir()

    monkeypatch.setenv("XDG_STATE_HOME", str(second_xdg))
    second = config.state_dir()

    assert first != second
    assert first == first_xdg / "wa-cli"
    assert second == second_xdg / "wa-cli"


def test_state_dir_creates_missing_intermediate_parents(tmp_path, monkeypatch):
    xdg = tmp_path / "does" / "not" / "exist" / "yet"
    assert not xdg.exists()
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result.is_dir()
    assert xdg.is_dir()


def test_state_dir_tolerates_preexisting_empty_target_dir(tmp_path, monkeypatch):
    xdg = tmp_path / "preexisting"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    (xdg / "wa-cli").mkdir(parents=True)

    result = config.state_dir()

    assert result == xdg / "wa-cli"
    assert result.is_dir()


def test_state_dir_raises_file_exists_error_when_target_is_a_file(tmp_path, monkeypatch):
    # mkdir(parents=True, exist_ok=True) only tolerates the target already
    # existing AS A DIRECTORY. If a plain file sits at that exact path,
    # it raises FileExistsError -- verify the actual behavior, don't assume.
    xdg = tmp_path / "blocked"
    xdg.mkdir()
    (xdg / "wa-cli").write_text("not a directory")
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    with pytest.raises(FileExistsError):
        config.state_dir()


def test_state_dir_follows_symlinked_xdg_state_home(tmp_path, monkeypatch):
    real_dir = tmp_path / "real-state"
    real_dir.mkdir()
    link = tmp_path / "state-link"
    link.symlink_to(real_dir)
    monkeypatch.setenv("XDG_STATE_HOME", str(link))

    result = config.state_dir()

    assert result == link / "wa-cli"
    assert (real_dir / "wa-cli").is_dir()


# --- derived path invariants ---


def test_bridge_pidfile_and_bridge_log_share_state_dir_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_pidfile().parent == config.bridge_log().parent == config.state_dir()


def test_mcp_pidfile_and_mcp_log_share_state_dir_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.mcp_pidfile().parent == config.mcp_log().parent == config.state_dir()


def test_bridge_binary_contacts_db_messages_db_share_whatsapp_bridge_ancestor(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))
    bridge_dir = fake_root / "whatsapp-bridge"

    assert config.bridge_binary().parent == bridge_dir
    assert bridge_dir in config.contacts_db().parents
    assert bridge_dir in config.messages_db().parents


def test_contacts_db_and_messages_db_are_siblings_in_store_dir(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.contacts_db().parent == config.messages_db().parent
    assert config.contacts_db().parent.name == "store"


@pytest.mark.parametrize(
    "dirname",
    [
        "state with spaces",
        "状態ディレクトリ",
        "state-normal",
        "state.dot",
        "state_underscore",
    ],
)
def test_derived_paths_end_to_end_with_unusual_dir_names(dirname, tmp_path, monkeypatch):
    xdg = tmp_path / dirname
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))
    fake_root = tmp_path / f"{dirname}-repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.bridge_pidfile() == xdg / "wa-cli" / "bridge.pid"
    assert config.bridge_log() == xdg / "wa-cli" / "bridge.log"
    assert config.mcp_pidfile() == xdg / "wa-cli" / "mcp.pid"
    assert config.mcp_log() == xdg / "wa-cli" / "mcp.log"
    assert config.bridge_binary() == fake_root / "whatsapp-bridge" / "whatsapp-bridge"
    assert config.contacts_db() == fake_root / "whatsapp-bridge" / "store" / "whatsapp.db"
    assert config.messages_db() == fake_root / "whatsapp-bridge" / "store" / "messages.db"


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.text(min_size=1, max_size=12).filter(lambda s: "\x00" not in s))
def test_state_dir_derived_paths_are_always_state_dir_slash_fixed_name(name, tmp_path, monkeypatch):
    fake_state_dir = tmp_path / name
    monkeypatch.setattr(config, "state_dir", lambda: fake_state_dir)

    assert config.bridge_pidfile() == fake_state_dir / "bridge.pid"
    assert config.bridge_log() == fake_state_dir / "bridge.log"
    assert config.mcp_pidfile() == fake_state_dir / "mcp.pid"
    assert config.mcp_log() == fake_state_dir / "mcp.log"


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(name=st.text(min_size=1, max_size=12).filter(lambda s: "\x00" not in s))
def test_repo_root_derived_paths_are_always_repo_root_slash_whatsapp_bridge(name, tmp_path, monkeypatch):
    fake_repo_root = tmp_path / name
    monkeypatch.setattr(config, "repo_root", lambda: fake_repo_root)

    assert config.bridge_binary() == fake_repo_root / "whatsapp-bridge" / "whatsapp-bridge"
    assert config.contacts_db() == fake_repo_root / "whatsapp-bridge" / "store" / "whatsapp.db"
    assert config.messages_db() == fake_repo_root / "whatsapp-bridge" / "store" / "messages.db"


# --- bridge_url() ---


def test_bridge_url_returns_str_type():
    assert isinstance(config.bridge_url(), str)


def test_bridge_url_matches_module_level_constant():
    assert config.bridge_url() == config.BRIDGE_URL


# --- more WA_REPO / XDG_STATE_HOME character + shape edge cases ---


def test_repo_root_wa_repo_with_newline_character(tmp_path, monkeypatch):
    override = str(tmp_path / "repo\nwith-newline")
    monkeypatch.setenv("WA_REPO", override)

    assert config.repo_root() == Path(override)


def test_repo_root_wa_repo_with_tab_character(tmp_path, monkeypatch):
    override = str(tmp_path / "repo\twith-tab")
    monkeypatch.setenv("WA_REPO", override)

    assert config.repo_root() == Path(override)


def test_repo_root_wa_repo_very_long_path(tmp_path, monkeypatch):
    long_name = "x" * 200
    fake_root = tmp_path / long_name
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.repo_root() == fake_root


def test_repo_root_wa_repo_with_backslash_is_literal_filename_char_on_posix(tmp_path, monkeypatch):
    # POSIX has no path-separator meaning for "\\" -- it's just a character
    # in a single path component, unlike on Windows.
    override = str(tmp_path / "repo\\with-backslash")
    monkeypatch.setenv("WA_REPO", override)

    assert config.repo_root() == Path(override)


def test_state_dir_xdg_state_home_long_path(tmp_path, monkeypatch):
    xdg = tmp_path / ("y" * 100)
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    result = config.state_dir()

    assert result == xdg / "wa-cli"
    assert result.is_dir()


def test_state_dir_xdg_state_home_internal_double_slashes_collapsed(tmp_path, monkeypatch):
    nested = tmp_path / "a" / "b"
    override = f"{tmp_path}//a//b"
    monkeypatch.setenv("XDG_STATE_HOME", override)

    result = config.state_dir()

    assert result == nested / "wa-cli"


def test_state_dir_result_is_absolute_when_xdg_state_home_is_absolute(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.state_dir().is_absolute()


def test_state_dir_recreates_dir_after_manual_deletion(tmp_path, monkeypatch):
    # No caching: if the directory is removed between calls, the next call
    # must recreate it rather than returning a stale/missing path.
    xdg = tmp_path / "recreate-me"
    monkeypatch.setenv("XDG_STATE_HOME", str(xdg))

    first = config.state_dir()
    assert first.is_dir()
    first.rmdir()
    assert not first.exists()

    second = config.state_dir()

    assert second == first
    assert second.is_dir()


# --- derived-path distinctness / type invariants ---


def test_bridge_pidfile_distinct_from_mcp_pidfile(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_pidfile() != config.mcp_pidfile()


def test_bridge_log_distinct_from_mcp_log(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert config.bridge_log() != config.mcp_log()


def test_contacts_db_distinct_from_messages_db(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    assert config.contacts_db() != config.messages_db()


def test_all_state_derived_paths_are_pathlib_path_instances(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    for fn in (config.bridge_pidfile, config.bridge_log, config.mcp_pidfile, config.mcp_log):
        assert isinstance(fn(), Path)


def test_all_repo_derived_paths_are_pathlib_path_instances(tmp_path, monkeypatch):
    fake_root = tmp_path / "repo"
    (fake_root / "whatsapp-bridge").mkdir(parents=True)
    monkeypatch.setenv("WA_REPO", str(fake_root))

    for fn in (config.bridge_binary, config.contacts_db, config.messages_db):
        assert isinstance(fn(), Path)
