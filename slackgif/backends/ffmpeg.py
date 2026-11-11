from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from slackgif.backends.quality import QUALITY_LEVELS, quality_to_colors
from slackgif.config import EncodeSpec, GifInfo, Preset
from slackgif.errors import BackendError
from slackgif.filters import build_encode_args, build_palette_args

__all__ = ["FfmpegBackend", "quality_to_colors", "QUALITY_LEVELS"]


class FfmpegBackend:
    name = "ffmpeg"
    quality_levels = QUALITY_LEVELS

    def __init__(self, workspace: Path, timeout_s: float = 120.0):
        self.workspace = Path(workspace)
        self.timeout_s = timeout_s
        self._root = self.workspace / "ffmpeg"
        self._root.mkdir(parents=True, exist_ok=True)
        self._counter = 0

    def available(self) -> bool:
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    def can_handle(self, info: GifInfo, preset: Preset) -> bool:
        return self.available()

    def _run(self, command: list[str], timeout: float) -> None:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise BackendError(f"{command[0]} failed: {exc}") from exc
        if result.returncode != 0:
            tail = (result.stderr or "").strip()[-2000:]
            raise BackendError(
                f"{command[0]} exited {result.returncode}: {tail or 'unknown error'}"
            )

    def _gifsicle_postpass(self, dst: Path, timeout: float) -> None:
        binary = shutil.which("gifsicle")
        if binary is None:
            return
        self._counter += 1
        candidate = self._root / f"gifsicle-{self._counter:03d}.gif"
        command = [binary, "-O3", "--lossy=120", str(dst), "-o", str(candidate)]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired):
            candidate.unlink(missing_ok=True)
            return
        if (
            result.returncode == 0
            and candidate.exists()
            and candidate.stat().st_size < dst.stat().st_size
        ):
            os.replace(candidate, dst)
        else:
            candidate.unlink(missing_ok=True)

    def encode(
        self,
        src: Path,
        dst: Path,
        spec: EncodeSpec,
        info: GifInfo,
        *,
        timeout_s: float | None = None,
    ) -> None:
        binary = shutil.which("ffmpeg")
        if binary is None:
            raise BackendError("ffmpeg is not installed")
        timeout = self.timeout_s if timeout_s is None else timeout_s
        src = Path(src)
        dst = Path(dst)
        self._counter += 1
        palette = self._root / f"palette-{self._counter:03d}.png"
        colors = quality_to_colors(spec.quality)

        try:
            self._run(build_palette_args(binary, src, palette, spec, colors, info), timeout)
            if not palette.exists() or palette.stat().st_size == 0:
                raise BackendError("ffmpeg produced no palette (static or empty input)")
            self._run(
                build_encode_args(binary, src, palette, dst, spec, colors, info),
                timeout,
            )
            if not dst.exists():
                raise BackendError(f"ffmpeg did not write {dst}")
            self._gifsicle_postpass(dst, timeout)
        except BackendError:
            dst.unlink(missing_ok=True)
            raise
        finally:
            palette.unlink(missing_ok=True)

    def close(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)
