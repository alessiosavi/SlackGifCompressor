from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from pathlib import Path

from slackgif.errors import UsageError

MESSAGE_TARGET = 5 * 1024 * 1024
EMOJI_TARGET = 128 * 1024
MAX_FRAMES_GUARD = 10_000
MAX_PIXELS_GUARD = 100_000_000
MAX_TOTAL_PIXELS_GUARD = 200_000_000
MAX_DURATION_GUARD = 600.0

_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)?\s*$", re.IGNORECASE)
_SIZE_FACTORS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


@dataclass(frozen=True)
class GifInfo:
    width: int
    height: int
    frames: int
    fps: float
    duration_s: float
    loop: int | None
    loop_known: bool
    has_alpha: bool | None
    variable_delays: bool | None
    size_bytes: int


@dataclass(frozen=True)
class EncodeSpec:
    width: int
    height: int
    fps: float
    quality: int
    loop: int | None
    fit: str = "contain"
    canvas: tuple[int, int] | None = None
    background: str | None = None
    max_duration_s: float | None = None
    max_frames: int | None = None


@dataclass(frozen=True)
class Candidate:
    spec: EncodeSpec
    size_bytes: int
    path: Path
    seconds: float


@dataclass(frozen=True)
class Result:
    success: bool
    output: Path | None
    candidate: Candidate | None
    target_bytes: int | None
    backend: str
    encodes: int
    seconds: float
    warnings: tuple[str, ...]
    error: str | None = None


@dataclass(frozen=True)
class SearchPolicy:
    target_bytes: int | None
    margin: float = 0.99
    quality_mode: str = "balanced"
    fixed_quality: int | None = None
    min_quality: int = 30
    max_quality: int = 100
    min_width: int = 64
    min_fps: float = 5.0
    max_width: int = 1024
    max_height: int = 1024
    max_fps: float | None = None
    max_frames: int | None = None
    max_duration_s: float | None = None
    max_encodes: int = 10
    timeout_s: float = 120.0
    allow_large: bool = False


@dataclass(frozen=True)
class Preset:
    name: str
    policy: SearchPolicy
    fixed_canvas: tuple[int, int] | None
    fit: str
    background: str | None
    keep_transparency: bool
    loop: str | int

    def __post_init__(self) -> None:
        if self.policy.target_bytes is None and self.policy.fixed_quality is None:
            raise UsageError("a preset needs a byte target or a fixed quality")
        if self.fixed_canvas is not None:
            width, height = self.fixed_canvas
            if self.policy.max_width != width or self.policy.max_height != height:
                raise UsageError("fixed-canvas presets must clamp max width/height to the canvas")


def parse_size(value: str) -> int:
    match = _SIZE_RE.match(value or "")
    if not match:
        raise UsageError(f"invalid size: {value!r}")
    number = float(match.group(1))
    unit = (match.group(2) or "KB").upper()
    if not math.isfinite(number) or number <= 0:
        raise UsageError(f"size must be a finite positive number: {value!r}")
    size = int(number * _SIZE_FACTORS[unit])
    if size <= 0:
        raise UsageError(f"size is too small to represent: {value!r}")
    return size


def _parse_loop(value: str | None, default: str | int) -> str | int:
    if value is None:
        return default
    if value in ("preserve", "forever", "once"):
        return value
    if value.isdigit():
        count = int(value)
        if count > 0xFFFF:
            raise UsageError(f"loop count exceeds the 16-bit GIF limit: {value!r}")
        return count
    raise UsageError(f"invalid loop value: {value!r}")


def _parse_fit(value: str | None, default: str) -> str:
    fit = default if value is None else value
    if fit == "pad":
        fit = "contain"
    if fit not in ("contain", "cover"):
        raise UsageError(f"invalid fit: {value!r}")
    return fit


