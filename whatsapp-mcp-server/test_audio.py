"""Tests for audio.py's ffmpeg-backed conversion helpers.

Boundary mocked: subprocess.run (the real ffmpeg invocation). Everything else
(file existence checks, output-dir creation, tempfile naming/cleanup) runs for
real against tmp_path so the actual logic is exercised, not just the mock.
"""

from __future__ import annotations

import os
import subprocess

import pytest

import audio


def _fake_completed_process(returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=["ffmpeg"], returncode=returncode, stdout="", stderr=stderr)


@pytest.fixture
def input_file(tmp_path):
    path = tmp_path / "voice.m4a"
    path.write_bytes(b"not real audio, just needs to exist")
    return str(path)


# --- convert_to_opus_ogg -------------------------------------------------------

def test_convert_missing_input_raises_file_not_found(tmp_path):
    missing = str(tmp_path / "nope.m4a")
    with pytest.raises(FileNotFoundError):
        audio.convert_to_opus_ogg(missing)


def test_convert_default_output_replaces_extension(monkeypatch, input_file):
    calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), _fake_completed_process())[1],
    )

    result = audio.convert_to_opus_ogg(input_file)

    assert result == os.path.splitext(input_file)[0] + ".ogg"
    assert calls[0][0] == "ffmpeg"
    assert calls[0][-1] == result


def test_convert_creates_missing_output_directory(monkeypatch, tmp_path, input_file):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _fake_completed_process())
    output_dir = tmp_path / "new_output_dir"
    output_file = str(output_dir / "voice.ogg")

    assert not output_dir.exists()
    result = audio.convert_to_opus_ogg(input_file, output_file=output_file)

    assert result == output_file
    assert output_dir.exists()


def test_convert_passes_bitrate_and_sample_rate(monkeypatch, input_file):
    calls = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: (calls.append(cmd), _fake_completed_process())[1],
    )

    audio.convert_to_opus_ogg(input_file, bitrate="64k", sample_rate=48000)

    cmd = calls[0]
    assert cmd[cmd.index("-b:a") + 1] == "64k"
    assert cmd[cmd.index("-ar") + 1] == "48000"


def test_convert_ffmpeg_failure_raises_runtime_error(monkeypatch, input_file):
    def _raise(cmd, **kw):
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr="ffmpeg: command not found")

    monkeypatch.setattr(subprocess, "run", _raise)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        audio.convert_to_opus_ogg(input_file)


# --- convert_to_opus_ogg_temp ---------------------------------------------------

def test_convert_temp_returns_ogg_suffixed_temp_path(monkeypatch, input_file):
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _fake_completed_process())

    result = audio.convert_to_opus_ogg_temp(input_file)

    assert result.endswith(".ogg")
    assert os.path.exists(result)
    os.unlink(result)


def test_convert_temp_cleans_up_temp_file_on_failure(monkeypatch, input_file):
    created_paths = []

    def _fake_convert(inp, output_file, bitrate, sample_rate):
        created_paths.append(output_file)
        raise RuntimeError("Failed to convert audio. You likely need to install ffmpeg")

    monkeypatch.setattr(audio, "convert_to_opus_ogg", _fake_convert)

    with pytest.raises(RuntimeError, match="ffmpeg"):
        audio.convert_to_opus_ogg_temp(input_file)

    assert created_paths and not os.path.exists(created_paths[0])
