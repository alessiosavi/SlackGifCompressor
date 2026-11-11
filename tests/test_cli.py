import json
import os
import subprocess
import sys

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
    assert (
        __import__("slackgif.probe", fromlist=["probe_gif"]).probe_gif(dst).loop is None
    )


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
