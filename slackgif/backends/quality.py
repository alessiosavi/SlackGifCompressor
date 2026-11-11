from __future__ import annotations

QUALITY_LEVELS = (0, 10, 30, 50, 75)


def quality_to_colors(quality: int) -> int:
    if quality < 10:
        return 16
    if quality < 30:
        return 32
    if quality < 50:
        return 64
    if quality < 75:
        return 128
    return 256
