"""Draw the MarbleScape icon and package its PNG sizes into a Windows ICO file.

The icon is MarbleScape's own drawing: a sphere outline with an equator, made of
a circle and an ellipse. ``python build_icon.py --draw`` draws every PNG size anew;
without it only the ICO is packaged from the PNGs (as the release build does).
"""

from pathlib import Path
import struct
import sys

from PIL import Image, ImageDraw

ICON_DIRECTORY = Path(__file__).resolve().parent / "assets" / "icons"
ICON_COLOR = (0, 195, 255, 255)  # #00C3FF
PNG_SIZES = (16, 32, 48, 64, 128, 256, 512)
ICO_SIZES = (16, 32, 48, 64, 128, 256)
SUPERSAMPLING = 8


def draw_icon(size):
    """The icon at ``size`` x ``size`` pixels: drawn large, then reduced smoothly."""
    scale = size * SUPERSAMPLING
    image = Image.new("RGBA", (scale, scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Thin lines stay visible at small sizes: at least 1.5 output pixels.
    stroke = round(max(1.5, size * 0.05) * SUPERSAMPLING)
    margin = round(size * 0.006 * SUPERSAMPLING)
    # The sphere's outline.
    draw.ellipse((margin, margin, scale - 1 - margin, scale - 1 - margin), outline=ICON_COLOR, width=stroke)
    # The equator: as wide as the sphere, two fifths as high.
    half_height = round(scale * 0.2)
    middle = scale // 2
    draw.ellipse((margin, middle - half_height, scale - 1 - margin, middle + half_height),
                 outline=ICON_COLOR, width=stroke)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def draw_pngs():
    for size in PNG_SIZES:
        path = ICON_DIRECTORY / f"marblescape_{size}.png"
        draw_icon(size).save(path, "PNG", optimize=True)
        print(f"Drew {path.name}.")


def build_icon():
    frames = []
    for size in ICO_SIZES:
        path = ICON_DIRECTORY / f"marblescape_{size}.png"
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != (size, size):
                raise ValueError(f"Expected a {size} x {size} PNG: {path}")
            image.verify()
        frames.append(path.read_bytes())
    offset = 6 + 16 * len(frames)
    entries = []
    for size, data in zip(ICO_SIZES, frames):
        entries.append(struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        ))
        offset += len(data)
    target = ICON_DIRECTORY / "marblescape.ico"
    target.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames))
        + b"".join(entries) + b"".join(frames)
    )
    print(f"Generated {target.name} with {len(frames)} PNG sizes.")


if __name__ == "__main__":
    if "--draw" in sys.argv[1:]:
        draw_pngs()
    build_icon()
