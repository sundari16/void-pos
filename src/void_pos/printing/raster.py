from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Raster:
    """A 1-bit raster: rows packed MSB-first, each padded to a byte boundary. A set bit
    is a printed (black) dot, matching the ESC/POS ``GS v 0`` raster format."""

    width: int
    height: int
    data: bytes

    def __post_init__(self) -> None:
        expected = self.bytes_per_row * self.height
        if len(self.data) != expected:
            raise ValueError(f"raster data is {len(self.data)} bytes, expected {expected}")

    @property
    def bytes_per_row(self) -> int:
        return (self.width + 7) // 8


class Rasterizer(Protocol):
    """Renders a string to a 1-bit raster so non-Latin scripts print on basic printers."""

    def render(self, text: str) -> Raster: ...
