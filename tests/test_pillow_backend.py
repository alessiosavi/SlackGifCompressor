import pytest
from PIL import Image

from slackgif.backends.pillow import PillowBackend
from slackgif.config import EncodeSpec
from slackgif.probe import probe_gif


def test_quality_levels():
    assert PillowBackend.quality_levels == (0, 10, 30, 50, 75)


def test_alpha_is_preserved(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=4, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=0),
        info,
    )
    with Image.open(out) as img:
        alpha = img.convert("RGBA").getchannel("A")
        assert alpha.getextrema() == (0, 255)


def test_variable_delays_do_not_speed_up(make_gif, tmp_path):
    src = make_gif(frames=4, durations=[50, 200, 50, 200])
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src, out, EncodeSpec(width=64, height=48, fps=5, quality=60, loop=0), info
    )
    assert probe_gif(out).duration_s == pytest.approx(info.duration_s, rel=0.10)


def test_extreme_aspect_does_not_crash(make_gif, tmp_path):
    src = make_gif(size=(1000, 2), frames=2)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(width=100, height=2, fps=info.fps, quality=40, loop=0),
        info,
    )
    assert probe_gif(out).height >= 1


def test_loop_once_from_loop_zero(make_gif, tmp_path):
    src = make_gif(frames=3, loop=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=None),
        info,
    )
    assert probe_gif(out).loop is None  # explicit loop=None, not inherited 0


def test_max_duration_truncates(make_gif, tmp_path):
    src = make_gif(frames=10, durations=[100] * 10)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(
            width=64,
            height=48,
            fps=info.fps,
            quality=60,
            loop=0,
            max_duration_s=0.5,
        ),
        info,
    )
    assert probe_gif(out).duration_s == pytest.approx(0.5, abs=0.11)


def test_cover_fills_canvas(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=2, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(
            width=128,
            height=128,
            fps=info.fps,
            quality=50,
            loop=0,
            fit="cover",
            canvas=(128, 128),
        ),
        info,
    )
    got = probe_gif(out)
    assert (got.width, got.height) == (128, 128)


def test_background_flattens_non_canvas(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=2, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(
            width=64,
            height=48,
            fps=info.fps,
            quality=60,
            loop=0,
            background="#ffffff",
        ),
        info,
    )
    assert probe_gif(out).has_alpha is False


def test_alpha_remap_keeps_opaque_regions(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=2, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=0),
        info,
    )
    with Image.open(out) as img:
        rgba = img.convert("RGBA")
        opaque = sum(
            1 for x in range(64) for y in range(48) if rgba.getpixel((x, y))[3] == 255
        )
        assert opaque > 0


def test_contain_pads_canvas(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=2, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(
            width=128,
            height=128,
            fps=info.fps,
            quality=50,
            loop=0,
            fit="contain",
            canvas=(128, 128),
        ),
        info,
    )
    with Image.open(out) as img:
        rgba = img.convert("RGBA")
        assert rgba.getpixel((64, 2))[3] == 0


def test_loop_3_preserved(make_gif, tmp_path):
    src = make_gif(frames=3, loop=3)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    PillowBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=3),
        info,
    )
    assert probe_gif(out).loop == 3
