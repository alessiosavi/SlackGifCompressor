import shutil

import pytest

from slackgif.backends.ffmpeg import FfmpegBackend, quality_to_colors
from slackgif.config import EncodeSpec
from slackgif.filters import build_encode_args, build_palette_args
from slackgif.probe import probe_gif

requires_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_quality_mapping():
    assert quality_to_colors(0) == 16
    assert quality_to_colors(30) == 64
    assert quality_to_colors(50) == 128
    assert quality_to_colors(100) == 256
    assert FfmpegBackend.quality_levels == (0, 10, 30, 50, 75)


def test_command_uses_fps_mode_not_vsync(make_gif):
    spec = EncodeSpec(width=64, height=48, fps=10, quality=50, loop=0)
    args = build_encode_args("ffmpeg", "src.gif", "palette.png", "out.gif", spec, 128)
    assert "-fps_mode" in args
    assert "passthrough" in args
    assert "-vsync" not in args
    palette_args = build_palette_args("ffmpeg", "src.gif", "palette.png", spec, 128)
    assert "palettegen" in " ".join(palette_args)
    assert "-vsync" not in palette_args


def test_background_palette_has_no_transparency():
    spec = EncodeSpec(width=64, height=48, fps=10, quality=50, loop=0, background="#ffffff")
    palette_args = build_palette_args("ffmpeg", "src.gif", "palette.png", spec, 128)
    assert "reserve_transparent=0" in " ".join(palette_args)
    encode_args = build_encode_args("ffmpeg", "src.gif", "palette.png", "out.gif", spec, 128)
    assert "alpha_threshold" not in " ".join(encode_args)


@requires_ffmpeg
def test_encode_preserves_dimensions_and_loop(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=4, loop=3)
    info = probe_gif(src)
    backend = FfmpegBackend(tmp_path / "work")
    out = tmp_path / "out.gif"
    backend.encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=3),
        info,
    )
    got = probe_gif(out)
    assert (got.width, got.height) == (64, 48)
    assert got.loop == 3


@requires_ffmpeg
def test_transparency(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=4, transparency=0)
    info = probe_gif(src)
    backend = FfmpegBackend(tmp_path / "work")
    out = tmp_path / "out.gif"
    backend.encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=0),
        info,
    )
    assert probe_gif(out).has_alpha is True


@requires_ffmpeg
def test_fixed_canvas_contains_not_stretches(make_gif, tmp_path):
    from PIL import Image

    src = make_gif(size=(300, 200), frames=2, transparency=0)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    spec = EncodeSpec(
        width=128,
        height=128,
        fps=info.fps,
        quality=50,
        loop=0,
        fit="contain",
        canvas=(128, 128),
    )
    FfmpegBackend(tmp_path / "work").encode(src, out, spec, info)
    with Image.open(out) as img:
        rgba = img.convert("RGBA")
        assert rgba.getpixel((64, 2))[3] == 0
        content_band = (rgba.getpixel((x, y))[3] for x in range(128) for y in range(21, 107))
        assert any(alpha > 0 for alpha in content_band)


@requires_ffmpeg
def test_failure_surfaces_stderr(monkeypatch, make_gif, tmp_path):
    from slackgif.errors import BackendError

    script = tmp_path / "ffmpeg"
    script.write_text("#!/bin/sh\necho 'boom: decoder exploded' >&2\nexit 1\n")
    script.chmod(0o755)
    real_which = shutil.which

    def fake_which(name):
        if name == "ffmpeg":
            return str(script)
        return real_which(name)

    monkeypatch.setattr(shutil, "which", fake_which)
    src = make_gif()
    info = probe_gif(src)
    backend = FfmpegBackend(tmp_path / "work")
    with pytest.raises(BackendError) as exc:
        backend.encode(
            src,
            tmp_path / "out.gif",
            EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=0),
            info,
        )
    assert "boom" in str(exc.value)


@requires_ffmpeg
def test_gifsicle_postpass_is_optional(monkeypatch, make_gif, tmp_path):
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "gifsicle" else real_which(name)
    )
    src = make_gif(size=(64, 48), frames=3)
    info = probe_gif(src)
    out = tmp_path / "out.gif"
    FfmpegBackend(tmp_path / "work").encode(
        src,
        out,
        EncodeSpec(width=64, height=48, fps=info.fps, quality=60, loop=0),
        info,
    )
    assert out.exists()
