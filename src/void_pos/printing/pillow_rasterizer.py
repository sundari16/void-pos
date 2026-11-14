from __future__ import annotations

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
        from PIL import Image, ImageDraw, ImageFont

        font = (
            ImageFont.truetype(self.font_path, self.font_size)
            if self.font_path
            else ImageFont.load_default()
        )
        measure = ImageDraw.Draw(Image.new("L", (1, 1), 255))
        box = measure.textbbox((0, 0), text, font=font)
        width = max(1, box[2] - box[0] + 2 * self.padding)
        height = max(1, box[3] - box[1] + 2 * self.padding)

        img = Image.new("L", (width, height), 255)
        ImageDraw.Draw(img).text(
            (self.padding - box[0], self.padding - box[1]), text, fill=0, font=font
        )

        bytes_per_row = (width + 7) // 8
        out = bytearray(bytes_per_row * height)
        pixels = img.load()
        for y in range(height):
            row = y * bytes_per_row
            for x in range(width):
                if pixels[x, y] < 128:  # dark pixel -> printed dot
                    out[row + (x >> 3)] |= 0x80 >> (x & 7)
        return Raster(width=width, height=height, data=bytes(out))
