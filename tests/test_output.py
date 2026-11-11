import os
import stat

import pytest

from slackgif.config import EncodeSpec, resolve_preset
from slackgif.errors import OutputError, UsageError, ValidationError
from slackgif.output import atomic_replace, ensure_safe_paths, validate_output
from slackgif.probe import probe_gif


def test_same_path_refused(make_gif):
    src = make_gif()
    with pytest.raises(UsageError):
        ensure_safe_paths(src, src, force=True)


def test_hardlink_refused(make_gif):
    src = make_gif()
    dst = src.with_name("hard.gif")
    os.link(src, dst)
    with pytest.raises(UsageError):
        ensure_safe_paths(src, dst, force=True)


def test_symlink_refused(make_gif):
    src = make_gif()
    dst = src.with_name("sym.gif")
    os.symlink(src, dst)
    with pytest.raises(UsageError):
        ensure_safe_paths(src, dst, force=True)


def test_existing_output_requires_force(make_gif):
    src = make_gif()
    dst = make_gif("out.gif")
    with pytest.raises(UsageError):
        ensure_safe_paths(src, dst, force=False)
    ensure_safe_paths(src, dst, force=True)


def test_directory_output_refused(make_gif, tmp_path):
    src = make_gif()
    with pytest.raises(UsageError):
        ensure_safe_paths(src, tmp_path, force=True)


def test_atomic_replace_mode_follows_umask(make_gif):
    src = make_gif()
    dst = src.with_name("out.gif")
    old = os.umask(0o022)
    try:
        atomic_replace(src, dst)
    finally:
        os.umask(old)
    assert stat.S_IMODE(dst.stat().st_mode) == 0o644


def test_validation_rejects_wrong_dimensions(make_gif):
    src = make_gif(size=(64, 48))
    info = probe_gif(src)
    preset = resolve_preset(
        "emoji", max_size=None, quality=None, loop=None, fit=None, background=None
    )
    spec = EncodeSpec(width=64, height=48, fps=info.fps, quality=50, loop=0)
    with pytest.raises(ValidationError):
        validate_output(src, info, spec, preset)


def test_failed_replace_preserves_candidate(monkeypatch, make_gif):
    src = make_gif()
    dst = src.with_name("out.gif")
    monkeypatch.setattr(
        "os.replace", lambda *a: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(OutputError) as exc:
        atomic_replace(src, dst)
    assert "disk full" in str(exc.value)
    assert any(src.parent.glob(f".{dst.name}.*.tmp"))


def test_validation_does_not_reject_over_target(make_gif):
    src = make_gif(size=(64, 48))
    info = probe_gif(src)
    preset = resolve_preset(
        "message", max_size=None, quality=None, loop=None, fit=None, background=None
    )
    spec = EncodeSpec(width=64, height=48, fps=info.fps, quality=50, loop=0)
    # Structural validation passes even though a target could be below this size.
    validate_output(src, info, spec, preset)


def test_validation_rejects_wrong_loop(make_gif):
    src = make_gif(frames=3, loop=0)
    info = probe_gif(src)
    preset = resolve_preset(
        "message", max_size=None, quality=None, loop=None, fit=None, background=None
    )
    spec = EncodeSpec(width=64, height=48, fps=info.fps, quality=50, loop=3)
    with pytest.raises(ValidationError):
        validate_output(src, info, spec, preset)


def test_validation_rejects_missing_alpha(make_gif):
    src = make_gif(frames=3, transparency=0)
    out = make_gif("opaque.gif", frames=3)
    info = probe_gif(src)
    preset = resolve_preset(
        "message", max_size=None, quality=None, loop=None, fit=None, background=None
    )
    spec = EncodeSpec(width=64, height=48, fps=info.fps, quality=50, loop=0)
    with pytest.raises(ValidationError):
        validate_output(out, info, spec, preset)
