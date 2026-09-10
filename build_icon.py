"""Package the supplied MarbleScape PNG sizes into a Windows ICO file."""

from pathlib import Path
import struct

from PIL import Image


def build_icon():
    directory = Path(__file__).resolve().parent / "assets" / "icons"
    sizes = (16, 32, 48, 64, 128, 256)
    frames = []
    for size in sizes:
        path = directory / f"marblescape_{size}.png"
        with Image.open(path) as image:
            if image.format != "PNG" or image.size != (size, size):
                raise ValueError(f"Expected a {size} x {size} PNG: {path}")
            image.verify()
        frames.append(path.read_bytes())
    offset = 6 + 16 * len(frames)
    entries = []
    for size, data in zip(sizes, frames):
        entries.append(struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset
        ))
        offset += len(data)
    target = directory / "marblescape.ico"
    target.write_bytes(
        struct.pack("<HHH", 0, 1, len(frames))
        + b"".join(entries) + b"".join(frames)
    )
    print(f"Generated {target.name} with {len(frames)} original PNG sizes.")


if __name__ == "__main__":
    build_icon()
