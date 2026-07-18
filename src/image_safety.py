"""Small PNG header checks used before handing image bytes to Streamlit."""

from __future__ import annotations

import struct


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_dimensions(png_bytes: bytes) -> tuple[int, int] | None:
    if (
        not isinstance(png_bytes, bytes)
        or len(png_bytes) < 24
        or not png_bytes.startswith(PNG_SIGNATURE)
        or png_bytes[12:16] != b"IHDR"
    ):
        return None
    width, height = struct.unpack(">II", png_bytes[16:24])
    if width < 1 or height < 1:
        return None
    return width, height


def is_png_over_pixel_limit(png_bytes: bytes, max_pixels: int) -> bool:
    dimensions = png_dimensions(png_bytes)
    return dimensions is not None and dimensions[0] * dimensions[1] > int(max_pixels)
