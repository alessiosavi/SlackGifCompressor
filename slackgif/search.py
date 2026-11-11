from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from slackgif.backends.base import Backend
from slackgif.config import (
    Candidate,
    EncodeSpec,
    GifInfo,
    Preset,
    Result,
    SearchPolicy,
    is_compliant,
)
from slackgif.errors import BackendError, ProbeError, ValidationError
from slackgif.output import atomic_replace, validate_output
from slackgif.probe import probe_gif

WIDTH_EXPONENT = 1.55
FPS_EXPONENT = 0.90
SEARCH_AIM = 0.99
MAX_PHASE_B_STEPS = 4


def is_success(size_bytes: int, target_bytes: int | None) -> bool:
    return target_bytes is None or size_bytes <= target_bytes


def _effective_quality(backend: Backend, quality: int) -> int:
    levels = backend.quality_levels
    if not levels:
        return quality
    return min(levels, key=lambda level: (abs(level - quality), level))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _measured_slope(
    previous_size: int, current_size: int, previous_l: float, current_l: float
) -> float:
    if abs(current_l - previous_l) < 1e-12 or previous_size <= 0 or current_size <= 0:
        return 1.0
    slope = (math.log2(previous_size) - math.log2(current_size)) / (
        current_l - previous_l
    )
    return _clamp(slope, 0.25, 4.0)


def _initial_dimensions(
    info: GifInfo, policy: SearchPolicy, canvas: tuple[int, int] | None
) -> tuple[int, int]:
    if canvas is not None:
        return canvas
    if info.width <= 0 or info.height <= 0:
        raise BackendError("input has invalid dimensions")
    scale = min(policy.max_width / info.width, policy.max_height / info.height, 1.0)
    return max(1, int(info.width * scale)), max(1, int(info.height * scale))


def _duration_after_trim(info: GifInfo, policy: SearchPolicy) -> float:
    duration = info.duration_s
    if policy.max_duration_s is not None and duration > 0:
        duration = min(duration, policy.max_duration_s)
    return duration


def _initial_fps(info: GifInfo, policy: SearchPolicy) -> tuple[float, float, bool]:
    duration = _duration_after_trim(info, policy)
    fps = info.fps
    if fps <= 0 and info.frames > 0 and info.duration_s > 0:
        fps = info.frames / info.duration_s
    if policy.max_fps is not None and fps > 0:
        fps = min(fps, policy.max_fps)
    if policy.max_frames is not None and duration > 0 and fps > 0:
        fps = min(fps, policy.max_frames / duration)
    if info.frames <= 1 or info.duration_s <= 0 or info.fps <= 0 or fps <= 0:
        return 1.0, 1.0, True
    return fps, min(policy.min_fps, fps), False


def _allocate(
    reduction: float,
    width0: int,
    height0: int,
    fps0: float,
    min_width: int,
    min_fps: float,
    canvas: tuple[int, int] | None,
    mode: str,
    x_max: float,
    y_max: float,
    static: bool,
) -> tuple[int, int, float]:
    if canvas is not None:
        width, height = canvas
        y = 0.0 if static else _clamp(reduction / FPS_EXPONENT, 0.0, y_max)
        fps = fps0 / (2**y)
        return width, height, _clamp(fps, min_fps, fps0)

    if static:
        x = _clamp(reduction / WIDTH_EXPONENT, 0.0, x_max)
        y = 0.0
    else:
        x = 0.0
        y = 0.0
        if mode == "resolution":
            y = min(y_max, reduction / FPS_EXPONENT)
            x = min(x_max, (reduction - FPS_EXPONENT * y) / WIDTH_EXPONENT)
        elif mode == "motion":
            x = min(x_max, reduction / WIDTH_EXPONENT)
            y = min(y_max, (reduction - WIDTH_EXPONENT * x) / FPS_EXPONENT)
        else:
            x = min(x_max, reduction / (WIDTH_EXPONENT + FPS_EXPONENT))
            y = min(y_max, (reduction - WIDTH_EXPONENT * x) / FPS_EXPONENT)
            if y >= y_max - 1e-12:
                y = y_max
                x = min(x_max, (reduction - FPS_EXPONENT * y) / WIDTH_EXPONENT)
        x = max(0.0, x)
        y = max(0.0, y)

    width = int(round(width0 / (2**x)))
    width = int(_clamp(width, min_width, width0))
    fps = fps0 / (2**y)
    fps = _clamp(fps, min_fps, fps0)
    height = max(1, min(height0, int(round(width * height0 / width0))))
    return width, height, fps


