from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
import traceback
from pathlib import Path

from slackgif import __version__
from slackgif.backends.ffmpeg import FfmpegBackend
from slackgif.backends.gifski import GifskiBackend
from slackgif.backends.pillow import PillowBackend
from slackgif.config import GifInfo, Preset, resolve_preset, validate_limits
from slackgif.errors import (
    BackendError,
    OutputError,
    ProbeError,
    SlackGifError,
    UsageError,
    ValidationError,
)
from slackgif.probe import probe_gif
from slackgif.report import report_human, report_json
from slackgif.search import compress


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gifc",
        description="Compress animated GIFs for Slack with a byte budget.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("input", nargs="?", help="input GIF")

    parser.add_argument(
        "--preset",
        choices=("message", "emoji", "custom"),
        default="message",
        help="message (default, 5 MiB), emoji (128 KiB, 128x128), or custom",
    )
    parser.add_argument(
        "-t",
        "--target",
        "--max-size",
        dest="max_size",
        help="byte target, e.g. 5MB, 128KB, or 5120 (plain number = KB)",
    )
    parser.add_argument(
        "--quality", type=int, help="fixed quality 1-100; skips quality search"
    )
    parser.add_argument(
        "--min-quality", type=int, help="minimum quality for the search"
    )
    parser.add_argument(
        "--quality-mode",
        choices=("balanced", "resolution", "motion"),
        help="how to spend a size reduction between width and fps",
    )
    parser.add_argument("--max-width", type=int)
    parser.add_argument("--max-height", type=int)
    parser.add_argument("--max-fps", type=float)
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--max-duration", type=float, help="trim to this many seconds")
    parser.add_argument("--fit", choices=("contain", "cover", "pad"))
    parser.add_argument(
        "--keep-transparency",
        action="store_true",
        help="preserve transparency (default when the source has alpha)",
    )
    parser.add_argument("--background", help="flatten transparency onto this color")
    parser.add_argument("--loop", help="preserve, forever, once, or a loop count")
    parser.add_argument("--trim", help="deferred; returns exit 2")
    parser.add_argument(
        "--backend", choices=("auto", "gifski", "ffmpeg", "pillow"), default="auto"
    )
    parser.add_argument("--pillow", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-o", "--output")
    parser.add_argument("--output-dir", help="deferred; returns exit 2")
    parser.add_argument("--force", action="store_true", help="overwrite the output")
    parser.add_argument(
        "--json", action="store_true", help="emit one JSON object on stdout"
    )
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="report the plan without encoding"
    )
    parser.add_argument("--max-encodes", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--allow-large", action="store_true")
    return parser


def _apply_overrides(preset: Preset, args: argparse.Namespace) -> Preset:
    policy = preset.policy
    changes = {}
    for name in (
        "min_quality",
        "max_width",
        "max_height",
        "max_fps",
        "max_frames",
        "max_duration_s",
    ):
        value = getattr(args, name, None)
        if value is not None:
            changes[name] = value
    if args.quality_mode is not None:
        changes["quality_mode"] = args.quality_mode
    if args.max_encodes is not None:
        changes["max_encodes"] = args.max_encodes
    if args.timeout is not None:
        changes["timeout_s"] = args.timeout
    if args.allow_large:
        changes["allow_large"] = True
    if changes:
        policy = dataclasses.replace(policy, **changes)

    if not 1 <= policy.min_quality <= 100:
        raise UsageError("--min-quality must be between 1 and 100")
    if policy.max_encodes < 1:
        raise UsageError("--max-encodes must be at least 1")
    if policy.timeout_s <= 0:
        raise UsageError("--timeout must be positive")
    if policy.max_width < 1 or policy.max_height < 1:
        raise UsageError("--max-width/--max-height must be positive")
    if policy.max_fps is not None and policy.max_fps <= 0:
        raise UsageError("--max-fps must be positive")
    if policy.max_frames is not None and policy.max_frames < 1:
        raise UsageError("--max-frames must be at least 1")
    if policy.max_duration_s is not None and policy.max_duration_s <= 0:
        raise UsageError("--max-duration must be positive")

    if preset.fixed_canvas is not None:
        canvas_width, canvas_height = preset.fixed_canvas
        if args.max_width is not None and args.max_width != canvas_width:
            raise UsageError("the emoji preset has a fixed 128x128 canvas")
        if args.max_height is not None and args.max_height != canvas_height:
            raise UsageError("the emoji preset has a fixed 128x128 canvas")
        policy = dataclasses.replace(
            policy, max_width=canvas_width, max_height=canvas_height
        )
    return dataclasses.replace(preset, policy=policy)


def _requested_backend(args: argparse.Namespace) -> str:
    if args.pillow:
        if args.backend not in ("auto", "pillow"):
            raise UsageError("--pillow conflicts with --backend")
        return "pillow"
    return args.backend


def select_backends(
    requested: str, *, info: GifInfo, preset: Preset, workspace: Path, timeout_s: float
) -> list:
    builders = {
        "gifski": GifskiBackend,
        "ffmpeg": FfmpegBackend,
        "pillow": PillowBackend,
    }
    if requested != "auto":
        backend = builders[requested](workspace, timeout_s)
        if not backend.available():
            raise BackendError(f"{requested} is not installed")
        if not backend.can_handle(info, preset):
            raise BackendError(
                f"{requested} cannot handle this preset; install ffmpeg or choose "
                "another backend"
            )
        return [backend]

    backends = []
    for name in ("gifski", "ffmpeg", "pillow"):
        backend = builders[name](workspace, timeout_s)
        if backend.available() and backend.can_handle(info, preset):
            backends.append(backend)
        else:
            backend.close()
    if not backends:
        raise BackendError(
            "no GIF backend is available; install gifski or ffmpeg, or install Pillow"
        )
    return backends


def _output_path(args: argparse.Namespace, input_path: Path) -> Path:
    if args.output is not None:
        return Path(args.output)
    return input_path.with_name(f"{input_path.stem}_compressed.gif")


def _print_dry_run(
    input_path: Path, preset: Preset, info: GifInfo, output_path: Path, requested: str
) -> None:
    policy = preset.policy
    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"Preset: {preset.name}")
    print(
        f"Target: {policy.target_bytes if policy.target_bytes is not None else 'none'}"
    )
    print(f"Max dimensions: {policy.max_width}x{policy.max_height}")
    print(f"Max fps: {policy.max_fps if policy.max_fps is not None else 'none'}")
    print(f"Backend: {requested}")
    print(
        f"Source: {info.width}x{info.height}, {info.frames} frames, "
        f"{info.fps:.2f} fps, {info.size_bytes} bytes"
    )


