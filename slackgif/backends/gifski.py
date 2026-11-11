from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from slackgif.config import EncodeSpec, GifInfo, Preset
from slackgif.errors import BackendError
from slackgif.filters import (
    build_frame_filtergraph,
    build_gifski_direct_args,
    build_gifski_frames_args,
)


class GifskiBackend:
    name = "gifski"
    quality_levels = None

    def __init__(self, workspace: Path, timeout_s: float = 120.0):
        self.workspace = Path(workspace)
        self.timeout_s = timeout_s
        self._frame_cache: dict[tuple, Path] = {}
        self._root = self.workspace / "gifski"
        self._root.mkdir(parents=True, exist_ok=True)

    def available(self) -> bool:
        return shutil.which("gifski") is not None

    def _needs_frames(
        self,
        info: GifInfo,
        preset: Preset | None = None,
        spec: EncodeSpec | None = None,
    ) -> bool:
        if spec is not None:
            return bool(
                spec.canvas is not None
                or abs(spec.fps - info.fps) > 1e-6
                or spec.background is not None
                or (
                    spec.max_duration_s is not None and spec.max_duration_s < info.duration_s - 1e-9
                )
                or (spec.max_frames is not None and info.frames > spec.max_frames)
            )
        assert preset is not None
        policy = preset.policy
        return bool(
            preset.fixed_canvas is not None
            or preset.background is not None
            or preset.fit != "contain"
            or (policy.max_fps is not None and info.fps > policy.max_fps)
            or (policy.max_frames is not None and info.frames > policy.max_frames)
            or (policy.max_duration_s is not None and info.duration_s > policy.max_duration_s)
        )

    def can_handle(self, info: GifInfo, preset: Preset) -> bool:
        if not self.available():
            return False
        if not self._needs_frames(info, preset=preset):
            return True
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

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

    def _frames_for(
        self, src: Path, spec: EncodeSpec, info: GifInfo, timeout: float
    ) -> tuple[Path, float]:
        key = (
            str(src),
            spec.width,
            spec.height,
            spec.fps,
            spec.fit,
            spec.background,
            spec.max_duration_s,
            spec.max_frames,
            spec.canvas,
        )
        cached = self._frame_cache.get(key)
        if cached is not None and cached[0].exists():
            return cached

        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise BackendError("gifski needs ffmpeg for fps/canvas/background output")
        frame_dir = self._root / f"frames-{len(self._frame_cache):03d}"
        frame_dir.mkdir(parents=True, exist_ok=True)
        filtergraph = build_frame_filtergraph(spec, info)
        command = [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-i",
            str(src),
            "-filter_complex",
            filtergraph,
            "-map",
            "[out]",
            "-fps_mode",
            "passthrough",
        ]
        if spec.max_duration_s is not None:
            command.extend(["-t", str(spec.max_duration_s)])
        command.append(str(frame_dir / "frame_%06d.png"))
        self._run(command, timeout)

        frames = sorted(frame_dir.glob("frame_*.png"))
        if not frames:
            raise BackendError("ffmpeg produced no PNG frames")

        rate = spec.fps
        if len(frames) == 1:
            # gifski refuses a single image. Duplicate it and choose a rate
            # that preserves the intended duration.
            duplicate = frame_dir / "frame_000002.png"
            shutil.copyfile(frames[0], duplicate)
            duration = spec.max_duration_s or info.duration_s
            if duration <= 0:
                duration = 1.0 / spec.fps if spec.fps > 0 else 1.0
            rate = 2.0 / duration

        self._frame_cache[key] = (frame_dir, rate)
        return frame_dir, rate

    def encode(
        self,
        src: Path,
        dst: Path,
        spec: EncodeSpec,
        info: GifInfo,
        *,
        timeout_s: float | None = None,
    ) -> None:
        binary = shutil.which("gifski")
        if binary is None:
            raise BackendError("gifski is not installed")
        timeout = self.timeout_s if timeout_s is None else timeout_s
        dst = Path(dst)

        if self._needs_frames(info, spec=spec):
            frame_dir, rate = self._frames_for(Path(src), spec, info, timeout)
            frames = sorted(frame_dir.glob("frame_*.png"))
            command = build_gifski_frames_args(binary, frames, dst, spec, rate=rate)
        else:
            command = build_gifski_direct_args(binary, Path(src), dst, spec)

        try:
            self._run(command, timeout)
        except BackendError:
            dst.unlink(missing_ok=True)
            raise
        if not dst.exists():
            raise BackendError(f"gifski did not write {dst}")

    def close(self) -> None:
        shutil.rmtree(self._root, ignore_errors=True)