def _loop_value(preset: Preset, info: GifInfo) -> int | None:
    if preset.loop == "preserve":
        return info.loop if info.loop_known else None
    if preset.loop == "forever":
        return 0
    if preset.loop == "once":
        return None
    return int(preset.loop)


@dataclass
class _Search:
    src: Path
    dst: Path
    preset: Preset
    backend: Backend
    info: GifInfo
    validator: Callable[[Path, GifInfo, EncodeSpec, Preset], None]
    workspace: Path
    target: int | None
    budget: int
    loop: int | None
    warnings: list[str] = field(default_factory=list)
    encodes: int = 0
    cache: dict[EncodeSpec, Candidate] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    @property
    def policy(self) -> SearchPolicy:
        return self.preset.policy

    def make_spec(
        self, width: int, height: int, fps: float, quality: int
    ) -> EncodeSpec:
        return EncodeSpec(
            width=width,
            height=height,
            fps=fps,
            quality=quality,
            loop=self.loop,
            fit=self.preset.fit,
            canvas=self.preset.fixed_canvas,
            background=self.preset.background,
            max_duration_s=self.policy.max_duration_s,
            max_frames=self.policy.max_frames,
        )

    def encode(self, spec: EncodeSpec) -> Candidate | None:
        spec = replace(spec, quality=_effective_quality(self.backend, spec.quality))
        cached = self.cache.get(spec)
        if cached is not None:
            return cached

        current = spec
        for _ in range(2):
            cached = self.cache.get(current)
            if cached is not None:
                return cached
            if self.encodes >= self.policy.max_encodes:
                return None

            path = self.workspace / f"candidate-{self.encodes:02d}.gif"
            started = time.monotonic()
            try:
                self.backend.encode(
                    self.src, path, current, self.info, timeout_s=self.policy.timeout_s
                )
            except BackendError:
                path.unlink(missing_ok=True)
                raise
            seconds = time.monotonic() - started
            self.encodes += 1
            if not path.exists():
                raise BackendError(f"{self.backend.name} did not write {path}")
            size_bytes = path.stat().st_size

            if current.max_frames is not None:
                frames = None
                try:
                    frames = probe_gif(path).frames
                except ProbeError:
                    frames = None
                if frames is not None and frames > current.max_frames:
                    duration = _duration_after_trim(self.info, self.policy)
                    if duration > 0 and self.encodes < self.policy.max_encodes:
                        adjusted_fps = (current.max_frames - 1) / duration
                        if adjusted_fps > 0 and abs(adjusted_fps - current.fps) > 1e-9:
                            path.unlink(missing_ok=True)
                            current = replace(current, fps=adjusted_fps)
                            continue

            candidate = Candidate(
                spec=current, size_bytes=size_bytes, path=path, seconds=seconds
            )
            try:
                self.validator(path, self.info, current, self.preset)
            except ValidationError as exc:
                path.unlink(missing_ok=True)
                self.validation_errors.append(str(exc))
                self.warnings.append(f"discarded invalid candidate: {exc}")
                return None

            self.candidates.append(candidate)
            self.cache[current] = candidate
            return candidate
        return None


def _quality_search(state: _Search, initial: Candidate) -> Candidate:
    levels = state.backend.quality_levels
    if levels is None:
        return _continuous_quality_search(state, initial)
    return _discrete_quality_search(state, initial, levels)


