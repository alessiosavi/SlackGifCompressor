from __future__ import annotations

import json
import sys
from pathlib import Path

from slackgif.config import GifInfo, Result


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value >= 1024**2:
        return f"{value / 1024**2:.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def _output_size(result: Result) -> int | None:
    if result.output is None:
        return None
    try:
        return result.output.stat().st_size
    except OSError:
        return None


def report_human(
    result: Result,
    *,
    info: GifInfo | None = None,
    preset_name: str | None = None,
    source: Path | None = None,
    quiet: bool = False,
    verbose: bool = False,
) -> None:
    output_size = _output_size(result)
    quality = result.candidate.spec.quality if result.candidate is not None else None

    print(f"Input: {source}" if source is not None else "Input: unknown")
    print(
        f"Output: {result.output}"
        if result.output is not None
        else "Output: not written"
    )
    if preset_name is not None:
        print(f"Preset: {preset_name}")
    print(f"Backend: {result.backend}")
    print("Result: success" if result.success else "Result: best effort (over target)")
    if output_size is not None:
        target = (
            f"; target {_format_bytes(result.target_bytes)}"
            if result.target_bytes
            else ""
        )
        print(f"Size: {_format_bytes(output_size)} ({output_size} bytes{target})")
    if info is not None:
        print(f"Dimensions: {info.width}x{info.height}")
        print(f"Frames: {info.frames}")
        print(f"FPS: {info.fps:.2f}")
    if quality is not None:
        print(f"Quality: {quality}")
    print(f"Encodes: {result.encodes}")
    print(f"Time: {result.seconds:.2f}s")

    if not quiet:
        for warning in result.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    if verbose and result.error:
        print(f"error: {result.error}", file=sys.stderr)


def report_json(
    result: Result,
    *,
    info: GifInfo | None = None,
    preset_name: str | None = None,
    source: Path | None = None,
) -> str:
    output_size = _output_size(result)
    quality = result.candidate.spec.quality if result.candidate is not None else None
    payload = {
        "input": str(source) if source is not None else None,
        "output": str(result.output) if result.output is not None else None,
        "preset": preset_name,
        "backend": result.backend,
        "success": result.success,
        "target_bytes": result.target_bytes,
        "output_bytes": output_size,
        "width": info.width if info is not None else None,
        "height": info.height if info is not None else None,
        "fps": info.fps if info is not None else None,
        "frames": info.frames if info is not None else None,
        "quality": quality,
        "encodes": result.encodes,
        "seconds": round(result.seconds, 3),
        "warnings": list(result.warnings),
        "error": result.error,
    }
    return json.dumps(payload, ensure_ascii=True)
