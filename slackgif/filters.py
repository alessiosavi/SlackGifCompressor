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


def _is_static(info: GifInfo | None) -> bool:
    return bool(info is not None and (info.frames <= 1 or info.duration_s <= 0 or info.fps <= 0))


def _fps_prefix(spec: EncodeSpec, info: GifInfo | None) -> str | None:
    if _is_static(info):
        return None
    return f"fps={format_rate(spec.fps)}"


def _background_duration(spec: EncodeSpec, info: GifInfo | None) -> float:
    duration = spec.max_duration_s
    if duration is None and info is not None and info.duration_s > 0:
        duration = info.duration_s
    if duration is None or duration <= 0:
        duration = 1.0 / spec.fps if spec.fps > 0 else 1.0
    return duration


def build_frame_prefilter(spec: EncodeSpec, info: GifInfo) -> str:
    chain = []
    prefix = _fps_prefix(spec, info)
    if prefix is not None:
        chain.append(prefix)
    chain.append("format=rgba")
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
            f"color=c={color}:s={width}x{height}:r={format_rate(spec.fps)}"
            f":d={format_rate(_background_duration(spec, info))}[bg];"
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


def build_prefilter(spec: EncodeSpec, info: GifInfo | None = None) -> str:
    prefix = _fps_prefix(spec, info)
    if spec.canvas is not None:
        width, height = spec.canvas
        if spec.fit == "cover":
            canvas_chain = (
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height}"
            )
        else:
            canvas_chain = (
                f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=#00000000"
            )
        return ",".join(filter(None, [prefix, canvas_chain]))
    scale_chain = f"scale={spec.width}:{spec.height}:flags=lanczos"
    return ",".join(filter(None, [prefix, scale_chain]))


def build_input_graph(spec: EncodeSpec, info: GifInfo | None = None) -> str:
    prefilter = build_prefilter(spec, info)
    if spec.background is not None:
        color = validate_color(spec.background)
        width, height = spec.canvas or (spec.width, spec.height)
        return (
            f"color=c={color}:s={width}x{height}:r={format_rate(spec.fps)}"
            f":d={format_rate(_background_duration(spec, info))}[bg];"
            f"[0:v]{prefilter}[fg];"
            f"[bg][fg]overlay=shortest=1:format=auto,format=rgb24[v]"
        )
    return f"[0:v]{prefilter}[v]"


def build_palette_args(
    binary: str, src, palette, spec: EncodeSpec, colors: int, info: GifInfo | None = None
) -> list[str]:
    graph = (
        build_input_graph(spec, info)
        + f";[v]palettegen=max_colors={colors}:stats_mode=diff:reserve_transparent=1[palette]"
    )
    args = [
        binary,
        "-y",
        "-v",
        "error",
        "-i",
        str(src),
        "-filter_complex",
        graph,
        "-map",
        "[palette]",
    ]
    if spec.max_duration_s is not None:
        args.extend(["-t", str(spec.max_duration_s)])
    args.append(str(palette))
    return args


def build_encode_args(
    binary: str, src, palette, dst, spec: EncodeSpec, colors: int, info: GifInfo | None = None
) -> list[str]:
    graph = (
        build_input_graph(spec, info)
        + ";[v][1:v]paletteuse=dither=bayer:bayer_scale=5:alpha_threshold=128[out]"
    )
    loop = -1 if spec.loop is None else int(spec.loop)
    args = [
        binary,
        "-y",
        "-v",
        "error",
        "-i",
        str(src),
        "-i",
        str(palette),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-loop",
        str(loop),
        "-fps_mode",
        "passthrough",
    ]
    if spec.max_duration_s is not None:
        args.extend(["-t", str(spec.max_duration_s)])
    args.append(str(dst))
    return args