def _continuous_quality_search(state: _Search, initial: Candidate) -> Candidate:
    best = initial
    q_lo = initial.spec.quality
    s_lo = initial.size_bytes
    if s_lo >= SEARCH_AIM * state.budget:
        return best

    q_hi: int | None = None
    s_hi: int | None = None
    q = q_lo
    step = 30
    while True:
        q = min(100, q + step)
        if q <= q_lo:
            break
        candidate = state.encode(replace(initial.spec, quality=q))
        if candidate is None:
            break
        if candidate.size_bytes > state.budget:
            q_hi = q
            s_hi = candidate.size_bytes
            break
        best = candidate
        q_lo = q
        s_lo = candidate.size_bytes
        if q >= 100:
            return best
        step *= 2

    if q_hi is None or s_hi is None:
        return best

    while True:
        if s_lo >= SEARCH_AIM * state.budget:
            break
        denominator = math.log(s_hi) - math.log(s_lo)
        if abs(denominator) < 1e-12:
            break
        predicted = q_lo + (
            (math.log(state.budget) - math.log(s_lo)) * (q_hi - q_lo) / denominator
        )
        if predicted - q_lo < 0.5:
            break
        q = int(round(predicted))
        q = max(q_lo + 1, min(q_hi - 1, q))
        if q <= q_lo or q >= q_hi:
            break
        candidate = state.encode(replace(initial.spec, quality=q))
        if candidate is None:
            break
        if candidate.size_bytes <= state.budget:
            best = candidate
            q_lo = q
            s_lo = candidate.size_bytes
        else:
            q_hi = q
            s_hi = candidate.size_bytes
        if q_hi - q_lo <= 1:
            break
    return best


def _discrete_quality_search(
    state: _Search, initial: Candidate, levels: tuple[int, ...]
) -> Candidate:
    q_lo = initial.spec.quality
    available = [level for level in levels if level >= q_lo]
    if not available or available[0] != q_lo:
        return initial
    best = initial
    if initial.size_bytes >= SEARCH_AIM * state.budget:
        return best

    low = 0
    high = len(available) - 1
    trials = 0
    while low < high and trials < 3:
        middle = (low + high + 1) // 2
        quality = available[middle]
        candidate = state.encode(replace(initial.spec, quality=quality))
        trials += 1
        if candidate is None:
            break
        if candidate.size_bytes <= state.budget:
            best = candidate
            low = middle
        else:
            high = middle - 1
    return best


def _base_warnings(info: GifInfo, preset: Preset) -> list[str]:
    warnings: list[str] = []
    if info.variable_delays:
        warnings.append(
            "variable frame delays may be normalized; exact per-frame timing is not "
            "preserved in this version."
        )
    if info.has_alpha is None:
        warnings.append("input transparency could not be determined")
    if not info.loop_known:
        warnings.append("input loop metadata could not be determined")
    if preset.policy.max_encodes < 3:
        warnings.append("max_encodes below 3 may produce a poor result")
    return warnings


def compress(
    src: Path,
    dst: Path,
    *,
    preset: Preset,
    backend: Backend,
    info: GifInfo | None = None,
    validator: Callable[[Path, GifInfo, EncodeSpec, Preset], None] | None = None,
) -> Result:
    src = Path(src)
    dst = Path(dst)
    started = time.monotonic()
    if info is None:
        info = probe_gif(src)
    validator = validator or validate_output

    target = preset.policy.target_bytes
    warnings = _base_warnings(info, preset)

    if target is not None and info.size_bytes <= target and is_compliant(info, preset):
        try:
            atomic_replace(src, dst)
        finally:
            try:
                backend.close()
            except OSError:
                pass
        return Result(
            success=True,
            output=dst,
            candidate=None,
            target_bytes=target,
            backend=backend.name,
            encodes=0,
            seconds=time.monotonic() - started,
            warnings=tuple(warnings),
        )

    budget = (
        max(1, int(math.floor(target * preset.policy.margin)))
        if target is not None
        else 0
    )
    loop = _loop_value(preset, info)
    with tempfile.TemporaryDirectory(prefix="slackgif-") as workspace_name:
        workspace = Path(workspace_name)
        state = _Search(
            src=src,
            dst=dst,
            preset=preset,
            backend=backend,
            info=info,
            validator=validator,
            workspace=workspace,
            target=target,
            budget=budget,
            loop=loop,
            warnings=warnings,
        )
        try:
            chosen = _run_search(state)
            atomic_replace(chosen.path, dst)
        finally:
            try:
                backend.close()
            except OSError:
                pass

    output_size = dst.stat().st_size
    success = is_success(output_size, target)
    if not success:
        state.warnings.append(
            "target is unreachable; try --max-duration, --min-width, or --min-fps"
        )
    return Result(
        success=success,
        output=dst,
        candidate=chosen,
        target_bytes=target,
        backend=backend.name,
        encodes=state.encodes,
        seconds=time.monotonic() - started,
        warnings=tuple(state.warnings),
        error=None if success else "output exceeds target",
    )


