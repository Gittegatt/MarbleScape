import time
import unittest
from unittest import mock

import marblescape_theme as theme

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # pragma: no cover - Linux --once environments
    tk = None


class AppearanceSettingTests(unittest.TestCase):
    def test_only_system_light_and_dark_are_accepted(self):
        for value in ("system", "light", "dark", " Dark "):
            self.assertIn(theme.normalize_appearance(value), theme.APPEARANCES)
        for value in ("", "auto", None, 1, "black"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                theme.normalize_appearance(value)
        self.assertEqual(list(theme.APPEARANCE_LABELS), list(theme.APPEARANCES))

    def test_system_follows_the_windows_app_mode(self):
        with mock.patch.object(theme, "system_prefers_dark", return_value=True):
            self.assertEqual(theme.resolve_appearance("system"), "dark")
            self.assertEqual(theme.resolve_appearance("light"), "light")
        with mock.patch.object(theme, "system_prefers_dark", return_value=False):
            self.assertEqual(theme.resolve_appearance("system"), "light")
            self.assertEqual(theme.resolve_appearance("dark"), "dark")

    @unittest.skipUnless(theme.os.name == "nt", "Windows registry")
    def test_windows_registry_value_selects_the_mode(self):
        key = mock.MagicMock()
        key.__enter__.return_value = key
        with mock.patch("winreg.OpenKey", return_value=key), \
                mock.patch("winreg.QueryValueEx", return_value=(0, 4)):
            self.assertTrue(theme.system_prefers_dark())
        with mock.patch("winreg.OpenKey", return_value=key), \
                mock.patch("winreg.QueryValueEx", return_value=(1, 4)):
            self.assertFalse(theme.system_prefers_dark())
        with mock.patch("winreg.OpenKey", side_effect=OSError("missing")):
            self.assertFalse(theme.system_prefers_dark())


@unittest.skipIf(tk is None, "Tkinter is not installed")
class NativeMenuTests(unittest.TestCase):
    def test_native_menus_force_the_resolved_mode(self):
        uxtheme = mock.MagicMock()
        with mock.patch.object(theme.os, "name", "nt"), \
                mock.patch("ctypes.WinDLL", create=True, return_value=uxtheme), \
                mock.patch("sys.getwindowsversion", create=True,
                           return_value=mock.Mock(build=26100)):
            self.assertTrue(theme.set_native_menus("dark"))
            uxtheme.__getitem__.return_value.assert_any_call(2)  # ForceDark
            self.assertTrue(theme.set_native_menus("light"))
            uxtheme.__getitem__.return_value.assert_any_call(3)  # ForceLight
            self.assertEqual([call.args[0] for call in uxtheme.__getitem__.call_args_list],
                             [135, 136, 135, 136])
            self.assertFalse(theme.set_native_menus("system"))
        with mock.patch.object(theme.os, "name", "nt"), \
                mock.patch("sys.getwindowsversion", create=True,
                           return_value=mock.Mock(build=17763)):
            self.assertFalse(theme.set_native_menus("dark"))  # Before Windows 10 1903.
        with mock.patch.object(theme.os, "name", "posix"):
            self.assertFalse(theme.set_native_menus("dark"))

    def test_the_tray_menu_opens_in_the_saved_appearance(self):
        import marblescape_download as app

        icon = app.create_windows_tray_icon("test", None, "test")
        with mock.patch.object(app, "APPEARANCE", "dark"), \
                mock.patch.object(app, "set_native_menus") as native, \
                mock.patch("pystray.Icon._on_notify", create=True) as base:
            icon._on_notify(0, 0x0205)
        native.assert_called_once_with("dark")
        base.assert_called_once()


class AppearanceWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def wait_for(self, condition, seconds=3):
        deadline = time.monotonic() + seconds
        while not condition():
            if time.monotonic() > deadline:
                self.fail("Condition not reached")
            self.root.update()
            time.sleep(0.01)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_light_and_dark_switch_the_theme_and_explicit_colors(self):
        canvas = tk.Canvas(self.root, background="#123456")
        link = tk.Label(self.root, text="https://example.org")
        theme.keep_background(canvas)
        theme.keep_palette_color(link, "foreground", "link")
        self.assertEqual(theme.apply_appearance(self.root, "dark"), "dark")
        self.assertEqual(ttk.Style(self.root).theme_use(), "sun-valley-dark")
        self.assertEqual(theme.current_mode(link), "dark")
        # Colors MarbleScape sets itself follow at once; Sun Valley's background is #1c1c1c.
        self.assertEqual(str(link.cget("foreground")), theme.PALETTES["dark"]["link"])
        self.assertEqual(str(canvas.cget("background")), "#1c1c1c")
        self.assertEqual(str(ttk.Style(self.root).lookup(".", "background")), "#1c1c1c")
        self.assertEqual(theme.apply_appearance(self.root, "light"), "light")
        self.assertEqual(ttk.Style(self.root).theme_use(), "sun-valley-light")
        self.assertEqual(str(link.cget("foreground")), theme.PALETTES["light"]["link"])
        self.assertEqual(str(canvas.cget("background")), "#fafafa")
        self.root.update()
        self.assertEqual(str(link.cget("foreground")), theme.PALETTES["light"]["link"])

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_section_titles_are_text_sized_and_semibold_in_both_modes(self):
        from tkinter import font as tkfont
        for mode in ("dark", "light", "dark"):
            theme.apply_appearance(self.root, mode)
            style = ttk.Style(self.root)
            for name in ("TLabelframe.Label", theme.SECTION_LABEL_STYLE):
                self.assertEqual(str(style.lookup(name, "font")), theme.SECTION_FONT, (mode, name))
        section = tkfont.nametofont(theme.SECTION_FONT, root=self.root)
        body = tkfont.nametofont("SunValleyBodyFont", root=self.root)
        self.assertEqual(section.actual("size"), body.actual("size"))
        self.assertIn("Semibold", section.actual("family"))

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_info_texts_use_the_labels_font_not_the_smaller_tk_default(self):
        from tkinter import font as tkfont
        theme.apply_appearance(self.root, "dark")
        self.root.update()
        shown = theme.markup_text(self.root, "Plain **bold** and *italic*.")
        body = tkfont.nametofont("SunValleyBodyFont", root=self.root)
        self.assertEqual(str(shown.cget("font")), "SunValleyBodyFont")
        self.assertGreater(body.metrics("linespace"),
                           tkfont.nametofont("TkDefaultFont", root=self.root).metrics("linespace"))
        bold = tkfont.Font(root=self.root, font=shown.tag_cget("bold", "font"))
        self.assertEqual((bold.actual("size"), bold.actual("weight")), (body.actual("size"), "bold"))
        theme.apply_appearance(self.root, "light")
        self.assertEqual(str(shown.cget("font")), "SunValleyBodyFont")

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_explicit_colors_survive_sun_valley_palette_reset(self):
        """Sun Valley's <<ThemeChanged>> runs tk_setPalette once the event loop runs."""
        theme.apply_appearance(self.root, "dark")
        plain = tk.Label(self.root, text="https://example.org")
        themed = ttk.Label(self.root, text="Saved")
        theme.keep_palette_color(plain, "foreground", "link")
        theme.keep_palette_color(themed, "foreground", "success")
        for mode in ("dark", "light", "dark"):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            self.assertEqual(str(plain.cget("foreground")), theme.PALETTES[mode]["link"], mode)
            self.assertEqual(str(themed.cget("foreground")), theme.PALETTES[mode]["success"], mode)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_disabled_fields_grey_like_disabled_check_buttons(self):
        """tk_setPalette's explicit text color must not hide the disabled grey."""
        theme.apply_appearance(self.root, "dark")
        before = ttk.Combobox(self.root, values=("7 days",), state="disabled")
        self.root.update()
        for mode, grey in (("dark", "#595959"), ("light", "#a0a0a0"), ("dark", "#595959")):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            later = ttk.Entry(self.root)
            label = ttk.Label(self.root, text="105%")
            self.root.update()
            style = ttk.Style(self.root)
            for widget in (before, later, label):
                self.assertEqual(str(widget.cget("foreground")), "", (mode, widget))
            for name in ("TCombobox", "TEntry", "TSpinbox", "TCheckbutton"):
                self.assertEqual(str(style.lookup(name, "foreground", ["disabled"])), grey, (mode, name))

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_disabled_slider_and_its_value_look_disabled(self):
        theme.apply_appearance(self.root, "dark")
        scale = tk.Scale(self.root, orient="horizontal")
        value = ttk.Label(self.root, text="105%")
        theme.style_scale(scale, value)
        self.root.update()
        enabled_trough = str(scale.cget("troughcolor"))
        theme.set_scale_enabled(scale, False)
        self.assertEqual(str(scale.cget("state")), "disabled")
        self.assertEqual(str(scale.cget("troughcolor")), theme.PALETTES["dark"]["scale_trough_disabled"])
        self.assertEqual(str(scale.cget("background")), theme.PALETTES["dark"]["scale_slider_disabled"])
        self.assertEqual(str(scale.cget("sliderrelief")), "flat")
        self.assertIn("disabled", value.state())
        # A theme switch keeps the disabled look in the new mode's colors.
        theme.apply_appearance(self.root, "light")
        self.root.update()
        self.assertEqual(str(scale.cget("troughcolor")), theme.PALETTES["light"]["scale_trough_disabled"])
        theme.apply_appearance(self.root, "dark")
        self.root.update()
        theme.set_scale_enabled(scale, True)
        self.assertEqual(str(scale.cget("troughcolor")), enabled_trough)
        self.assertEqual(str(scale.cget("sliderrelief")), "raised")
        self.assertNotIn("disabled", value.state())

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_color_swatches_are_framed_in_the_text_color(self):
        swatch = tk.Label(self.root, width=2, background="#000000")
        theme.style_swatch(swatch)
        for mode, color in (("dark", "#fafafa"), ("light", "#1c1c1c"), ("dark", "#fafafa")):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            self.assertEqual(str(swatch.cget("highlightbackground")), color, mode)
            self.assertEqual(int(swatch.cget("highlightthickness")), 1)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_menu_check_marks_use_the_text_color_of_the_mode(self):
        existing = tk.Menu(self.root, tearoff=False)
        for mode, color in (("dark", "#fafafa"), ("light", "#1c1c1c"), ("dark", "#fafafa")):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            self.assertEqual(str(existing.cget("selectcolor")), color, mode)
            # Menus created later, such as the profile table's Columns menu.
            self.assertEqual(str(tk.Menu(self.root, tearoff=False).cget("selectcolor")), color, mode)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_greyed_out_menu_entries_avoid_the_embossed_disabled_look(self):
        calls = []
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="Rename", command=lambda: calls.append("rename"))
        for mode, grey in (("dark", "#595959"), ("light", "#a0a0a0")):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            theme.set_menu_entry_enabled(menu, "Rename", False)
            # Never Tk's disabled state, which Windows draws with a white shadow.
            self.assertEqual(menu.entrycget("Rename", "state"), "normal")
            self.assertFalse(theme.menu_entry_enabled(menu, "Rename"))
            self.assertEqual((str(menu.entrycget("Rename", "foreground")),
                              str(menu.entrycget("Rename", "activeforeground"))), (grey, grey))
            # Not highlighted under the pointer, and a click does nothing.
            self.assertEqual(str(menu.entrycget("Rename", "activebackground")), str(menu.cget("background")))
            menu.invoke("Rename")
            self.assertEqual(calls, [])
        # A theme change recolors a greyed-out entry.
        theme.apply_appearance(self.root, "dark")
        self.root.update()
        self.assertEqual(str(menu.entrycget("Rename", "foreground")), "#595959")
        theme.set_menu_entry_enabled(menu, "Rename", True)
        self.assertTrue(theme.menu_entry_enabled(menu, "Rename"))
        self.assertEqual(str(menu.entrycget("Rename", "foreground")), "")
        menu.invoke("Rename")
        self.assertEqual(calls, ["rename"])

    def test_typing_finds_dropdown_entries_by_start_then_by_any_part(self):
        values = ("Iceland", "India", "Italy", "South Iceland")
        self.assertEqual(theme.type_search_index(values, "IC"), 0)
        self.assertEqual(theme.type_search_index(values, "ital"), 2)
        # No entry starts with it: any part of a name matches.
        self.assertEqual(theme.type_search_index(values, "south ice"), 3)
        self.assertEqual(theme.type_search_index(values, "land", start=1), 3)
        self.assertIsNone(theme.type_search_index(values, "x"))
        self.assertIsNone(theme.type_search_index((), "i"))
        # A first letter moves on from the current entry, repeating it steps on,
        # more letters refine the current match.
        self.assertEqual(theme._search_target(values, "i", 0), 1)
        self.assertEqual(theme._search_target(values, "ii", 1), 2)
        self.assertEqual(theme._search_target(values, "ii", 2), 0)
        self.assertEqual(theme._search_target(values, "ic", 0), 0)
        self.assertEqual(theme._search_target(values, "i", -1), 0)

    def test_typing_in_a_closed_dropdown_chooses_the_matching_entry(self):
        from types import SimpleNamespace
        theme.apply_appearance(self.root, "light")
        self.assertIn("_combobox_type_search", self.root.bind_class("TCombobox", "<KeyPress>"))
        self.assertIn("search", self.root.bind_class("ComboboxListbox", "<KeyPress>"))
        combo = ttk.Combobox(self.root, values=("Bolivia", "Iceland", "India", "Italy"), state="readonly")
        combo.current(0)
        chosen = []
        combo.bind("<<ComboboxSelected>>", lambda _event: chosen.append(combo.get()), add="+")
        clock = [100.0]

        def press(char, state=0):
            return theme._combobox_type_search(SimpleNamespace(widget=combo, char=char, state=state))

        with mock.patch.object(theme.time, "monotonic", side_effect=lambda: clock[0]):
            self.assertEqual(press("i"), "break")
            clock[0] += 0.2
            press("n")
            self.assertEqual(combo.get(), "India")
            # After a pause the search starts again; Ctrl and Alt shortcuts are not typing.
            clock[0] += 2
            press("i", state=0x4)
            self.assertEqual(combo.get(), "India")
            press("i")
            self.assertEqual(combo.get(), "Italy")
            clock[0] += 2
            self.assertIsNone(press(""))
            combo.configure(state="disabled")
            press("b")
            self.assertEqual(combo.get(), "Italy")
        self.assertEqual(chosen, ["Iceland", "India", "Italy"])
        # An editable dropdown keeps normal typing.
        editable = ttk.Combobox(self.root, values=("Iceland",))
        self.assertIsNone(theme._combobox_type_search(SimpleNamespace(widget=editable, char="i", state=0)))

    def test_typing_in_an_open_dropdown_list_marks_the_matching_entry(self):
        from types import SimpleNamespace
        theme.apply_appearance(self.root, "light")
        combo = ttk.Combobox(self.root, values=("Bolivia", "Iceland", "Italy"), state="readonly")
        popdown = self.root.tk.call("ttk::combobox::PopdownWindow", combo)
        listbox = f"{popdown}.f.l"
        # As ttk does when it opens the list.
        self.root.tk.call(listbox, "insert", "end", *combo.cget("values"))
        self.root.tk.call(listbox, "selection", "set", 0)
        search = theme._listbox_type_search(self.root)
        with mock.patch.object(theme.time, "monotonic", return_value=50.0):
            self.assertEqual(search(SimpleNamespace(widget=listbox, char="i", state=0)), "break")
            search(SimpleNamespace(widget=listbox, char="t", state=0))
        self.assertEqual(self.root.tk.splitlist(self.root.tk.call(listbox, "curselection")), (2,))
        self.assertEqual(int(self.root.tk.call(listbox, "index", "active")), 2)
        # Enter (ttk's own binding) then chooses the marked entry; the field is unchanged until then.
        self.assertEqual(combo.get(), "")

    def test_a_chosen_dropdown_value_is_not_left_highlighted(self):
        theme.apply_appearance(self.root, "dark")
        combo = ttk.Combobox(self.root, values=("first", "second"), state="readonly")
        combo.pack()
        self.root.update()
        # As ttk does when a value is chosen from the list.
        combo.current(1)
        combo.selection_range(0, "end")
        combo.event_generate("<<ComboboxSelected>>")
        self.root.update()
        self.assertFalse(combo.selection_present())
        self.assertEqual(combo.get(), "second")

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_disabled_menu_entries_are_flat_grey_without_a_white_shadow(self):
        # Without a disabled color Windows draws disabled entries embossed.
        existing = tk.Menu(self.root, tearoff=False)
        for mode, color in (("dark", "#595959"), ("light", "#a0a0a0"), ("dark", "#595959")):
            theme.apply_appearance(self.root, mode)
            self.root.update()
            self.assertEqual(str(existing.cget("disabledforeground")), color, mode)
            self.assertEqual(str(tk.Menu(self.root, tearoff=False).cget("disabledforeground")), color, mode)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_closed_widgets_leave_the_listener_list(self):
        canvas = tk.Canvas(self.root)
        link = tk.Label(self.root, text="https://example.org")
        theme.keep_background(canvas)
        theme.keep_palette_color(link, "foreground", "link")
        theme.apply_appearance(self.root, "light")
        # A closed widget is dropped instead of breaking later switches.
        canvas.destroy()
        self.assertEqual(theme.apply_appearance(self.root, "dark"), "dark")
        self.assertEqual(len(self.root._marblescape_theme_listeners), 1)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_system_choice_follows_a_windows_mode_change_while_open(self):
        with mock.patch.object(theme, "system_prefers_dark", return_value=False):
            theme.apply_appearance(self.root, "system")
        self.assertEqual(theme.current_mode(self.root), "light")
        with mock.patch.object(theme, "SYSTEM_POLL_MILLISECONDS", 10), \
                mock.patch.object(theme, "system_prefers_dark", return_value=True):
            theme.follow_system(self.root, lambda: "system")
            self.wait_for(lambda: theme.current_mode(self.root) == "dark")
        self.assertEqual(ttk.Style(self.root).theme_use(), "sun-valley-dark")

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_settings_tab_buttons_show_the_selected_tab_in_both_modes(self):
        button = ttk.Radiobutton(self.root, text="General", style=theme.TAB_BUTTON_STYLE, value=0)
        for mode in ("light", "dark"):
            with self.subTest(mode=mode):
                theme.apply_appearance(self.root, mode)
                layout = str(ttk.Style(self.root).layout(theme.TAB_BUTTON_STYLE))
                # Sun Valley's notebook-tab images, which have a selected look.
                self.assertIn("MarbleScapeTab.button", layout)
                self.assertIn("MarbleScapeTab.button",
                              self.root.tk.splitlist(self.root.tk.call("ttk::style", "element", "names")))
                self.assertLess(button.winfo_reqheight(), 40)

    @unittest.skipIf(theme.sv_ttk is None, "sv-ttk is not installed")
    def test_widened_sprites_keep_every_widget_size_and_restore_ttk_style(self):
        def sizes(fast, mode):
            root = tk.Tk()
            root.withdraw()
            try:
                theme._load_sun_valley(root, fast_sprites=fast)
                self.assertEqual(root.tk.eval("info commands ::ttk::style"), "::ttk::style")
                self.assertEqual(root.tk.eval("interp alias {} ::ttk::style"), "")
                ttk.Style(root).theme_use(f"sun-valley-{mode}")
                combo = ttk.Combobox(root, values=["System"], state="readonly")
                tree = ttk.Treeview(root, columns=("a",), height=3)
                tree.heading("#0", text="Profile name")
                notebook = ttk.Notebook(root)
                notebook.add(ttk.Frame(notebook), text="General")
                widgets = [
                    ttk.Button(root, text="Choose color..."), ttk.Button(root, text="Go", style="Accent.TButton"),
                    ttk.Entry(root), combo, ttk.Combobox(root), ttk.Spinbox(root, from_=0, to=9),
                    ttk.Checkbutton(root, text="Check"), ttk.Radiobutton(root, text="Radio"),
                    ttk.Radiobutton(root, text="Tab", style=theme.TAB_BUTTON_STYLE),
                    ttk.Radiobutton(root, text="Tool", style="Toolbutton"), tree, notebook,
                    ttk.LabelFrame(root, text="Group"), ttk.Scrollbar(root, orient="vertical"),
                    ttk.Scrollbar(root, orient="horizontal"), ttk.Progressbar(root),
                    ttk.Scale(root), ttk.Separator(root),
                ]
                return [(type(widget).__name__, widget.winfo_reqwidth(), widget.winfo_reqheight())
                        for widget in widgets], theme.fast_sprite_count(root)
            finally:
                root.destroy()

        for mode in ("light", "dark"):
            with self.subTest(mode=mode):
                plain, plain_count = sizes(False, mode)
                fast, fast_count = sizes(True, mode)
                self.assertEqual(fast, plain)
                self.assertEqual(plain_count, 0)
                # Buttons, fields, tabs, headings, frames and troughs, in both themes.
                self.assertGreaterEqual(fast_count, 40)

    def test_without_sv_ttk_the_default_theme_stays(self):
        before = ttk.Style(self.root).theme_use()
        with mock.patch.object(theme, "sv_ttk", None):
            self.assertEqual(theme.apply_appearance(self.root, "dark"), "light")
        self.assertEqual(ttk.Style(self.root).theme_use(), before)
        self.assertEqual(theme.palette(self.root), theme.PALETTES["light"])


if __name__ == "__main__":
    unittest.main()
