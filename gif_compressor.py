#!/usr/bin/env python3
"""
GIF Compressor for Slack Compatibility

A robust script to compress animated GIFs to under 128 KB using ffmpeg (preferred)
or Pillow as a fallback. Implements iterative optimization through scaling,
frame dropping, and color palette reduction.

Usage:
    python gif_compressor.py input.gif [-o output.gif] [-t 128]
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple

# Target size in KB
DEFAULT_TARGET_KB = 128
MIN_WIDTH = 200
SCALE_STEP = 0.9  # 10% reduction per iteration
MIN_COLORS = 32
MAX_FRAME_SKIP = 4  # Keep every Nth frame (max)


def check_ffmpeg_available() -> bool:
    """Check if ffmpeg is available in the system PATH."""
    return shutil.which("ffmpeg") is not None


def get_file_size_kb(filepath: str) -> float:
    """Get file size in kilobytes."""
    return os.path.getsize(filepath) / 1024


def print_progress(step: str, size_kb: float, target_kb: float) -> None:
    """Print current progress with file size."""
    status = "✓" if size_kb < target_kb else "→"
    print(f"  {status} {step}: {size_kb:.1f} KB (target: {target_kb} KB)")


def get_gif_info_ffmpeg(filepath: str) -> Tuple[int, int, int, float]:
    """
    Get GIF dimensions, frame count, and frame rate using ffprobe.
    Returns: (width, height, frame_count, fps)
    """
    try:
        # Get dimensions and frame rate
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
                "-of", "csv=p=0",
                filepath,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        parts = result.stdout.strip().split(",")
        width = int(parts[0])
        height = int(parts[1])

        # Parse frame rate (format: "num/den")
        fps_parts = parts[2].split("/")
        fps = float(fps_parts[0]) / float(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])

        # Frame count might be N/A for GIFs, count manually if needed
        frame_count = int(parts[3]) if parts[3] != "N/A" else None

        if frame_count is None:
            # Count frames manually
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v", "error",
                    "-count_frames",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=nb_read_frames",
                    "-of", "csv=p=0",
                    filepath,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            frame_count = int(result.stdout.strip())

        return width, height, frame_count, fps
    except (subprocess.CalledProcessError, ValueError, IndexError) as e:
        raise RuntimeError(f"Failed to get GIF info: {e}")


def compress_gif_ffmpeg(
    input_path: str,
    output_path: str,
    scale: Optional[float] = None,
    width: Optional[int] = None,
    fps: Optional[float] = None,
    colors: int = 256,
    frame_skip: int = 1,
) -> bool:
    """
    Compress GIF using ffmpeg with specified parameters.

    Args:
        input_path: Path to input GIF
        output_path: Path to output GIF
        scale: Scale factor (0.0-1.0) - mutually exclusive with width
        width: Target width in pixels - mutually exclusive with scale
        fps: Target frame rate
        colors: Number of colors in palette (2-256)
        frame_skip: Keep every Nth frame (1 = keep all, 2 = keep every 2nd, etc.)

    Returns:
        True if successful, False otherwise
    """
    try:
        filters = []

        # Frame skipping using select filter
        if frame_skip > 1:
            filters.append(f"select='not(mod(n\\,{frame_skip}))'")

        # FPS adjustment
        if fps:
            filters.append(f"fps={fps}")

        # Scaling
        if scale and scale < 1.0:
            filters.append(f"scale=iw*{scale}:ih*{scale}:flags=lanczos")
        elif width:
            filters.append(f"scale={width}:-1:flags=lanczos")

        # Build the palettegen and paletteuse filter for optimal GIF encoding
        # Split the filter into two passes for better quality
        filter_complex = []

        if filters:
            filter_complex.append(",".join(filters))

        # Add palette generation with specified colors
        palette_filters = f"split[s0][s1];[s0]palettegen=max_colors={colors}:stats_mode=diff[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5"

        if filter_complex:
            full_filter = f"{filter_complex[0]},{palette_filters}"
        else:
            full_filter = palette_filters

        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-i", input_path,
            "-filter_complex", full_filter,
            "-loop", "0",  # Preserve infinite loop
            output_path,
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )

        return result.returncode == 0 and os.path.exists(output_path)

    except Exception as e:
        print(f"  Error during ffmpeg compression: {e}", file=sys.stderr)
        return False


def compress_with_ffmpeg(
    input_path: str,
    output_path: str,
    target_kb: float,
) -> Tuple[bool, float]:
    """
    Main compression loop using ffmpeg.

    Returns:
        Tuple of (success, final_size_kb)
    """
    print("\n[ffmpeg] Starting GIF compression...")

    # Get original info
    try:
        orig_width, orig_height, frame_count, orig_fps = get_gif_info_ffmpeg(input_path)
        print(f"  Original: {orig_width}x{orig_height}, {frame_count} frames, {orig_fps:.1f} fps")
    except RuntimeError as e:
        print(f"  Error: {e}", file=sys.stderr)
        return False, get_file_size_kb(input_path)

    orig_size = get_file_size_kb(input_path)
    print(f"  Original size: {orig_size:.1f} KB")

    # Check if already under target
    if orig_size < target_kb:
        print(f"  Already under {target_kb} KB, copying as-is.")
        shutil.copy2(input_path, output_path)
        return True, orig_size

    # Use temp file for intermediate results
    with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tmp:
        temp_path = tmp.name

    best_path = None
    best_size = orig_size

    try:
        # Phase 1: Scaling
        print("\n[Phase 1] Scaling down...")
        current_scale = 1.0
        current_width = orig_width

        while current_width > MIN_WIDTH:
            current_scale *= SCALE_STEP
            current_width = int(orig_width * current_scale)

            if current_width < MIN_WIDTH:
                current_width = MIN_WIDTH

            success = compress_gif_ffmpeg(
                input_path, temp_path,
                width=current_width,
                colors=256,
            )

            if success:
                size = get_file_size_kb(temp_path)
                print_progress(f"Scale to {current_width}px", size, target_kb)

                if size < best_size:
                    best_size = size
                    if best_path and best_path != temp_path:
                        os.unlink(best_path)
                    best_path = temp_path
                    temp_path = tempfile.NamedTemporaryFile(suffix=".gif", delete=False).name

                if size < target_kb:
                    shutil.copy2(best_path, output_path)
                    return True, size

            if current_width <= MIN_WIDTH:
                break

        # Phase 2: Frame dropping
        print("\n[Phase 2] Dropping frames...")
        scale_width = min(current_width, orig_width)

        for frame_skip in range(2, MAX_FRAME_SKIP + 1):
            success = compress_gif_ffmpeg(
                input_path, temp_path,
                width=scale_width,
                frame_skip=frame_skip,
                colors=256,
            )

            if success:
                size = get_file_size_kb(temp_path)
                print_progress(f"Keep every {frame_skip}th frame", size, target_kb)

                if size < best_size:
                    best_size = size
                    if best_path and best_path != temp_path:
                        os.unlink(best_path)
                    best_path = temp_path
                    temp_path = tempfile.NamedTemporaryFile(suffix=".gif", delete=False).name

                if size < target_kb:
                    shutil.copy2(best_path, output_path)
                    return True, size

        # Phase 3: Color reduction
        print("\n[Phase 3] Reducing colors...")
        best_frame_skip = MAX_FRAME_SKIP

        for colors in [128, 64, MIN_COLORS]:
            success = compress_gif_ffmpeg(
                input_path, temp_path,
                width=scale_width,
                frame_skip=best_frame_skip,
                colors=colors,
            )

            if success:
                size = get_file_size_kb(temp_path)
                print_progress(f"Reduce to {colors} colors", size, target_kb)

                if size < best_size:
                    best_size = size
                    if best_path and best_path != temp_path:
                        os.unlink(best_path)
                    best_path = temp_path
                    temp_path = tempfile.NamedTemporaryFile(suffix=".gif", delete=False).name

                if size < target_kb:
                    shutil.copy2(best_path, output_path)
                    return True, size

        # Phase 4: Aggressive combined optimization
        print("\n[Phase 4] Aggressive optimization...")
        for width in [MIN_WIDTH, 150, 100]:
            for colors in [64, 32, 16]:
                for frame_skip in range(2, MAX_FRAME_SKIP + 1):
                    success = compress_gif_ffmpeg(
                        input_path, temp_path,
                        width=width,
                        frame_skip=frame_skip,
                        colors=colors,
                    )

                    if success:
                        size = get_file_size_kb(temp_path)
                        print_progress(f"{width}px, {colors} colors, 1/{frame_skip} frames", size, target_kb)

                        if size < best_size:
                            best_size = size
                            if best_path and best_path != temp_path:
                                os.unlink(best_path)
                            best_path = temp_path
                            temp_path = tempfile.NamedTemporaryFile(suffix=".gif", delete=False).name

                        if size < target_kb:
                            shutil.copy2(best_path, output_path)
                            return True, size

        # Return best result even if over target
        if best_path:
            shutil.copy2(best_path, output_path)
            return False, best_size

        return False, orig_size

    finally:
        # Cleanup temp files
        for path in [temp_path, best_path]:
            if path and os.path.exists(path) and path != output_path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def compress_with_pillow(
    input_path: str,
    output_path: str,
    target_kb: float,
) -> Tuple[bool, float]:
    """
    Fallback compression using Pillow.

    Returns:
        Tuple of (success, final_size_kb)
    """
    try:
        from PIL import Image
    except ImportError:
        print("Error: Pillow is not installed. Install with: pip install Pillow", file=sys.stderr)
        return False, get_file_size_kb(input_path)

    print("\n[Pillow] Starting GIF compression...")

    orig_size = get_file_size_kb(input_path)
    print(f"  Original size: {orig_size:.1f} KB")

    if orig_size < target_kb:
        print(f"  Already under {target_kb} KB, copying as-is.")
        shutil.copy2(input_path, output_path)
        return True, orig_size

    # Load original GIF
    img = Image.open(input_path)
    orig_width, orig_height = img.size

    # Extract all frames
    frames = []
    durations = []

    try:
        while True:
            # Convert to RGBA to handle transparency properly
            frame = img.convert("RGBA")
            frames.append(frame)
            durations.append(img.info.get("duration", 100))
            img.seek(img.tell() + 1)
    except EOFError:
        pass

    print(f"  Original: {orig_width}x{orig_height}, {len(frames)} frames")

    loop = img.info.get("loop", 0)
    best_size = orig_size
    best_output = None

    def save_gif(frames_to_save, durations_to_save, width, colors):
        """Helper to save GIF and return size."""
        nonlocal best_size, best_output

        # Resize frames
        if width != orig_width:
            ratio = width / orig_width
            new_height = int(orig_height * ratio)
            resized = [f.resize((width, new_height), Image.LANCZOS) for f in frames_to_save]
        else:
            resized = frames_to_save

        # Quantize to reduce colors
        quantized = []
        for frame in resized:
            # Convert to palette mode with specified colors
            q = frame.convert("RGB").quantize(colors=colors, method=Image.MEDIANCUT)
            quantized.append(q)

        with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as tmp:
            temp_path = tmp.name

        # Save animated GIF
        quantized[0].save(
            temp_path,
            save_all=True,
            append_images=quantized[1:],
            duration=durations_to_save,
            loop=loop,
            optimize=True,
        )

        size = get_file_size_kb(temp_path)

        if size < best_size:
            best_size = size
            if best_output:
                try:
                    os.unlink(best_output)
                except OSError:
                    pass
            best_output = temp_path
        else:
            os.unlink(temp_path)

        return size

    try:
        # Phase 1: Scaling
        print("\n[Phase 1] Scaling down...")
        current_width = orig_width

        while current_width > MIN_WIDTH:
            current_width = int(current_width * SCALE_STEP)
            if current_width < MIN_WIDTH:
                current_width = MIN_WIDTH

            size = save_gif(frames, durations, current_width, 256)
            print_progress(f"Scale to {current_width}px", size, target_kb)

            if size < target_kb:
                shutil.copy2(best_output, output_path)
                return True, size

        # Phase 2: Frame dropping
        print("\n[Phase 2] Dropping frames...")
        scale_width = min(current_width, orig_width)

        for frame_skip in range(2, MAX_FRAME_SKIP + 1):
            reduced_frames = frames[::frame_skip]
            reduced_durations = [d * frame_skip for d in durations[::frame_skip]]

            size = save_gif(reduced_frames, reduced_durations, scale_width, 256)
            print_progress(f"Keep every {frame_skip}th frame", size, target_kb)

            if size < target_kb:
                shutil.copy2(best_output, output_path)
                return True, size

        # Phase 3: Color reduction
        print("\n[Phase 3] Reducing colors...")
        reduced_frames = frames[::MAX_FRAME_SKIP]
        reduced_durations = [d * MAX_FRAME_SKIP for d in durations[::MAX_FRAME_SKIP]]

        for colors in [128, 64, MIN_COLORS]:
            size = save_gif(reduced_frames, reduced_durations, scale_width, colors)
            print_progress(f"Reduce to {colors} colors", size, target_kb)

            if size < target_kb:
                shutil.copy2(best_output, output_path)
                return True, size

        # Phase 4: Aggressive optimization
        print("\n[Phase 4] Aggressive optimization...")
        for width in [MIN_WIDTH, 150, 100]:
            for colors in [64, 32, 16]:
                for skip in range(2, MAX_FRAME_SKIP + 1):
                    rf = frames[::skip]
                    rd = [d * skip for d in durations[::skip]]

                    size = save_gif(rf, rd, width, colors)
                    print_progress(f"{width}px, {colors} colors, 1/{skip} frames", size, target_kb)

                    if size < target_kb:
                        shutil.copy2(best_output, output_path)
                        return True, size

        # Return best result
        if best_output:
            shutil.copy2(best_output, output_path)
            return False, best_size

        return False, orig_size

    finally:
        if best_output and os.path.exists(best_output) and best_output != output_path:
            try:
                os.unlink(best_output)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description="Compress animated GIF to target size for Slack compatibility",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s input.gif                    # Compress to 128 KB, output to input_compressed.gif
  %(prog)s input.gif -o output.gif      # Specify output path
  %(prog)s input.gif -t 64              # Compress to 64 KB target
  %(prog)s input.gif --pillow           # Force Pillow backend
        """,
    )
    parser.add_argument("input", help="Input GIF file path")
    parser.add_argument("-o", "--output", help="Output GIF file path (default: input_compressed.gif)")
    parser.add_argument(
        "-t", "--target",
        type=float,
        default=DEFAULT_TARGET_KB,
        help=f"Target size in KB (default: {DEFAULT_TARGET_KB})",
    )
    parser.add_argument(
        "--pillow",
        action="store_true",
        help="Force using Pillow instead of ffmpeg",
    )

    args = parser.parse_args()

    # Validate input
    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if not input_path.suffix.lower() == ".gif":
        print("Warning: Input file may not be a GIF", file=sys.stderr)

    # Set output path
    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = input_path.with_stem(f"{input_path.stem}_compressed")

    # Ensure output directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")
    print(f"Target: {args.target} KB")

    # Choose compression backend
    use_ffmpeg = check_ffmpeg_available() and not args.pillow

    if use_ffmpeg:
        success, final_size = compress_with_ffmpeg(str(input_path), str(output_path), args.target)
    else:
        if not args.pillow:
            print("\nNote: ffmpeg not found, falling back to Pillow")
        success, final_size = compress_with_pillow(str(input_path), str(output_path), args.target)

    # Final report
    print("\n" + "=" * 50)
    orig_size = get_file_size_kb(str(input_path))
    reduction = ((orig_size - final_size) / orig_size) * 100

    if success:
        print(f"✓ Success! Compressed to {final_size:.1f} KB ({reduction:.1f}% reduction)")
        print(f"  Output: {output_path}")
    else:
        print(f"⚠ Warning: Could not reach target {args.target} KB")
        print(f"  Best result: {final_size:.1f} KB ({reduction:.1f}% reduction)")
        print(f"  Output: {output_path}")
        print("\nTips to further reduce size:")
        print("  - Crop unnecessary parts of the GIF")
        print("  - Shorten the animation duration")
        print("  - Consider converting to a video format (MP4)")

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