def _parse_quality(value: int | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or not 1 <= value <= 100:
        raise UsageError(f"quality must be between 1 and 100: {value!r}")
    return value


def _base_policy(**overrides) -> SearchPolicy:
    values = dict(
        target_bytes=MESSAGE_TARGET,
        margin=0.99,
        quality_mode="balanced",
        fixed_quality=None,
        min_quality=30,
        max_quality=100,
        min_width=64,
        min_fps=5.0,
        max_width=1024,
        max_height=1024,
        max_fps=30,
        max_frames=None,
        max_duration_s=None,
        max_encodes=10,
        timeout_s=120.0,
        allow_large=False,
    )
    values.update(overrides)
    return SearchPolicy(**values)


def resolve_preset(
    name: str,
    *,
    max_size: str | None,
    quality: int | None,
    loop: str | None,
    fit: str | None,
    background: str | None,
    keep_transparency: bool | None = None,
) -> Preset:
    quality = _parse_quality(quality)

    if name == "message":
        policy = _base_policy()
        default_loop: str | int = "preserve"
        default_fit = "contain"
        default_keep = True
        fixed_canvas = None
    elif name == "emoji":
        policy = _base_policy(
            target_bytes=EMOJI_TARGET,
            margin=0.97,
            min_quality=20,
            min_width=128,
            min_fps=2.5,
            max_width=128,
            max_height=128,
            max_fps=15,
            max_frames=50,
        )
        default_loop = "forever"
        default_fit = "contain"
        default_keep = True
        fixed_canvas = (128, 128)
    elif name == "custom":
        if max_size is None and quality is None:
            raise UsageError("--preset custom requires --max-size or --quality")
        policy = _base_policy(target_bytes=None, max_fps=None)
        default_loop = "preserve"
        default_fit = "contain"
        default_keep = True
        fixed_canvas = None
    else:
        raise UsageError(f"unknown preset: {name!r}")

    target_bytes = parse_size(max_size) if max_size is not None else policy.target_bytes
    if quality is not None:
        policy = replace(
            policy,
            fixed_quality=quality,
            target_bytes=target_bytes if max_size is not None else None,
        )
    elif target_bytes != policy.target_bytes:
        policy = replace(policy, target_bytes=target_bytes)

    if background is not None:
        if not background.strip():
            raise UsageError("--background needs a color")
        if keep_transparency is True:
            raise UsageError("--background and --keep-transparency are mutually exclusive")
        keep = False
    else:
        keep = default_keep if keep_transparency is None else keep_transparency

    return Preset(
        name=name,
        policy=policy,
        fixed_canvas=fixed_canvas,
        fit=_parse_fit(fit, default_fit),
        background=background,
        keep_transparency=keep,
        loop=_parse_loop(loop, default_loop),
    )


def is_compliant(info: GifInfo, preset: Preset) -> bool:
    policy = preset.policy
    if policy.fixed_quality is not None:
        return False
    if policy.target_bytes is not None and info.size_bytes > policy.target_bytes:
        return False

    if preset.fixed_canvas is not None:
        if (info.width, info.height) != preset.fixed_canvas:
            return False
    else:
        if info.width > policy.max_width or info.height > policy.max_height:
            return False

    if policy.max_fps is not None and info.fps > policy.max_fps + 0.01:
        return False
    if policy.max_frames is not None and info.frames > policy.max_frames:
        return False
    if policy.max_duration_s is not None:
        frame = 1.0 / info.fps if info.fps > 0 else 0.1
        if info.duration_s > policy.max_duration_s + frame + 1e-9:
            return False

    if preset.loop == "preserve":
        pass
    elif preset.loop == "forever":
        if not info.loop_known or info.loop != 0:
            return False
    elif preset.loop == "once":
        if not info.loop_known or info.loop is not None:
            return False
    else:
        if not info.loop_known or info.loop != int(preset.loop):
            return False

    if preset.keep_transparency and info.has_alpha is None:
        return False
    if preset.background is not None:
        # A copy cannot flatten, even when the source's first frame is opaque:
        # optimized GIFs can introduce real transparency in later frames.
        return False
    return True


def validate_limits(info: GifInfo, policy: SearchPolicy) -> None:
    if policy.allow_large:
        return
    if info.frames > MAX_FRAMES_GUARD:
        raise UsageError(
            f"input has {info.frames} frames (limit {MAX_FRAMES_GUARD}); "
            "use --allow-large to override"
        )
    if info.width * info.height > MAX_PIXELS_GUARD:
        raise UsageError(
            f"input frame is {info.width}x{info.height} (limit {MAX_PIXELS_GUARD} pixels); "
            "use --allow-large to override"
        )
    if info.frames * info.width * info.height > MAX_TOTAL_PIXELS_GUARD:
        raise UsageError(
            f"input totals {info.frames * info.width * info.height} pixels "
            f"(limit {MAX_TOTAL_PIXELS_GUARD}); use --allow-large to override"
        )
    if info.duration_s > MAX_DURATION_GUARD:
        raise UsageError(
            f"input lasts {info.duration_s:.0f}s (limit {MAX_DURATION_GUARD:.0f}s); "
            "use --allow-large to override"
        )
