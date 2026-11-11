import shutil

import pytest

import slackgif.probe as probe
from slackgif.errors import ProbeError
from slackgif.probe import probe_gif


def test_probe_returns_metadata(make_gif):
    p = make_gif(frames=4, loop=0)
    info = probe_gif(p)
    assert info.frames == 4
    assert info.loop == 0
    assert info.size_bytes == p.stat().st_size


def test_empty_file_raises(tmp_path):
    p = tmp_path / "empty.gif"
    p.write_bytes(b"")
    with pytest.raises(ProbeError):
        probe_gif(p)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ProbeError):
        probe_gif(tmp_path / "missing.gif")


def test_pillow_fallback_when_parser_fails(monkeypatch, make_gif):
    p = make_gif(frames=4, loop=2)

    def broken(path):
        raise ProbeError("parser unavailable")

    monkeypatch.setattr(probe, "parse_gif_metadata", broken)
    info = probe.probe_gif(p)
    assert info.frames == 4
    assert info.loop == 2
    assert info.loop_known is True


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_ffprobe_fallback_when_parser_and_pillow_fail(monkeypatch, make_gif):
    p = make_gif(frames=2, loop=0)

    def broken(path):
        raise ProbeError("parser unavailable")

    monkeypatch.setattr(probe, "parse_gif_metadata", broken)
    monkeypatch.setattr(
        probe,
        "_probe_with_pillow",
        lambda path: (_ for _ in ()).throw(ProbeError("no pillow")),
    )
    info = probe.probe_gif(p)
    assert info.frames >= 1
    assert info.width == 64 and info.height == 48
    assert info.loop_known is False