def _run_search(state: _Search) -> Candidate:
    policy = state.policy
    info = state.info
    width0, height0 = _initial_dimensions(info, policy, state.preset.fixed_canvas)
    fps0, min_fps, static = _initial_fps(info, policy)
    min_width = min(policy.min_width, width0)
    x_max = (
        math.log2(width0 / min_width) if min_width > 0 and width0 > min_width else 0.0
    )
    y_max = math.log2(fps0 / min_fps) if min_fps > 0 and fps0 > min_fps else 0.0
    max_reduction = WIDTH_EXPONENT * x_max + FPS_EXPONENT * y_max

    levels = state.backend.quality_levels
    if policy.fixed_quality is not None:
        phase_quality = policy.fixed_quality
    elif levels:
        eligible = [level for level in levels if level >= policy.min_quality]
        phase_quality = eligible[0] if eligible else levels[-1]
    else:
        phase_quality = policy.min_quality

    initial_spec = state.make_spec(width0, height0, fps0, phase_quality)
    initial = state.encode(initial_spec)
    if initial is None:
        if state.validation_errors:
            raise BackendError(
                f"{state.backend.name} produced no structurally valid candidates: "
                f"{state.validation_errors[-1]}"
            )
        raise BackendError(f"{state.backend.name} produced no candidate")

    if state.target is None:
        return max(state.candidates, key=lambda c: c.spec.quality)

    if initial.size_bytes <= state.budget:
        if policy.fixed_quality is not None:
            return initial
        return _quality_search(state, initial)

    best_fit: Candidate | None = None
    reduction = _clamp(math.log2(initial.size_bytes / state.budget), 0.0, max_reduction)
    previous_l = 0.0
    previous_size = initial.size_bytes
    previous_dims = (width0, height0, fps0)

    for _ in range(MAX_PHASE_B_STEPS):
        width, height, fps = _allocate(
            reduction,
            width0,
            height0,
            fps0,
            min_width,
            min_fps,
            state.preset.fixed_canvas,
            policy.quality_mode,
            x_max,
            y_max,
            static,
        )
        dims = (width, height, fps)
        if dims == previous_dims:
            break
        candidate = state.encode(state.make_spec(width, height, fps, phase_quality))
        if candidate is None:
            break
        if candidate.size_bytes <= state.budget:
            best_fit = candidate
            if candidate.size_bytes >= 0.90 * state.budget:
                break

        gamma = _measured_slope(
            previous_size, candidate.size_bytes, previous_l, reduction
        )
        next_reduction = _clamp(
            reduction + math.log2(candidate.size_bytes / state.budget) / gamma,
            0.0,
            max_reduction,
        )
        previous_l = reduction
        previous_size = candidate.size_bytes
        previous_dims = dims
        if abs(next_reduction - reduction) < 1e-12:
            reduction = next_reduction
            break
        reduction = next_reduction

    if best_fit is not None:
        if policy.fixed_quality is not None:
            return best_fit
        return _quality_search(state, best_fit)

    floor_width = min_width
    if state.preset.fixed_canvas is not None:
        floor_width, floor_height = state.preset.fixed_canvas
    else:
        floor_height = max(1, min(height0, int(round(floor_width * height0 / width0))))
    floor_quality = policy.fixed_quality if policy.fixed_quality is not None else 1
    floor = state.encode(
        state.make_spec(floor_width, floor_height, min_fps, floor_quality)
    )
    if floor is not None and floor.size_bytes <= state.budget:
        if policy.fixed_quality is not None:
            return floor
        return _quality_search(state, floor)

    if not state.candidates:
        if state.validation_errors:
            raise BackendError(
                f"{state.backend.name} produced no structurally valid candidates: "
                f"{state.validation_errors[-1]}"
            )
        raise BackendError(f"{state.backend.name} produced no candidate")

    under_target = [
        candidate
        for candidate in state.candidates
        if candidate.size_bytes <= state.target
    ]
    if under_target:
        return max(
            under_target,
            key=lambda c: (c.spec.width, c.spec.height, c.spec.fps, c.spec.quality),
        )
    return min(state.candidates, key=lambda c: c.size_bytes)
