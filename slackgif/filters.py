from __future__ import annotations

import re

from slackgif.config import EncodeSpec, GifInfo
from slackgif.errors import BackendError

_COLOR_RE = re.compile(r"^(#[0-9A-Fa-f]{3,8}|[A-Za-z]+)$")


def format_rate(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def validate_color(value: str) -> str:
    if not _COLOR_RE.match(value or ""):
        raise BackendError(f"invalid background color: {value!r}")
    return value


def build_frame_prefilter(spec: EncodeSpec, info: GifInfo) -> str:
    chain = [f"fps={format_rate(spec.fps)}", "format=rgba"]
    if spec.canvas is not None:
        width, height = spec.canvas
        if spec.fit == "cover":
            chain.append(
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos"
            )
            chain.append(f"crop={width}:{height}")
        else:
            chain.append(
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos"
            )
            chain.append(f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#00000000")
    else:
        chain.append(f"scale={spec.width}:{spec.height}:flags=lanczos")
    return ",".join(chain)


def build_frame_filtergraph(spec: EncodeSpec, info: GifInfo) -> str:
    prefilter = build_frame_prefilter(spec, info)
    if spec.background is not None:
        color = validate_color(spec.background)
        width, height = spec.canvas or (spec.width, spec.height)
        return (
            f"color=c={color}:s={width}x{height}:r={format_rate(spec.fps)}[bg];"
            f"[0:v]{prefilter}[fg];"
            f"[bg][fg]overlay=shortest=1:format=auto,format=rgb24[out]"
        )
    return f"[0:v]{prefilter},format=rgba[out]"


def build_gifski_direct_args(binary: str, src, dst, spec: EncodeSpec) -> list[str]:
    repeat = -1 if spec.loop is None else int(spec.loop)
    quality = max(1, min(100, spec.quality))
    return [
        binary,
        "-q",
        "--repeat",
        str(repeat),
        "-W",
        str(spec.width),
        "-H",
        str(spec.height),
        "-Q",
        str(quality),
        "-o",
        str(dst),
        str(src),
    ]


def build_gifski_frames_args(
    binary: str, frames, dst, spec: EncodeSpec, rate: float | None = None
) -> list[str]:
    repeat = -1 if spec.loop is None else int(spec.loop)
    quality = max(1, min(100, spec.quality))
    fps = spec.fps if rate is None else rate
    return [
        binary,
        "-q",
        "--repeat",
        str(repeat),
        "-r",
        format_rate(fps),
        "-Q",
        str(quality),
        "-o",
        str(dst),
        *[str(frame) for frame in frames],
    ]
