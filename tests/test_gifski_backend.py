import shutil

import pytest

from slackgif.backends.gifski import GifskiBackend
from slackgif.config import EncodeSpec, resolve_preset
from slackgif.probe import probe_gif

requires_gifski = pytest.mark.skipif(
    shutil.which("gifski") is None, reason="gifski not installed"
)
requires_gifski_frames = pytest.mark.skipif(
    shutil.which("gifski") is None or shutil.which("ffmpeg") is None,
    reason="gifski + ffmpeg required for the frame path",
)


@requires_gifski
def test_direct_encode(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=4, loop=3)
    info = probe_gif(src)
    backend = GifskiBackend(tmp_path / "work")
    out = tmp_path / "out.gif"
    spec = EncodeSpec(width=64, height=48, fps=info.fps, quality=80, loop=3)
    backend.encode(src, out, spec, info)
    assert out.exists()
    got = probe_gif(out)
    assert (got.width, got.height) == (64, 48)
    assert got.loop == 3


@requires_gifski_frames
def test_fps_change_uses_frames(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=10, durations=[100] * 10)
    info = probe_gif(src)
    backend = GifskiBackend(tmp_path / "work")
    spec = EncodeSpec(width=64, height=48, fps=5, quality=70, loop=0)
    backend.encode(src, tmp_path / "out.gif", spec, info)
    assert len(backend._frame_cache) == 1


@requires_gifski_frames
def test_fixed_canvas_pads_and_preserves_alpha(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=4, transparency=0)
    info = probe_gif(src)
    backend = GifskiBackend(tmp_path / "work")
    spec = EncodeSpec(
        width=128,
        height=128,
        fps=5,
        quality=50,
        loop=0,
        fit="contain",
        canvas=(128, 128),
    )
    backend.encode(src, tmp_path / "out.gif", spec, info)
    got = probe_gif(tmp_path / "out.gif")
    assert (got.width, got.height) == (128, 128)
    assert got.has_alpha is True


def test_can_handle_requires_ffmpeg_for_frames(monkeypatch, make_gif, tmp_path):
    src = make_gif()
    info = probe_gif(src)
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/gifski" if name == "gifski" else None
    )
    backend = GifskiBackend(tmp_path / "work")
    preset = resolve_preset(
        "emoji", max_size=None, quality=None, loop=None, fit=None, background=None
    )
    assert backend.can_handle(info, preset) is False


@requires_gifski_frames
def test_fixed_canvas_contains_not_stretches(make_gif, tmp_path):
    from PIL import Image

    src = make_gif(size=(300, 200), frames=2, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    spec = EncodeSpec(
        width=128,
        height=128,
        fps=5,
        quality=50,
        loop=0,
        fit="contain",
        canvas=(128, 128),
    )
    GifskiBackend(tmp_path / "work").encode(src, out, spec, info)
    with Image.open(out) as img:
        rgba = img.convert("RGBA")
        assert rgba.getpixel((64, 2))[3] == 0  # padded top band
        content_band = (
            rgba.getpixel((x, y))[3] for x in range(128) for y in range(21, 107)
        )
        assert any(alpha > 0 for alpha in content_band)


@requires_gifski_frames
def test_background_forces_frame_path(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=4, transparency=0)
    info = probe_gif(src)
    backend = GifskiBackend(tmp_path / "work")
    spec = EncodeSpec(
        width=64, height=48, fps=info.fps, quality=50, loop=0, background="#ffffff"
    )
    backend.encode(src, tmp_path / "out.gif", spec, info)
    assert len(backend._frame_cache) == 1
    assert probe_gif(tmp_path / "out.gif").has_alpha is False


@requires_gifski_frames
def test_max_duration_forces_frame_path(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=10, durations=[100] * 10)
    info = probe_gif(src)
    backend = GifskiBackend(tmp_path / "work")
    spec = EncodeSpec(
        width=64, height=48, fps=info.fps, quality=50, loop=0, max_duration_s=0.5
    )
    backend.encode(src, tmp_path / "out.gif", spec, info)
    assert len(backend._frame_cache) == 1
    assert probe_gif(tmp_path / "out.gif").duration_s == pytest.approx(0.5, abs=0.11)
