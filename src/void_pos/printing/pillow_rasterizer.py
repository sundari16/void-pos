from __future__ import annotations

import math

from .raster import Raster


class PillowRasterizer:
    """Rasterize text with Pillow (optional dependency: ``pip install '.[print]'``).

    For full non-Latin coverage, pass the path to a font that has the glyphs (e.g. a Noto
    font); complex scripts such as Tamil also need libraqm installed for correct shaping.
    """

    def __init__(
        self, font_path: str | None = None, font_size: int = 32, padding: int = 2
    ) -> None:
        self.font_path = font_path
        self.font_size = font_size
        self.padding = padding

    def render(self, text: str) -> Raster:
        from PIL import Image, ImageDraw, ImageFont, ImageOps

        font = (
            ImageFont.truetype(self.font_path, self.font_size)
            if self.font_path
            else ImageFont.load_default()
        )
        measure = ImageDraw.Draw(Image.new("L", (1, 1), 255))
        left, top, right, bottom = measure.textbbox((0, 0), text, font=font)
        left, top = math.floor(left), math.floor(top)
        width = max(1, math.ceil(right) - left + 2 * self.padding)
        height = max(1, math.ceil(bottom) - top + 2 * self.padding)

        img = Image.new("L", (width, height), 255)
        ImageDraw.Draw(img).text((self.padding - left, self.padding - top), text, fill=0, font=font)

        # Mode "1" packs rows MSB-first, each padded to a byte, with a set bit for white.
        # Inverting first makes a set bit a dark (printed) dot, the ESC/POS GS v 0 layout;
        # the fixed threshold at 128 keeps the same dark/light cut as a plain `< 128` test.
        bits = ImageOps.invert(img).convert("1", dither=Image.Dither.NONE)
        return Raster(width=width, height=height, data=bits.tobytes())