def _run(args: argparse.Namespace) -> int:
    if args.trim is not None:
        print("error: --trim is not implemented in this version", file=sys.stderr)
        return 2
    if args.output_dir is not None:
        print("error: --output-dir is not implemented in this version", file=sys.stderr)
        return 2
    if args.input is None:
        print("error: an input GIF is required", file=sys.stderr)
        return 2

    input_path = Path(args.input)
    preset = _apply_overrides(
        resolve_preset(
            args.preset,
            max_size=args.max_size,
            quality=args.quality,
            loop=args.loop,
            fit=args.fit,
            background=args.background,
            keep_transparency=True if args.keep_transparency else None,
        ),
        args,
    )
    info = probe_gif(input_path)
    validate_limits(info, preset.policy)
    output_path = _output_path(args, input_path)
    requested = _requested_backend(args)

    if args.dry_run:
        _print_dry_run(input_path, preset, info, output_path, requested)
        return 0

    from slackgif.output import ensure_safe_paths

    ensure_safe_paths(input_path, output_path, force=args.force)

    warnings: list[str] = []
    result = None
    with tempfile.TemporaryDirectory(prefix="slackgif-backends-") as workspace_name:
        backends = select_backends(
            requested,
            info=info,
            preset=preset,
            workspace=Path(workspace_name),
            timeout_s=preset.policy.timeout_s,
        )
        for backend in backends:
            if not args.quiet and not args.json:
                print(f"Encoding with {backend.name}...", file=sys.stderr)
            try:
                result = compress(
                    input_path, output_path, preset=preset, backend=backend, info=info
                )
                if warnings:
                    result = dataclasses.replace(
                        result, warnings=tuple(warnings) + result.warnings
                    )
                break
            except OutputError:
                raise
            except (BackendError, ValidationError) as exc:
                warnings.append(f"{backend.name}: {exc}")
                continue

    if result is None:
        raise BackendError("all backends failed: " + "; ".join(warnings))
    if result.output is None:
        raise BackendError(result.error or "no output was written")

    try:
        output_info = probe_gif(result.output)
    except ProbeError:
        output_info = info

    if args.json:
        print(
            report_json(
                result,
                info=output_info,
                preset_name=preset.name,
                source=input_path,
            )
        )
    else:
        report_human(
            result,
            info=output_info,
            preset_name=preset.name,
            source=input_path,
            quiet=args.quiet,
            verbose=args.verbose,
        )

    return 0 if result.success else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        return _run(args)
    except UsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("error: interrupted", file=sys.stderr)
        return 130
    except (ProbeError, BackendError, ValidationError, OutputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SlackGifError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        if getattr(args, "verbose", False):
            traceback.print_exc()
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
