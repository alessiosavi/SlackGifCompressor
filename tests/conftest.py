from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def make_gif(tmp_path: Path):
    def _make(
        name="test.gif",
        *,
        size=(64, 48),
        frames=3,
        durations=None,
        loop=0,
        transparency=None,
        colors=(255, 0, 0),
    ):
        from PIL import Image

        if durations is None:
            durations = [100] * frames

        images = []
        for i in range(frames):
            # Index 0 is the background (transparent when requested); index 1 is the
            # moving opaque square. P-mode keeps the transparency flag deterministic.
            img = Image.new("P", size, 0)
            palette = [0, 0, 0, colors[0], colors[1], colors[2]] + [0] * (256 * 3 - 6)
            img.putpalette(palette)
            for x in range(0, size[0], 8):
                for y in range(0, size[1], 8):
                    if (x // 8 + y // 8 + i) % 3 == 0:
                        for dx in range(2):
                            for dy in range(2):
                                px, py = x + dx, y + dy
                                if px < size[0] and py < size[1]:
                                    img.putpixel((px, py), 1)
            if transparency is not None:
                img.info["transparency"] = 0
            images.append(img)

        path = tmp_path / name
        save_kwargs = dict(
            save_all=True,
            append_images=images[1:],
            duration=durations,
            optimize=False,
            disposal=2,
        )
        if loop is not None:
            save_kwargs["loop"] = loop
        if transparency is not None:
            save_kwargs["transparency"] = 0
        images[0].save(path, **save_kwargs)
        return path

    return _make
