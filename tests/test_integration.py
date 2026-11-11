import os
import shutil

import pytest

from slackgif.cli import main
from slackgif.probe import probe_gif

pytestmark = pytest.mark.integration

requires_backend = pytest.mark.skipif(
    shutil.which("ffmpeg") is None and shutil.which("gifski") is None,
    reason="no GIF backend installed",
)


@requires_backend
def test_message_end_to_end(make_gif, tmp_path):
    src = make_gif(size=(320, 240), frames=10, durations=[100] * 10)
    dst = tmp_path / "out.gif"
    # --max-width forces a non-compliant source, so the search/backend runs.
    code = main([str(src), "-t", "200KB", "--max-width", "100", "-o", str(dst)])
    assert code == 0
    assert dst.exists()
    info = probe_gif(dst)
    assert dst.stat().st_size <= 200 * 1024
    assert info.width <= 100 and info.height <= 240
    assert info.frames >= 1


@requires_backend
def test_emoji_end_to_end(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=60, transparency=0)
    dst = tmp_path / "emoji.gif"
    code = main([str(src), "--preset", "emoji", "-o", str(dst)])
    assert code in (0, 1)
    info = probe_gif(dst)
    assert (info.width, info.height) == (128, 128)
    assert info.frames <= 50
    if code == 0:
        assert dst.stat().st_size <= 128 * 1024
    if dst.stat().st_size <= 128 * 1024:
        assert info.has_alpha is True


def test_compliant_source_copies(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=2)
    dst = tmp_path / "out.gif"
    assert main([str(src), "-o", str(dst)]) == 0
    assert dst.read_bytes() == src.read_bytes()


@requires_backend
def test_background_end_to_end(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=4, transparency=0)
    dst = tmp_path / "out.gif"
    assert main([str(src), "--background", "white", "-o", str(dst)]) == 0
    assert probe_gif(dst).has_alpha is False


@requires_backend
def test_emoji_infeasible_reports_hint(make_gif, tmp_path, capsys):
    src = make_gif(size=(300, 200), frames=60, transparency=0)
    dst = tmp_path / "emoji.gif"
    code = main([str(src), "--preset", "emoji", "-t", "1KB", "-o", str(dst)])
    assert code == 1
    assert dst.exists()
    assert "--max-duration" in capsys.readouterr().err


def test_noncompliant_under_target_reencodes(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=2)
    dst = tmp_path / "out.gif"
    assert main([str(src), "--max-width", "100", "-t", "5MB", "-o", str(dst)]) == 0
    assert probe_gif(dst).width <= 100
    assert dst.stat().st_size <= 5 * 1024 * 1024


def test_ffmpeg_only_fallback(monkeypatch, make_gif, tmp_path):
    real_which = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "gifski" else real_which(name)
    )
    dst = tmp_path / "out.gif"
    assert main(
        [
            str(make_gif(size=(320, 240), frames=10)),
            "-t",
            "200KB",
            "--max-width",
            "120",
            "-o",
            str(dst),
        ]
    ) in (0, 1)
    assert dst.exists()


def test_pillow_only_fallback(monkeypatch, make_gif, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    dst = tmp_path / "out.gif"
    assert main(
        [
            str(make_gif(size=(320, 240), frames=4)),
            "-t",
            "200KB",
            "--max-width",
            "120",
            "-o",
            str(dst),
        ]
    ) in (0, 1)
    assert dst.exists()


@pytest.mark.bench
@pytest.mark.skipif(
    not os.environ.get("SLACKGIF_BENCH_GIF"),
    reason="set SLACKGIF_BENCH_GIF to run the real-GIF benchmark",
)
def test_real_gif_benchmark(tmp_path):
    src = os.environ["SLACKGIF_BENCH_GIF"]
    dst = tmp_path / "message.gif"
    assert main([src, "--preset", "message", "-o", str(dst)]) == 0
    info = probe_gif(dst)
    assert (info.width, info.height) == (500, 625)
    assert dst.stat().st_size <= 5 * 1024 * 1024
