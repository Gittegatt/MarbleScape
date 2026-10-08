"""Displays Windows lists without a device path, and the wallpaper after display changes."""

import ctypes
import hashlib
import os
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import marblescape_download as app

HP = "\\\\?\\DISPLAY#HPN3678#7&3adedb68&0&UID260#{e6f07b5f}"
LEN = "\\\\?\\DISPLAY#LEN0A12#7&3adedb68&0&UID264#{e6f07b5f}"
HP_RECT = (0, 0, 4, 2)
LEN_RECT = (-2, 0, 0, 4)
LAYOUT = [(HP, HP_RECT), (LEN, LEN_RECT)]


class ResolveMonitorTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(app, "log")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_display_without_path_gets_the_path_at_its_place(self):
        monitors = app.resolve_wallpaper_monitors([("", HP_RECT), (LEN, LEN_RECT)], LAYOUT)
        self.assertEqual(monitors, [{"id": HP, "slot": "", "rect": HP_RECT},
                                    {"id": LEN, "rect": LEN_RECT}])

    def test_an_unknown_or_duplicate_empty_slot_is_left_out(self):
        # No display at that place: an empty ID could set every display.
        self.assertEqual(app.resolve_wallpaper_monitors([("", (9, 9, 10, 10))], LAYOUT), [])
        # The display is also listed under its own path: the empty slot is stale.
        self.assertEqual(app.resolve_wallpaper_monitors([("", HP_RECT), (HP.lower(), HP_RECT)], LAYOUT),
                         [{"id": HP.lower(), "rect": HP_RECT}])
        # A mirrored display has no single path.
        self.assertEqual(app.resolve_wallpaper_monitors([("", HP_RECT)], [("", HP_RECT)]), [])

    def test_listed_paths_stay_unchanged(self):
        entries = [(HP, HP_RECT), (LEN, LEN_RECT)]
        self.assertEqual(app.resolve_wallpaper_monitors(entries, []),
                         [{"id": HP, "rect": HP_RECT}, {"id": LEN, "rect": LEN_RECT}])


@unittest.skipUnless(os.name == "nt", "Windows wallpaper API")
class WallpaperSlotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.events = []
        for target, value in (("log", Mock()), ("capture_previous_wallpapers", Mock()),
                              ("with_windows_com", Mock(side_effect=lambda action: action())),
                              ("create_desktop_wallpaper_interface", Mock(return_value=123)),
                              ("get_com_method", Mock(side_effect=self.method)),
                              ("release_com_pointer", Mock()), ("CONTENT_DIR", self.root),
                              ("windows_display_layout", Mock(return_value=LAYOUT)),
                              ("WINDOWS_WALLPAPER_PAUSED", frozenset())):
            patcher = patch.object(app, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def method(self, _interface, index, _restype, *_argtypes):
        if index == 10:
            return lambda _desktop, value: self.events.append(("position", value)) or 0
        if index == 3:
            return lambda _desktop, monitor, path: self.events.append(("wallpaper", monitor, path)) or 0
        if index == 4:
            def get_wallpaper(_desktop, monitor, output):
                buffer = ctypes.create_unicode_buffer("C:\\other\\" + monitor[-6:] + ".png")
                self.buffers.append(buffer)
                ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(buffer)
                return 0
            return get_wallpaper
        raise AssertionError(index)

    buffers = []

    def test_the_empty_slot_goes_first_with_the_displays_own_settings(self):
        image_path = self.root / "source.png"
        Image.new("RGB", (4, 2), "red").save(image_path)
        outputs = {HP: {"width": 3, "height": 0, "aspect_ratio": "1:1",
                        "render_scale": "auto", "background_color": "#102030"}}
        # Windows lists the portrait display first and the landscape one without its path.
        monitors = [{"id": LEN, "rect": LEN_RECT}, {"id": HP, "slot": "", "rect": (0, 0, 5, 5)}]
        with patch.object(app, "WINDOWS_WALLPAPER_POSITION", "center"), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {}), \
             patch.object(app, "WINDOWS_WALLPAPER_MONITOR_OUTPUTS", outputs), \
             patch.object(app, "WIDTH", 4), patch.object(app, "HEIGHT", 2), \
             patch.object(app, "ASPECT_RATIO", "2:1"), \
             patch.object(app, "list_windows_wallpaper_monitors", return_value=monitors), \
             patch.object(app, "WALLPAPER_LAYOUT", None):
            app.set_windows_wallpaper(image_path)
            self.assertEqual(app.WALLPAPER_LAYOUT, LAYOUT)
        wallpapers = [event[1:] for event in self.events if event[0] == "wallpaper"]
        # The empty ID first (it can set every display), then the others, then
        # the real path for a later Windows start.
        self.assertEqual([target for target, _path in wallpapers], ["", LEN, HP])
        hp_file = self.root / "device_wallpapers" / (hashlib.sha256(HP.encode()).hexdigest()[:16] + ".png")
        self.assertEqual(wallpapers[0][1], str(hp_file.resolve()))
        self.assertEqual(wallpapers[2][1], str(hp_file.resolve()))
        with Image.open(hp_file) as picture:
            # The display's own output settings (its background) apply.
            self.assertEqual(picture.getpixel((0, 0)), (16, 32, 48))

    def test_restoring_the_display_without_path_keeps_the_others(self):
        backup_dir = self.root / "previous_wallpapers"
        backup_dir.mkdir()
        backup = backup_dir / (hashlib.sha256(HP.encode()).hexdigest() + ".png")
        Image.new("RGB", (2, 2), "red").save(backup)
        free = Mock()
        with patch.object(app, "WINDOWS_WALLPAPER_MONITOR_POSITIONS", {}), \
             patch.object(app, "update_active_configuration", return_value=True), \
             patch.object(app, "list_windows_wallpaper_monitors", return_value=[
                 {"id": HP, "slot": "", "rect": HP_RECT}, {"id": LEN, "rect": LEN_RECT}]), \
             patch.object(app.ctypes, "windll", SimpleNamespace(ole32=SimpleNamespace(CoTaskMemFree=free))):
            app.restore_previous_wallpaper(HP)
        wallpapers = [event[1:] for event in self.events if event[0] == "wallpaper"]
        self.assertEqual(wallpapers, [("", str(backup.resolve())), (HP, str(backup.resolve())),
                                      (LEN, "C:\\other\\" + LEN[-6:] + ".png")])

    def test_enumeration_names_a_display_listed_without_its_path(self):
        buffers = [ctypes.create_unicode_buffer(value) for value in ("", LEN, HP)]

        def method(_desktop, index, _restype, *_argtypes):
            if index == 6:
                def get_count(_interface, count):
                    ctypes.cast(count, ctypes.POINTER(ctypes.c_uint))[0] = 3
                    return 0
                return get_count
            if index == 5:
                def get_id(_interface, monitor_index, output):
                    ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = \
                        ctypes.addressof(buffers[monitor_index])
                    return 0
                return get_id
            if index == 7:
                def get_rect(_interface, monitor_id, output):
                    if monitor_id == HP:
                        # The real path of the display listed without it: detached.
                        return ctypes.c_long(0x80070057).value
                    rectangle = ctypes.cast(output, ctypes.POINTER(app.WindowsRect))
                    rectangle[0] = app.WindowsRect(*(HP_RECT if monitor_id == "" else LEN_RECT))
                    return 0
                return get_rect
            raise AssertionError(index)

        with patch.object(app, "get_com_method", side_effect=method), \
             patch.object(app.ctypes, "windll", SimpleNamespace(ole32=SimpleNamespace(CoTaskMemFree=Mock()))):
            self.assertEqual(app.list_windows_wallpaper_monitors(), [
                {"id": HP, "slot": "", "rect": HP_RECT}, {"id": LEN, "rect": LEN_RECT}])


class DisplayWatchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.picture = Path(self.temporary.name) / "shown.png"
        self.picture.write_bytes(b"png")
        for target, value in (("log", Mock()), ("SET_WINDOWS_WALLPAPER", True),
                              ("get_current_image_path", Mock(return_value=self.picture))):
            patcher = patch.object(app, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def watch(self, applied, layouts, fail=False):
        """Run the watch over the given layouts, one per check; returns the wallpaper calls."""
        stop = Mock()
        stop.wait.side_effect = [False] * len(layouts) + [True]
        calls = []

        def set_wallpaper(path):
            calls.append(path)
            if fail:
                raise OSError("display busy")
            app.WALLPAPER_LAYOUT = layouts_iter[-1]

        layouts_iter = []

        def layout():
            layouts_iter.append(layouts[len(layouts_iter)])
            return layouts_iter[-1]

        with patch.object(app, "WALLPAPER_LAYOUT", applied), \
             patch.object(app, "windows_display_layout", side_effect=layout), \
             patch.object(app, "set_windows_wallpaper", side_effect=set_wallpaper):
            app.watch_display_layout(stop, interval=0)
            self.final_layout = app.WALLPAPER_LAYOUT
        return calls

    def test_a_display_that_appears_after_the_wallpaper_gets_it_once_settled(self):
        only_hp = [(HP, HP_RECT)]
        # Set while only one display was ready; the other comes up later.
        self.assertEqual(self.watch(only_hp, [only_hp, LAYOUT, LAYOUT, LAYOUT, LAYOUT]), [self.picture])

    def test_a_rotated_or_resized_display_gets_the_picture_again(self):
        landscape = [(HP, HP_RECT), (LEN, (-4, 0, 0, 2))]
        self.assertEqual(self.watch(landscape, [LAYOUT, LAYOUT]), [self.picture])

    def test_unchanged_flapping_or_unset_layouts_do_nothing(self):
        self.assertEqual(self.watch(LAYOUT, [LAYOUT] * 4), [])
        # A layout that changes at every check is not settled yet.
        self.assertEqual(self.watch(LAYOUT, [[(HP, HP_RECT)], LAYOUT, [(HP, HP_RECT)]]), [])
        # Before MarbleScape set any wallpaper (or with every display set to none).
        self.assertEqual(self.watch(None, [LAYOUT] * 3), [])
        with patch.object(app, "SET_WINDOWS_WALLPAPER", False):
            self.assertEqual(self.watch([(HP, HP_RECT)], [LAYOUT] * 3), [])

    def test_a_failed_retry_waits_for_the_next_change(self):
        self.assertEqual(self.watch([(HP, HP_RECT)], [LAYOUT] * 5, fail=True), [self.picture])
        self.assertEqual(self.final_layout, LAYOUT)


if __name__ == "__main__":
    unittest.main()
