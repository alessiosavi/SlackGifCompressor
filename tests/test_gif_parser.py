import pytest

from slackgif.errors import ProbeError
from slackgif.probe import parse_gif_metadata


def test_loop_delays_and_variable_timing(make_gif):
    p = make_gif(frames=3, durations=[50, 200, 50], loop=3)
    info = parse_gif_metadata(p)
    assert (info.width, info.height) == (64, 48)
    assert info.frames == 3
    assert info.loop == 3 and info.loop_known is True
    assert info.variable_delays is True
    assert info.duration_s == pytest.approx(0.30, abs=0.02)


def test_no_loop_extension(make_gif):
    p = make_gif(frames=2, loop=None)
    info = parse_gif_metadata(p)
    assert info.loop is None and info.loop_known is True


def test_transparency(make_gif):
    p = make_gif(frames=2, transparency=0)
    info = parse_gif_metadata(p)
    assert info.has_alpha is True


def test_opaque_gif_has_no_alpha(make_gif):
    p = make_gif(frames=2)
    info = parse_gif_metadata(p)
    assert info.has_alpha is False


def test_truncated_gif(tmp_path):
    p = tmp_path / "bad.gif"
    p.write_bytes(b"GIF89a" + b"\x00" * 10)
    with pytest.raises(ProbeError):
        parse_gif_metadata(p)


def test_non_gif_header(tmp_path):
    p = tmp_path / "not.gif"
    p.write_bytes(b"PNG\x00" + b"\x00" * 20)
    with pytest.raises(ProbeError):
        parse_gif_metadata(p)
