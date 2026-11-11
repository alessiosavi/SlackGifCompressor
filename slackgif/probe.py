from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from slackgif.config import GifInfo
from slackgif.errors import ProbeError

_GIF_HEADERS = (b"GIF87a", b"GIF89a")
_LOOP_APPS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")


def _read_exact(handle, count: int, offset: int) -> bytes:
    data = handle.read(count)
    if len(data) != count:
        raise ProbeError(f"truncated GIF at offset {offset}")
    return data


def parse_gif_metadata(path: Path) -> GifInfo:
    size_bytes = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(6)
        if header not in _GIF_HEADERS:
            raise ProbeError(f"not a GIF: {path}")

        descriptor = _read_exact(handle, 7, 6)
        width = int.from_bytes(descriptor[0:2], "little")
        height = int.from_bytes(descriptor[2:4], "little")
        packed = descriptor[4]
        if width <= 0 or height <= 0:
            raise ProbeError(f"invalid GIF dimensions {width}x{height}")
        if packed & 0x80:
            table_size = 3 * (2 ** ((packed & 0x07) + 1))
            _read_exact(handle, table_size, handle.tell())

        frames = 0
        delays: list[int] = []
        has_alpha = False
        pending_delay = 0
        pending_transparent = False
        loop: int | None = None
        saw_trailer = False

        while True:
            marker = handle.read(1)
            if not marker:
                break
            introducer = marker[0]
            offset = handle.tell() - 1

            if introducer == 0x3B:
                saw_trailer = True
                break

            if introducer == 0x21:
                label = _read_exact(handle, 1, offset)[0]
                if label == 0xF9:
                    block_size = _read_exact(handle, 1, offset)[0]
                    if block_size != 4:
                        raise ProbeError(
                            f"invalid graphic control extension at offset {offset}"
                        )
                    block = _read_exact(handle, 4, offset)
                    pending_delay = int.from_bytes(block[1:3], "little")
                    pending_transparent = bool(block[0] & 0x01)
                    terminator = _read_exact(handle, 1, offset)[0]
                    if terminator != 0:
                        raise ProbeError(
                            f"invalid graphic control block at offset {offset}"
                        )
                elif label == 0xFF:
                    app_size = _read_exact(handle, 1, offset)[0]
                    identifier = _read_exact(handle, app_size, offset)
                    blocks: list[bytes] = []
                    while True:
                        block_size = _read_exact(handle, 1, offset)[0]
                        if block_size == 0:
                            break
                        blocks.append(_read_exact(handle, block_size, offset))
                    if identifier.startswith(_LOOP_APPS):
                        for block in blocks:
                            if len(block) >= 3 and block[0] == 0x01:
                                loop = int.from_bytes(block[1:3], "little")
                                break
                else:
                    while True:
                        block_size = _read_exact(handle, 1, offset)[0]
                        if block_size == 0:
                            break
                        _read_exact(handle, block_size, offset)
                continue

            if introducer == 0x2C:
                image_descriptor = _read_exact(handle, 9, offset)
                image_packed = image_descriptor[8]
                if image_packed & 0x80:
                    table_size = 3 * (2 ** ((image_packed & 0x07) + 1))
                    _read_exact(handle, table_size, offset)
                _read_exact(handle, 1, offset)  # LZW minimum code size
                while True:
                    block_size = _read_exact(handle, 1, offset)[0]
                    if block_size == 0:
                        break
                    _read_exact(handle, block_size, offset)
                frames += 1
                delays.append(pending_delay)
                if frames == 1:
                    # Gifski (and some optimizers) set the transparency flag on
                    # delta frames even when the visible animation is opaque.
                    # The first frame cannot borrow pixels from an earlier frame,
                    # so its flag is the reliable signal for real alpha.
                    has_alpha = pending_transparent
                pending_delay = 0
                pending_transparent = False
                continue

            raise ProbeError(
                f"unexpected GIF block 0x{introducer:02x} at offset {offset}"
            )

        if not saw_trailer:
            raise ProbeError("truncated GIF: missing trailer")
        if frames == 0:
            raise ProbeError("GIF contains no image frames")

    duration_s = sum(delays) / 100.0
    fps = frames / duration_s if duration_s > 0 else 0.0
    return GifInfo(
        width=width,
        height=height,
        frames=frames,
        fps=fps,
        duration_s=duration_s,
        loop=loop,
        loop_known=True,
        has_alpha=has_alpha,
        variable_delays=len(set(delays)) > 1,
        size_bytes=size_bytes,
    )


