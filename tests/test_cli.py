import json
import os
import shutil
import subprocess
import sys

import pytest

from slackgif.cli import main


def test_version(capsys):
    assert main(["--version"]) == 0
    assert "2.0.0" in capsys.readouterr().out


def test_help():
    assert main(["--help"]) == 0


def test_dry_run_writes_nothing(make_gif, tmp_path, capsys):
    src = make_gif()
    dst = tmp_path / "out.gif"
    assert main([str(src), "--dry-run", "-o", str(dst)]) == 0
    assert not dst.exists()
    assert "preset" in capsys.readouterr().out.lower()


def test_invalid_target(make_gif):
    assert main([str(make_gif()), "-t", "0"]) == 2


def test_custom_requires_target(make_gif):
    assert main([str(make_gif()), "--preset", "custom"]) == 2


def test_same_input_output_refused(make_gif):
    src = make_gif()
    assert main([str(src), "-o", str(src)]) == 2


def test_existing_output_requires_force(make_gif):
    src = make_gif()
    dst = make_gif("out.gif")
    assert main([str(src), "-o", str(dst)]) == 2
    assert main([str(src), "-o", str(dst), "--force"]) == 0


def test_json_schema(make_gif, tmp_path, capsys):
    src = make_gif()
    assert main([str(src), "--json", "-o", str(tmp_path / "out.gif")]) == 0
    data = json.loads(capsys.readouterr().out)
    assert set(data) >= {
        "input",
        "output",
        "success",
        "target_bytes",
        "output_bytes",
        "width",
        "height",
        "fps",
        "frames",
        "quality",
        "encodes",
        "seconds",
        "warnings",
        "error",
    }


