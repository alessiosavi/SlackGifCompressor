from __future__ import annotations

from pathlib import Path
from typing import Protocol

from slackgif.config import EncodeSpec, GifInfo, Preset
from slackgif.errors import BackendError


class Backend(Protocol):
    name: str
    quality_levels: tuple[int, ...] | None

    def available(self) -> bool: ...

    def can_handle(self, info: GifInfo, preset: Preset) -> bool: ...

    def encode(
        self,
        src: Path,
        dst: Path,
        spec: EncodeSpec,
        info: GifInfo,
        *,
        timeout_s: float | None = None,
    ) -> None: ...

    def close(self) -> None: ...


__all__ = ["Backend", "BackendError"]
