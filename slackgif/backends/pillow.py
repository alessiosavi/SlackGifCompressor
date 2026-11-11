from __future__ import annotations

import bisect
import importlib.util
import math
from pathlib import Path

from slackgif.backends.quality import QUALITY_LEVELS, quality_to_colors
from slackgif.config import EncodeSpec, GifInfo, Preset
from slackgif.errors import BackendError

_TRANSPARENT_INDEX = 255


def _pixel_data(image) -> list:
    getter = getattr(image, "get_flattened_data", None)
    if getter is not None:
        return list(getter())
    return list(image.getdata())


class PillowBackend:
    name = "pillow"
    quality_levels = QUALITY_LEVELS

    def __init__(self, workspace: Path, timeout_s: float = 120.0):
        self.workspace = Path(workspace)
        self.timeout_s = timeout_s

    def available(self) -> bool:
        return importlib.util.find_spec("PIL") is not None

    def can_handle(self, info: GifInfo, preset: Preset) -> bool:
        return self.available()

    def _load(self, src: Path) -> tuple[list, list[int]]:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = 100_000_000
        frames = []
        durations = []
        try:
            with Image.open(src) as image:
                for index in range(int(getattr(image, "n_frames", 1))):
                    image.seek(index)
                    frames.append(image.convert("RGBA"))
                    durations.append(max(0, int(image.info.get("duration", 0) or 0)))
        except Exception as exc:
            raise BackendError(f"Pillow could not read {src}: {exc}") from exc
        if not frames:
            raise BackendError(f"Pillow found no frames in {src}")
        return frames, durations

    def _truncate(
        self, frames: list, durations: list[int], max_duration_s: float | None
    ) -> tuple[list, list[int]]:
        if max_duration_s is None:
            return frames, durations
        limit = max(1, int(round(max_duration_s * 1000)))
        kept_frames = []
        kept_durations = []
        total = 0
        for frame, duration in zip(frames, durations):
            if total >= limit:
                break
            take = duration
            if total + take > limit:
                take = limit - total
            kept_frames.append(frame)
            kept_durations.append(max(1, take))
            total += max(1, take)
        if not kept_frames:
            return [frames[0]], [limit]
        return kept_frames, kept_durations

    def _resample(
        self, frames: list, durations: list[int], fps: float, max_frames: int | None
    ) -> tuple[list, list[int]]:
        total = sum(durations)
        if total <= 0 or fps <= 0:
            return frames, durations

        count = max(1, int(round(total * fps / 1000)))
        if max_frames is not None and count > max_frames:
            effective_fps = max_frames / (total / 1000.0) if max_frames > 0 else 0.0
            count = max(1, int(round(total * effective_fps / 1000)))
            count = min(count, max_frames)
        count = max(1, count)

        boundaries = [round(index * total / count) for index in range(count + 1)]
        starts = []
        elapsed = 0
        for duration in durations:
            starts.append(elapsed)
            elapsed += duration

        out_frames = []
        out_durations = []
        for index in range(count):
            midpoint = (boundaries[index] + boundaries[index + 1]) // 2
            source_index = bisect.bisect_right(starts, midpoint) - 1
            source_index = max(0, min(len(frames) - 1, source_index))
            out_frames.append(frames[source_index])
            out_durations.append(max(1, boundaries[index + 1] - boundaries[index]))
        return out_frames, out_durations

    def _fit(self, frame, spec: EncodeSpec):
        from PIL import Image

        width, height = frame.size
        if spec.canvas is None:
            return frame.resize((max(1, spec.width), max(1, spec.height)), Image.LANCZOS)

        canvas_width, canvas_height = spec.canvas
        if spec.fit == "cover":
            ratio = max(canvas_width / width, canvas_height / height)
            new_width = max(1, int(math.ceil(width * ratio)))
            new_height = max(1, int(math.ceil(height * ratio)))
            resized = frame.resize((new_width, new_height), Image.LANCZOS)
            left = max(0, (new_width - canvas_width) // 2)
            top = max(0, (new_height - canvas_height) // 2)
            return resized.crop((left, top, left + canvas_width, top + canvas_height))

        ratio = min(canvas_width / width, canvas_height / height)
        new_width = max(1, int(round(width * ratio)))
        new_height = max(1, int(round(height * ratio)))
        resized = frame.resize((new_width, new_height), Image.LANCZOS)
        canvas = Image.new("RGBA", (canvas_width, canvas_height), (0, 0, 0, 0))
        canvas.paste(
            resized,
            ((canvas_width - new_width) // 2, (canvas_height - new_height) // 2),
        )
        return canvas

    def _flatten(self, frame, color: str):
        from PIL import Image, ImageColor

        try:
            rgb = ImageColor.getrgb(color)
        except ValueError as exc:
            raise BackendError(f"invalid background color: {color!r}") from exc
        if len(rgb) == 3:
            rgb = (*rgb, 255)
        background = Image.new("RGBA", frame.size, rgb)
        return Image.alpha_composite(background, frame).convert("RGB")

    def _quantize(self, frame, colors: int, preserve_alpha: bool):
        from PIL import Image

        if not preserve_alpha:
            return frame.convert("RGB").quantize(
                colors=max(2, min(256, colors)),
                method=Image.MEDIANCUT,
                dither=Image.FLOYDSTEINBERG,
            )

        rgb = frame.convert("RGB")
        quantized = rgb.quantize(
            colors=max(2, min(255, colors)),
            method=Image.MEDIANCUT,
            dither=Image.FLOYDSTEINBERG,
        )
        palette = list(quantized.getpalette() or [])
        if len(palette) < 768:
            palette.extend([0] * (768 - len(palette)))
        quantized.putpalette(palette[:768])

        alpha_data = _pixel_data(frame.getchannel("A"))
        if any(alpha < 128 for alpha in alpha_data):
            data = _pixel_data(quantized)
            for index, alpha in enumerate(alpha_data):
                if alpha < 128:
                    data[index] = _TRANSPARENT_INDEX
                elif data[index] == _TRANSPARENT_INDEX:
                    data[index] = _TRANSPARENT_INDEX - 1
            quantized.putdata(data)
            quantized.info["transparency"] = _TRANSPARENT_INDEX
        return quantized

    def encode(
        self,
        src: Path,
        dst: Path,
        spec: EncodeSpec,
        info: GifInfo,
        *,
        timeout_s: float | None = None,
    ) -> None:
        if not self.available():
            raise BackendError("Pillow is not installed")
        src = Path(src)
        dst = Path(dst)

        frames, durations = self._load(src)
        frames, durations = self._truncate(frames, durations, spec.max_duration_s)
        frames, durations = self._resample(frames, durations, spec.fps, spec.max_frames)

        preserve_alpha = spec.background is None and any(
            frame.getchannel("A").getextrema()[0] < 128 for frame in frames
        )
        colors = quality_to_colors(spec.quality)

        output = []
        for frame in frames:
            scaled = self._fit(frame, spec)
            if spec.background is not None:
                scaled = self._flatten(scaled, spec.background)
            output.append(self._quantize(scaled, colors, preserve_alpha))

        save_kwargs = dict(
            save_all=True,
            append_images=output[1:],
            duration=durations,
            loop=spec.loop,
            optimize=True,
            disposal=2,
        )
        if preserve_alpha:
            save_kwargs["transparency"] = _TRANSPARENT_INDEX
        try:
            output[0].save(dst, **save_kwargs)
        except Exception as exc:
            raise BackendError(f"Pillow could not write {dst}: {exc}") from exc

    def close(self) -> None:
        return None
