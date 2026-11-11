#!/usr/bin/env python3
"""Manual acceptance benchmark for the real GIF used during development."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MESSAGE_TARGET = 5 * 1024 * 1024
EMOJI_TARGET = 128 * 1024


def _run_case(source: Path, output: Path, extra: list[str]) -> tuple[int, dict, str]:
    command = [
        sys.executable,
        "-m",
        "slackgif",
        str(source),
        "-o",
        str(output),
        "--json",
        *extra,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    payload: dict = {}
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            payload = {}
    return result.returncode, payload, result.stderr


def _fmt_bytes(value) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    if value >= 1024**2:
        return f"{value / 1024**2:.2f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{int(value)} B"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source",
        nargs="?",
        default=os.environ.get("SLACKGIF_BENCH_GIF")
        or str(Path.home() / "Downloads" / "meme723-1794423241586.gif"),
    )
    parser.add_argument("--assert", dest="check", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=10.0)
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    if not source.exists():
        print(f"source GIF not found: {source}", file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="slackgif-bench-"))
    cases = [
        ("message", workdir / "message.gif", ["--preset", "message"]),
        ("emoji", workdir / "emoji.gif", ["--preset", "emoji"]),
        (
            "ffmpeg",
            workdir / "ffmpeg.gif",
            ["--preset", "message", "--backend", "ffmpeg"],
        ),
    ]

    results = []
    print(f"source: {source}")
    print("case      exit  size          dimensions  frames  encodes  seconds")
    for name, output, extra in cases:
        code, payload, stderr = _run_case(source, output, extra)
        results.append((name, code, payload, output, stderr))
        dimensions = f"{payload.get('width')}x{payload.get('height')}"
        print(
            f"{name:<9} {code:>4}  {_fmt_bytes(payload.get('output_bytes')):<12}  "
            f"{dimensions:<10}  {str(payload.get('frames')):<6}  "
            f"{str(payload.get('encodes')):<7}  {payload.get('seconds')}"
        )

    if not args.check:
        return 0

    failures = []
    for name, code, payload, _output, stderr in results:
        if code != 0:
            failures.append(f"{name}: exit {code}: {stderr.strip()}")
            continue
        size = payload.get("output_bytes")
        width = payload.get("width")
        height = payload.get("height")
        frames = payload.get("frames")
        encodes = payload.get("encodes")
        seconds = payload.get("seconds")
        if not isinstance(size, int):
            failures.append(f"{name}: no output size")
            continue
        if name == "message":
            if size > MESSAGE_TARGET:
                failures.append(f"message: {size} bytes over target")
            if (width, height) != (500, 625):
                failures.append(f"message: {width}x{height} != 500x625")
            if not isinstance(encodes, int) or encodes > 10:
                failures.append(f"message: encodes {encodes} > 10")
            if isinstance(seconds, (int, float)) and seconds > args.max_seconds:
                failures.append(f"message: {seconds:.2f}s > {args.max_seconds:.2f}s")
        elif name == "emoji":
            if size > EMOJI_TARGET:
                failures.append(f"emoji: {size} bytes over target")
            if (width, height) != (128, 128):
                failures.append(f"emoji: {width}x{height} != 128x128")
            if not isinstance(frames, int) or frames > 50:
                failures.append(f"emoji: frames {frames} > 50")
        elif name == "ffmpeg":
            minimum = 360 if shutil.which("gifsicle") else 340
            if size > MESSAGE_TARGET:
                failures.append(f"ffmpeg: {size} bytes over target")
            if not isinstance(width, int) or width < minimum:
                failures.append(f"ffmpeg: width {width} < {minimum}")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("\nall assertions passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