def _probe_with_pillow(path: Path) -> GifInfo:
    try:
        from PIL import Image
    except ImportError as exc:
        raise ProbeError("Pillow is not installed") from exc

    try:
        with Image.open(path) as image:
            width, height = image.size
            frames = int(getattr(image, "n_frames", 1))
            initial_info = dict(image.info)
            durations: list[int] = []
            has_alpha = False
            for index in range(frames):
                image.seek(index)
                durations.append(int(image.info.get("duration", 0) or 0))
                if "transparency" in image.info:
                    has_alpha = True
            loop = initial_info.get("loop")
            loop_known = "loop" in initial_info
            variable = len(set(durations)) > 1 if any(durations) else None
    except ProbeError:
        raise
    except Exception as exc:  # Pillow raises several format-specific errors.
        raise ProbeError(f"Pillow could not read {path}: {exc}") from exc

    total_ms = sum(durations)
    duration_s = total_ms / 1000.0
    fps = frames / duration_s if duration_s > 0 else 0.0
    return GifInfo(
        width=width,
        height=height,
        frames=frames,
        fps=fps,
        duration_s=duration_s,
        loop=loop,
        loop_known=loop_known,
        has_alpha=has_alpha,
        variable_delays=variable,
        size_bytes=path.stat().st_size,
    )


def _parse_rate(value: str) -> float:
    if not value or value == "N/A":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        try:
            denominator_value = float(denominator)
            if denominator_value == 0:
                return 0.0
            return float(numerator) / denominator_value
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def _probe_with_ffprobe(path: Path, timeout_s: float) -> GifInfo:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise ProbeError("ffprobe is not installed")
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,nb_frames,duration",
        "-of",
        "csv=p=0",
        str(path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"ffprobe failed: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-1:]
        raise ProbeError(f"ffprobe failed: {tail[0] if tail else 'unknown error'}")

    lines = [line for line in result.stdout.strip().splitlines() if line]
    if not lines:
        raise ProbeError("ffprobe returned no stream metadata")
    parts = lines[0].split(",")
    if len(parts) < 2:
        raise ProbeError(f"unexpected ffprobe output: {lines[0]!r}")

    try:
        width = int(parts[0])
        height = int(parts[1])
    except ValueError as exc:
        raise ProbeError(f"unexpected ffprobe dimensions: {lines[0]!r}") from exc

    fps = _parse_rate(parts[2]) if len(parts) > 2 else 0.0
    frames_text = parts[3] if len(parts) > 3 else ""
    duration_text = parts[4] if len(parts) > 4 else ""
    frames = 0
    if frames_text not in ("", "N/A"):
        try:
            frames = int(frames_text)
        except ValueError:
            frames = 0
    try:
        duration_s = float(duration_text) if duration_text not in ("", "N/A") else 0.0
    except ValueError:
        duration_s = 0.0
    if frames <= 0 and fps > 0 and duration_s > 0:
        frames = max(1, round(fps * duration_s))
    if duration_s <= 0 and frames > 0 and fps > 0:
        duration_s = frames / fps

    return GifInfo(
        width=width,
        height=height,
        frames=frames,
        fps=fps,
        duration_s=duration_s,
        loop=None,
        loop_known=False,
        has_alpha=None,
        variable_delays=None,
        size_bytes=path.stat().st_size,
    )


def probe_gif(path: Path, *, timeout_s: float = 30.0) -> GifInfo:
    try:
        if not path.is_file():
            raise ProbeError(f"not a regular file: {path}")
        if path.stat().st_size <= 0:
            raise ProbeError(f"empty file: {path}")
    except OSError as exc:
        raise ProbeError(f"cannot read {path}: {exc}") from exc

    try:
        return parse_gif_metadata(path)
    except ProbeError as parser_error:
        try:
            return _probe_with_pillow(path)
        except ProbeError as pillow_error:
            try:
                return _probe_with_ffprobe(path, timeout_s)
            except ProbeError as ffprobe_error:
                raise ProbeError(
                    f"could not probe {path}: {parser_error}; {pillow_error}; {ffprobe_error}"
                ) from ffprobe_error
