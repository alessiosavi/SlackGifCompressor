from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from slackgif.config import EncodeSpec, GifInfo, Preset
from slackgif.errors import OutputError, ProbeError, UsageError, ValidationError
from slackgif.probe import probe_gif

_GIF_HEADERS = (b"GIF87a", b"GIF89a")


def ensure_safe_paths(src: Path, dst: Path, *, force: bool) -> None:
    src = Path(src)
    dst = Path(dst)

    if dst.exists() and dst.is_dir():
        raise UsageError(f"output is a directory: {dst}")

    if dst.exists():
        try:
            if os.path.samefile(src, dst):
                raise UsageError("output path is the input file (or a link to it)")
        except OSError:
            pass
        if not force:
            raise UsageError(f"output already exists: {dst} (use --force to overwrite)")

    if src.resolve() == dst.resolve():
        raise UsageError("output path is the input file")


def atomic_replace(src: Path, dst: Path) -> None:
    src = Path(src)
    dst = Path(dst)
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent)
        )
        os.close(fd)
    except OSError as exc:
        raise OutputError(f"cannot create a temp file next to {dst}: {exc}") from exc

    try:
        shutil.copyfile(src, temp_name)
        current_umask = os.umask(0)
        os.umask(current_umask)
        os.chmod(temp_name, 0o666 & ~current_umask)
        os.replace(temp_name, dst)
    except OSError as exc:
        raise OutputError(
            f"could not replace {dst}: {exc} (candidate kept at {temp_name})"
        ) from exc


def validate_output(
    path: Path, info: GifInfo, spec: EncodeSpec, preset: Preset
) -> None:
    path = Path(path)
    try:
        with path.open("rb") as handle:
            magic = handle.read(6)
    except OSError as exc:
        raise ValidationError(f"cannot read output {path}: {exc}") from exc
    if magic not in _GIF_HEADERS:
        raise ValidationError(f"output is not a GIF: {path}")

    try:
        output = probe_gif(path)
    except ProbeError as exc:
        raise ValidationError(f"could not probe output {path}: {exc}") from exc

    canvas = spec.canvas or preset.fixed_canvas
    if canvas is not None:
        if (output.width, output.height) != canvas:
            raise ValidationError(
                f"output dimensions {output.width}x{output.height} != canvas "
                f"{canvas[0]}x{canvas[1]}"
            )
    elif (output.width, output.height) != (spec.width, spec.height):
        raise ValidationError(
            f"output dimensions {output.width}x{output.height} != "
            f"{spec.width}x{spec.height}"
        )

    if output.frames < 1:
        raise ValidationError("output has no frames")
    if spec.max_frames is not None and output.frames > spec.max_frames:
        raise ValidationError(
            f"output has {output.frames} frames (limit {spec.max_frames})"
        )

    if output.loop_known and output.loop != spec.loop:
        raise ValidationError(f"output loop {output.loop!r} != requested {spec.loop!r}")

    if (
        preset.keep_transparency
        and info.has_alpha is True
        and preset.background is None
    ):
        if output.has_alpha is False:
            raise ValidationError("output lost transparency")

    expected_duration = info.duration_s
    if spec.max_duration_s is not None:
        expected_duration = min(expected_duration, spec.max_duration_s)
    if expected_duration > 0:
        frame = 1.0 / spec.fps if spec.fps > 0 else 0.1
        tolerance = max(frame, 0.05 * expected_duration, 0.05)
        if abs(output.duration_s - expected_duration) > tolerance:
            raise ValidationError(
                f"output duration {output.duration_s:.3f}s != expected "
                f"{expected_duration:.3f}s"
            )