def test_ascii_stdout():
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    proc = subprocess.run(
        [sys.executable, "-m", "slackgif", "--help"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0


def test_quality_alone(make_gif, tmp_path):
    dst = tmp_path / "out.gif"
    assert main([str(make_gif()), "--quality", "80", "-o", str(dst)]) == 0
    assert dst.exists()


def test_quality_with_target(make_gif, tmp_path):
    dst = tmp_path / "out.gif"
    assert main([str(make_gif()), "--quality", "80", "-t", "1MB", "-o", str(dst)]) in (
        0,
        1,
    )


def test_deferred_flags_return_2(make_gif):
    src = make_gif()
    assert main([str(src), "--trim", "0-1"]) == 2
    assert main([str(src), "--output-dir", "out"]) == 2


def test_background_conflict(make_gif):
    src = make_gif()
    assert main([str(src), "--background", "white", "--keep-transparency"]) == 2


def test_exception_mapping(monkeypatch, make_gif):
    import slackgif.cli as cli
    from slackgif.errors import ProbeError

    monkeypatch.setattr(
        cli,
        "probe_gif",
        lambda *a, **k: (_ for _ in ()).throw(ProbeError("bad")),
    )
    assert main([str(make_gif())]) == 3


def test_loop_once_from_loop_zero(make_gif, tmp_path):
    src = make_gif(frames=3, loop=0)
    dst = tmp_path / "out.gif"
    assert main([str(src), "--loop", "once", "-o", str(dst)]) == 0
    assert __import__("slackgif.probe", fromlist=["probe_gif"]).probe_gif(dst).loop is None


def test_large_input_rejected_without_flag(monkeypatch, make_gif):
    import slackgif.cli as cli
    from slackgif.config import GifInfo

    huge = GifInfo(
        width=100,
        height=100,
        frames=20_000,
        fps=10.0,
        duration_s=2000.0,
        loop=0,
        loop_known=True,
        has_alpha=False,
        variable_delays=False,
        size_bytes=1_000_000,
    )
    monkeypatch.setattr(cli, "probe_gif", lambda *a, **k: huge)
    assert main([str(make_gif())]) == 2  # UsageError from validate_limits


def test_output_error_is_exit_3(monkeypatch, make_gif):
    import slackgif.search as search
    from slackgif.errors import OutputError

    monkeypatch.setattr(
        search,
        "atomic_replace",
        lambda *a: (_ for _ in ()).throw(OutputError("disk full")),
    )
    assert main([str(make_gif())]) == 3


def test_max_duration_trims(make_gif, tmp_path):
    src = make_gif(frames=10, durations=[100] * 10)
    dst = tmp_path / "out.gif"
    assert main([str(src), "--max-duration", "0.5", "-o", str(dst)]) == 0
    assert __import__("slackgif.probe", fromlist=["probe_gif"]).probe_gif(
        dst
    ).duration_s == __import__("pytest").approx(0.5, abs=0.11)


def test_max_duration_zero_rejected(make_gif):
    assert main([str(make_gif()), "--max-duration", "0"]) == 2


def test_min_width_and_min_fps_accepted(make_gif, tmp_path):
    dst = tmp_path / "out.gif"
    assert (
        main(
            [
                str(make_gif()),
                "--min-width",
                "24",
                "--min-fps",
                "2",
                "-t",
                "500KB",
                "-o",
                str(dst),
            ]
        )
        == 0
    )


def test_unicode_path_ascii(tmp_path, make_gif):
    src = make_gif("caf\u00e9.gif")
    dst = tmp_path / "out.gif"
    env = dict(os.environ, PYTHONIOENCODING="ascii")
    proc = subprocess.run(
        [sys.executable, "-m", "slackgif", str(src), "-o", str(dst)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert proc.returncode == 0
    assert dst.exists()


def test_json_error_object(monkeypatch, make_gif, capsys):
    import slackgif.cli as cli
    from slackgif.errors import ProbeError

    monkeypatch.setattr(cli, "probe_gif", lambda *a, **k: (_ for _ in ()).throw(ProbeError("bad")))
    assert main([str(make_gif()), "--json"]) == 3
    data = json.loads(capsys.readouterr().out)
    assert data["success"] is False
    assert "bad" in data["error"]


def test_cli_hardlink_refused(make_gif):
    src = make_gif()
    dst = src.with_name("hard.gif")
    os.link(src, dst)
    assert main([str(src), "-o", str(dst), "--force"]) == 2


@pytest.mark.skipif(
    shutil.which("gifski") is None or shutil.which("ffmpeg") is None,
    reason="gifski and ffmpeg are required for the fallback test",
)
def test_cli_backend_fallback(monkeypatch, make_gif, tmp_path, capsys):
    from slackgif.backends.gifski import GifskiBackend
    from slackgif.errors import BackendError

    def boom(self, *args, **kwargs):
        raise BackendError("gifski exploded")

    monkeypatch.setattr(GifskiBackend, "encode", boom)
    dst = tmp_path / "out.gif"
    assert main([str(make_gif()), "--max-width", "32", "--json", "-o", str(dst)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["backend"] == "ffmpeg"
    assert dst.exists()


def test_variable_delay_warning(make_gif, tmp_path, capsys):
    src = make_gif(frames=4, durations=[50, 200, 50, 200])
    dst = tmp_path / "out.gif"
    assert main([str(src), "--max-width", "32", "--json", "-o", str(dst)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any("variable frame delays" in warning for warning in data["warnings"])


@pytest.mark.skipif(
    shutil.which("gifski") is None or shutil.which("ffmpeg") is None,
    reason="gifski + ffmpeg required for the frame path",
)
def test_static_emoji_gifski(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=1, durations=[100], transparency=0)
    dst = tmp_path / "emoji.gif"
    assert main([str(src), "--preset", "emoji", "--backend", "gifski", "-o", str(dst)]) == 0
    info = __import__("slackgif.probe", fromlist=["probe_gif"]).probe_gif(dst)
    assert (info.width, info.height) == (128, 128)
    assert info.frames >= 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_static_emoji_ffmpeg(make_gif, tmp_path):
    src = make_gif(size=(300, 200), frames=1, durations=[100], transparency=0)
    dst = tmp_path / "emoji.gif"
    assert main([str(src), "--preset", "emoji", "--backend", "ffmpeg", "-o", str(dst)]) == 0
    assert __import__("slackgif.probe", fromlist=["probe_gif"]).probe_gif(dst).frames >= 1


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_static_background_does_not_hang(make_gif, tmp_path):
    src = make_gif(size=(64, 48), frames=1, durations=[100], transparency=0)
    dst = tmp_path / "out.gif"
    assert (
        main(
            [
                str(src),
                "--backend",
                "ffmpeg",
                "--background",
                "white",
                "--timeout",
                "1",
                "-o",
                str(dst),
            ]
        )
        == 0
    )
