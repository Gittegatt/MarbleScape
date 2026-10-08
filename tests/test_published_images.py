import io
from pathlib import Path
import tempfile
import unittest

from PIL import Image
from marblescape_image_metadata import embed_png_metadata
from marblescape_published_images import PublishedImageIndex


class PublishedImageTests(unittest.TestCase):
    def test_only_existing_latest_or_history_images_count_and_newest_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            latest, history, cache = (root / name for name in ("latest", "history", "cache"))
            for folder in (latest, history, cache):
                folder.mkdir()
            def image(folder, timestamp):
                data = io.BytesIO()
                Image.new("RGB", (2, 2)).save(data, "PNG")
                path = folder / "image.png"
                path.write_bytes(embed_png_metadata(data.getvalue(), {
                    "software": "MarbleScape", "width": 2, "height": 2,
                    "profile_id": "1" * 32, "generated_at_utc": timestamp,
                }))
                return path
            image(cache, "2026-09-27T03:00:00Z")
            index = PublishedImageIndex()
            self.assertEqual(index.scan([latest, history]), {})
            older = image(history, "2026-09-26T03:00:00Z")
            newest = image(latest, "2026-09-27T02:00:00Z")
            self.assertEqual(index.scan([latest, history])["1" * 32], "2026-09-27T02:00:00+00:00")
            newest.unlink()
            self.assertEqual(index.scan([latest, history])["1" * 32], "2026-09-26T03:00:00+00:00")
            older.unlink()
            (latest / "broken.png").write_bytes(b"not PNG")
            self.assertEqual(index.scan([latest, history]), {})
