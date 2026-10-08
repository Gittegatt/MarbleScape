"""Light and dark appearance of MarbleScape's Tk windows (Sun Valley ttk theme).

``appearance`` is the saved choice: ``system`` follows the Windows app mode,
``light`` and ``dark`` are fixed. Each Tk root applies it once; its Toplevel
windows inherit the theme, and every mapped window gets a matching title bar.
Without the optional ``sv_ttk`` package the default ttk theme stays in use.
"""

from __future__ import annotations

import os
import re
import time

APPEARANCES = ("system", "light", "dark")
APPEARANCE_LABELS = {
    "system": "System (recommended)",
    "light": "Light",
    "dark": "Dark",
}
SYSTEM_POLL_MILLISECONDS = 2000
_PERSONALIZE_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
# Colors MarbleScape sets itself on top of the theme, per resolved mode.
PALETTES = {
    "light": {"link": "#005fb8", "active_row": "#dceeff", "active_row_text": "#1c1c1c",
              "accent_line": "#1a73e8", "success": "#0f7b0f", "warning": "#9d5d00",
              "scale_trough_disabled": "#f0f0f0", "scale_slider_disabled": "#c4c4c4",
              "placeholder": "#8a8a8a", "newest": "#b35900"},
    "dark": {"link": "#57c8ff", "active_row": "#1d3a5c", "active_row_text": "#fafafa",
             "accent_line": "#57c8ff", "success": "#6ccb5f", "warning": "#ffb340",
             "scale_trough_disabled": "#262626", "scale_slider_disabled": "#454545",
             "placeholder": "#8b8b8b", "newest": "#ff9f43"},
}
# ttk widgets whose text color comes from the style. tk_setPalette gives them an
# explicit foreground, which hides the style's grey disabled text.
_STYLE_TEXT_CLASSES = ("TLabel", "TEntry", "TCombobox", "TSpinbox")

try:
    import sv_ttk
except ImportError:  # Optional: the downloader (``--once``) needs no GUI theme.
    sv_ttk = None


def normalize_appearance(value):
    text = str(value).strip().lower() if isinstance(value, str) else ""
    if text not in APPEARANCES:
        raise ValueError("Appearance must be system, light or dark.")
    return text


def system_prefers_dark():
    """True when Windows apps use the dark mode; False elsewhere or when unknown."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _PERSONALIZE_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (OSError, ValueError, TypeError):
        return False


def resolve_appearance(appearance):
    """Return the effective mode, ``light`` or ``dark``."""
    appearance = normalize_appearance(appearance)
    if appearance == "system":
        return "dark" if system_prefers_dark() else "light"
    return appearance


def current_mode(widget):
    """The mode last applied to the widget's Tk root (``light`` without a theme)."""
    return getattr(widget._root(), "_marblescape_mode", "light")


def palette(widget):
    return PALETTES[current_mode(widget)]


def set_title_bar(window, dark):
    """Use the dark or light Windows title bar for a mapped Tk window."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, before Windows 10 20H1
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                return True
    except (AttributeError, OSError, ValueError):
        pass
    return False


# SetPreferredAppMode values: ForceDark, ForceLight.
_NATIVE_MENU_MODES = {"dark": 2, "light": 3}
_PREFERRED_APP_MODE_BUILD = 18362  # Windows 10 1903; before it, ordinal 135 differs.


def set_native_menus(mode):
    """Draw this process's native Windows menus, such as the tray menu, light or dark.

    Dark menus have a dark grey background with white text and check marks.
    Windows offers this only through uxtheme's unnamed SetPreferredAppMode and
    FlushMenuThemes (ordinals 135 and 136); without them menus stay as they are.
    """
    if os.name != "nt" or mode not in _NATIVE_MENU_MODES:
        return False
    try:
        import ctypes
        import sys
        if sys.getwindowsversion().build < _PREFERRED_APP_MODE_BUILD:
            return False
        uxtheme = ctypes.WinDLL("uxtheme")
        uxtheme[135](_NATIVE_MENU_MODES[mode])
        uxtheme[136]()
        return True
    except (AttributeError, OSError, ValueError):
        return False


def _title_bar_on_map(event):
    widget = event.widget
    try:
        if isinstance(widget, str) or widget.winfo_toplevel() is not widget:
            return
        set_title_bar(widget, current_mode(widget) == "dark")
    except Exception:
        pass  # A window closing while it maps needs no title bar.


def _toplevels(root):
    pending = [root]
    while pending:
        widget = pending.pop()
        if widget.winfo_toplevel() is widget:
            yield widget
        pending.extend(widget.winfo_children())


# The Settings tab strip uses toolbuttons; Sun Valley's Toolbutton has no
# selected look, so the strip gets the theme's own notebook-tab images. Other
# themes fall back to the parent ``Toolbutton`` style by name.
TAB_BUTTON_STYLE = "MarbleScapeTab.Toolbutton"
# Section titles as large as the text, semibold; Sun Valley sets them in its
# smaller caption font. Labels built into a section title use SECTION_LABEL_STYLE.
SECTION_FONT = "SunValleyBodyStrongFont"
SECTION_LABEL_STYLE = "MarbleScapeSection.TLabel"
_TAB_BUTTON_TCL = """
ttk::style theme settings sun-valley-{mode} {{
  ttk::style element create MarbleScapeTab.button image [list       marblescape_tab_rest_{mode}       selected marblescape_tab_selected_{mode}       active marblescape_tab_hover_{mode}] -border 13 -padding {{8 8 8 4}} -sticky nsew
  ttk::style layout {style} {{
    MarbleScapeTab.button -children {{
      Toolbutton.padding -children {{Toolbutton.label -side left -expand 1}}
    }}
  }}
  ttk::style configure {style} -padding {{4 1 4 1}} -anchor center
}}
"""


# Tk 9 draws a stretched ttk image element by tiling the area between its
# borders with the image's own small center, one alpha-blended copy at a time:
# a 20 px Sun Valley button sprite needs dozens of copies for one wide field,
# which made resizing and tab switches up to ten times slower. While Sun Valley
# loads, ``ttk::style element create`` is wrapped: each bordered element gets
# copies of its images whose center is widened along every axis where that
# center is uniform, and its original image size as explicit -width/-height,
# so sizes and pixels stay identical. Anything unexpected keeps the original.
SPRITE_WIDEN_PIXELS = (240, 24)
_FAST_SPRITES_TCL = r"""
namespace eval ::marblescape_sv {
  variable wide [dict create]
  variable patched 0
  proc sides {border} {
    switch [llength $border] {
      1 {return [list $border $border $border $border]}
      2 {lassign $border h v; return [list $h $v $h $v]}
      4 {return $border}
    }
    error "unsupported border $border"
  }
  proc pixel {img x y} {
    if {[catch {$img get $x $y -withalpha} value]} {
      set value [list {*}[$img get $x $y] [$img transparency get $x $y]]
    }
    return $value
  }
  proc uniform {img axis from to} {
    set w [image width $img]; set h [image height $img]
    if {$to - $from < 1} {return 0}
    if {$axis eq "x"} {
      for {set y 0} {$y < $h} {incr y} {
        set first [pixel $img $from $y]
        for {set x [expr {$from + 1}]} {$x < $to} {incr x} {
          if {[pixel $img $x $y] ne $first} {return 0}
        }
      }
    } else {
      for {set x 0} {$x < $w} {incr x} {
        set first [pixel $img $x $from]
        for {set y [expr {$from + 1}]} {$y < $to} {incr y} {
          if {[pixel $img $x $y] ne $first} {return 0}
        }
      }
    }
    return 1
  }
  proc widen {img border addw addh} {
    variable wide
    set key [list $img $border]
    if {[dict exists $wide $key]} {return [dict get $wide $key]}
    lassign [sides $border] l t r b
    set w [image width $img]; set h [image height $img]
    if {![uniform $img x $l [expr {$w - $r}]]} {set addw 0}
    if {![uniform $img y $t [expr {$h - $b}]]} {set addh 0}
    if {$addw == 0 && $addh == 0} {
      dict set wide $key $img
      return $img
    }
    set W [expr {$w + $addw}]; set H [expr {$h + $addh}]
    set across [image create photo -width $W -height $h]
    $across copy $img -from 0 0 $l $h -to 0 0 -compositingrule set
    $across copy $img -from $l 0 [expr {$l + 1}] $h -to $l 0 [expr {$l + $addw + 1}] $h -compositingrule set
    $across copy $img -from $l 0 $w $h -to [expr {$l + $addw}] 0 -compositingrule set
    set result [image create photo -width $W -height $H]
    $result copy $across -from 0 0 $W $t -to 0 0 -compositingrule set
    $result copy $across -from 0 $t $W [expr {$t + 1}] -to 0 $t $W [expr {$t + $addh + 1}] -compositingrule set
    $result copy $across -from 0 $t $W $h -to 0 [expr {$t + $addh}] -compositingrule set
    image delete $across
    dict set wide $key $result
    return $result
  }
  proc patch {arguments addw addh} {
    variable patched
    set spec [lindex $arguments 4]
    set options [lrange $arguments 5 end]
    if {[llength $options] % 2 || ![dict exists $options -border]} {return $arguments}
    set border [dict get $options -border]
    set base [lindex $spec 0]
    set changed 0
    set result [list [widen $base $border $addw $addh]]
    if {[lindex $result 0] ne $base} {set changed 1}
    foreach {state image} [lrange $spec 1 end] {
      set copy [widen $image $border $addw $addh]
      if {$copy ne $image} {set changed 1}
      lappend result $state $copy
    }
    if {!$changed} {return $arguments}
    if {![dict exists $options -width]} {dict set options -width [image width $base]}
    if {![dict exists $options -height]} {dict set options -height [image height $base]}
    incr patched
    return [list {*}[lrange $arguments 0 3] $result {*}$options]
  }
  proc style {args} {
    if {[lindex $args 0] eq "element" && [lindex $args 1] eq "create"
        && [lindex $args 3] eq "image" && [llength $args] >= 5} {
      if {![catch {patch $args @ADDW@ @ADDH@} patched_args]} {set args $patched_args}
    }
    tailcall ::marblescape_sv::original_style {*}$args
  }
}
"""


def _use_sun_valley(root, mode):
    """``sv_ttk.set_theme`` without its ``tkinter.Tk`` type check, per interpreter."""
    from tkinter import ttk

    style = ttk.Style(master=root)
    if not getattr(root, "_sv_ttk_loaded", False):
        _load_sun_valley(root, fast_sprites=True)
        root._sv_ttk_loaded = True  # The flag sv_ttk itself uses.
    style.theme_use(f"sun-valley-{mode}")
    for name in ("TLabelframe.Label", SECTION_LABEL_STYLE):
        style.configure(name, font=SECTION_FONT)


def _tab_card_images(root, mode):
    """Tab images in the selected tab's shape, all on the window background.

    Sun Valley's selected tab is a rounded card (the page color) on the tab
    strip's grey; its inactive tab is a plain grey square. The inactive image
    becomes the same card in that grey, the hovered one the card in its hover
    color. The selected card keeps the page color without the grey square
    around it, so it merges with the page below and every tab reads as a card.
    """
    def image(name):
        return root.tk.eval(f"set ttk::theme::sv_{mode}::I({name})")

    def pixel(img, x, y):
        return tuple(int(value) for value in root.tk.splitlist(root.tk.call(img, "get", x, y)))

    selected, rest, hover = image("tab-selected"), image("tab-rest"), image("tab-hover")
    width = int(root.tk.call("image", "width", selected))
    height = int(root.tk.call("image", "height", selected))
    middle = (width // 2, height // 2)
    background = pixel(selected, *middle)  # The card: the page color.
    strip = pixel(rest, *middle)           # Around the card, and the inactive grey.
    hover_color = pixel(hover, *middle)

    def card(name, fill):
        target = f"marblescape_tab_{name}_{mode}"
        root.tk.call("image", "create", "photo", target, "-width", width, "-height", height)
        for y in range(height):
            for x in range(width):
                color = pixel(selected, x, y)
                # How far this pixel is from the card toward the strip around it.
                span = [s - b for s, b in zip(strip, background)]
                share = max(0.0, min(1.0, sum((c - b) / d for c, b, d in zip(color, background, span) if d)
                                     / max(1, sum(1 for d in span if d))))
                new = tuple(round(f + share * (b - f)) for f, b in zip(fill, background))
                root.tk.call(target, "put", "#%02x%02x%02x" % new, "-to", x, y)
        return target

    card("rest", strip)
    card("hover", hover_color)
    card("selected", background)


def _load_sun_valley(root, fast_sprites):
    """Source Sun Valley and the tab style, optionally with widened sprites."""
    def load():
        root.tk.call("source", str(sv_ttk.TCL_THEME_FILE_PATH))
        for theme_mode in ("light", "dark"):
            _tab_card_images(root, theme_mode)
            root.tk.eval(_TAB_BUTTON_TCL.format(mode=theme_mode, style=TAB_BUTTON_STYLE))

    if not fast_sprites:
        load()
        return
    addw, addh = SPRITE_WIDEN_PIXELS
    root.tk.eval(_FAST_SPRITES_TCL.replace("@ADDW@", str(addw)).replace("@ADDH@", str(addh)))
    root.tk.eval("rename ::ttk::style ::marblescape_sv::original_style")
    root.tk.eval("interp alias {} ::ttk::style {} ::marblescape_sv::style")
    try:
        load()
    finally:
        root.tk.eval("interp alias {} ::ttk::style {}")
        root.tk.eval("rename ::marblescape_sv::original_style ::ttk::style")


def fast_sprite_count(root):
    """How many Sun Valley elements were loaded with widened sprites."""
    try:
        return int(root.tk.eval("set ::marblescape_sv::patched"))
    except Exception:
        return 0


# Typing in a dropdown jumps to the first matching entry; letters typed within
# this many seconds extend the search, a longer pause starts a new one.
TYPE_SEARCH_RESET_SECONDS = 1.0
_CONTROL_OR_ALT = 0x4 | 0x20000


def type_search_index(values, typed, start=0):
    """Index of the first value starting with ``typed`` (else containing it).

    The search begins at ``start`` and wraps around; ``None`` without a match.
    Case does not matter.
    """
    text = str(typed).casefold()
    if not text or not values:
        return None
    start = min(max(start, 0), len(values))
    order = [*range(start, len(values)), *range(0, start)]
    for matches in (str.startswith, str.__contains__):
        for index in order:
            if matches(str(values[index]).casefold(), text):
                return index
    return None


def _typed_text(root, path, char):
    """The search text typed so far in the dropdown ``path``, with ``char`` added."""
    state = root.__dict__.setdefault("_marblescape_type_search", {})
    typed, last = state.get(path, ("", 0.0))
    now = time.monotonic()
    typed = (typed if now - last <= TYPE_SEARCH_RESET_SECONDS else "") + char
    state[path] = (typed, now)
    return typed


def _search_target(values, typed, current):
    """Where typing ``typed`` moves from ``current`` (-1 for none)."""
    if len(typed) > 1 and len(set(typed.casefold())) == 1:
        # The same letter again steps through the entries starting with it.
        return type_search_index(values, typed[0], current + 1)
    # A first letter moves on from the current entry; more letters refine it.
    return type_search_index(values, typed, current + 1 if len(typed) == 1 else max(current, 0))


def _search_char(event):
    """The printable character a key press types, or "" (also with Ctrl or Alt)."""
    char = getattr(event, "char", "")
    try:
        modifiers = int(getattr(event, "state", 0) or 0)
    except (TypeError, ValueError):
        modifiers = 0
    if not isinstance(char, str) or len(char) != 1 or not char.isprintable() or modifiers & _CONTROL_OR_ALT:
        return ""
    return char


def _combobox_type_search(event):
    """A focused, closed read-only dropdown: typing chooses the matching entry."""
    widget = event.widget
    char = _search_char(event)
    try:
        if (not char or isinstance(widget, str) or str(widget.cget("state")) != "readonly"
                or widget.instate(["disabled"])):
            return None
        values = widget.tk.splitlist(widget.cget("values"))
        current = widget.current()
        index = _search_target(values, _typed_text(widget._root(), str(widget), char), current)
        if index is not None and index != current:
            widget.current(index)
            widget.event_generate("<<ComboboxSelected>>")
    except Exception:
        return None
    return "break"


def _listbox_type_search(root):
    """An open dropdown list: typing marks the matching entry; Enter chooses it."""
    def search(event):
        char = _search_char(event)
        if not char:
            return None
        path = str(event.widget)
        try:
            values = root.tk.splitlist(root.tk.call(path, "get", 0, "end"))
            selected = root.tk.splitlist(root.tk.call(path, "curselection"))
            current = int(selected[0]) if selected else -1
            index = _search_target(values, _typed_text(root, path, char), current)
            if index is not None:
                root.tk.call(path, "selection", "clear", 0, "end")
                root.tk.call(path, "selection", "set", index)
                root.tk.call(path, "activate", index)
                root.tk.call(path, "see", index)
        except Exception:
            return None
        return "break"
    return search


def _clear_combobox_selection(event):
    try:
        event.widget.selection_clear()
    except Exception:
        pass


def apply_appearance(root, appearance):
    """Apply the saved appearance to a Tk root and its windows; return the mode."""
    mode = resolve_appearance(appearance)
    root._marblescape_appearance = normalize_appearance(appearance)
    if not getattr(root, "_marblescape_combobox_selection", False):
        # A chosen dropdown value stays selected (highlighted) in the field;
        # clear it, as the choice is done. Class binding: every dropdown.
        root.bind_class("TCombobox", "<<ComboboxSelected>>", _clear_combobox_selection, add="+")
        # Typing jumps to the matching entry, in an open list and a closed field.
        root.bind_class("TCombobox", "<KeyPress>", _combobox_type_search, add="+")
        root.bind_class("ComboboxListbox", "<KeyPress>", _listbox_type_search(root), add="+")
        root._marblescape_combobox_selection = True
    if sv_ttk is None:
        root._marblescape_mode = "light"
        return "light"
    if not getattr(root, "_marblescape_palette_guard", False):
        # Sun Valley's <<ThemeChanged>> handler (configure_colors, bound to the
        # root's class) runs tk_setPalette, which resets the foreground of every
        # widget. The "all" tag runs after it, so MarbleScape's colors come back.
        root.bind_all("<<ThemeChanged>>", lambda event: _restore_after_palette(root, event), add="+")
        root._marblescape_palette_guard = True
    if getattr(root, "_marblescape_mode", None) != mode:
        _use_sun_valley(root, mode)
        root._marblescape_mode = mode  # Only once the theme is in use.
        try:
            # Sun Valley colors the root style and plain tk widgets (tk_setPalette)
            # from <<ThemeChanged>>, which the first switch of a root never sends.
            root.tk.call("configure_colors")
        except Exception:
            pass
        _release_text_colors(root)
        _notify_theme_listeners(root)
        _style_menus(root)
    if not getattr(root, "_marblescape_title_bars", False):
        root.bind_all("<Map>", _title_bar_on_map, add="+")
        root._marblescape_title_bars = True
    for window in _toplevels(root):
        if window.winfo_ismapped():
            set_title_bar(window, mode == "dark")
    return mode


def _restore_after_palette(root, event):
    if event.widget is root:
        _release_text_colors(root)
        _notify_theme_listeners(root)
        _style_menus(root)


def _release_text_colors(root):
    """Let ttk text widgets take their colors from the style again.

    Sun Valley calls tk_setPalette, which writes the text color into every widget
    and the option database; a disabled label, field or list then keeps full
    contrast. Disabled fields get the grey of disabled check buttons.
    """
    from tkinter import ttk

    style = ttk.Style(root)
    color = str(style.lookup(".", "foreground")).lower()
    disabled = dict(style.map(".", "foreground")).get("disabled")
    for name in _STYLE_TEXT_CLASSES:
        # Widgets created later: higher priority than tk_setPalette's entries.
        root.option_add(f"*{name}.foreground", "", "interactive")
        if disabled and name != "TLabel":
            style.map(name, foreground=[("disabled", disabled)]
                      + [entry for entry in style.map(name, "foreground") if entry[0] != "disabled"])
    pending = [root]
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if widget.winfo_class() in _STYLE_TEXT_CLASSES:
            try:
                if str(widget.cget("foreground")).lower() == color:
                    widget.configure(foreground="")
            except Exception:
                pass


def _style_menus(root):
    """Draw menu check marks in the text color of the current mode.

    Sun Valley skips its menu colors on Windows and tk_setPalette leaves the
    check marks black, invisible on the dark menu background. Disabled entries
    take the grey of disabled ttk widgets: without a disabled color, Windows
    draws them embossed with a white shadow.
    """
    from tkinter import ttk

    style = ttk.Style(root)
    color = style.lookup(".", "foreground")
    if not color:
        return
    disabled = dict(style.map(".", "foreground")).get("disabled") or "#8a8a8a"
    # Menus created later take the colors from the option database.
    root.option_add("*Menu.selectColor", color)
    root.option_add("*Menu.disabledForeground", disabled)
    pending = [root]
    while pending:
        widget = pending.pop()
        pending.extend(widget.winfo_children())
        if widget.winfo_class() == "Menu":
            try:
                widget.configure(selectcolor=color, disabledforeground=disabled)
                for label in getattr(widget, "_marblescape_disabled", ()):
                    _grey_menu_entry(widget, label)
            except Exception:
                pass


def _grey_menu_entry(menu, entry):
    grey = menu.cget("disabledforeground") or "#8a8a8a"
    menu.entryconfigure(entry, state="normal", command="", foreground=grey, activeforeground=grey,
                        activebackground=menu.cget("background"))


def menu_check_images(widget):
    """(unchecked, checked) images for menu check entries, in the mode's text color.

    On Windows Tk draws a menu's own check mark (and a submenu's arrow) in the
    system menu text color, black on a dark menu until the entry is highlighted.
    A check entry with indicatoron=False, image=unchecked and selectimage=checked
    shows this mark instead. The arrow has no such option and stays as it is. The images follow the mode; one pair per root and
    color, created with the root as master.
    """
    from PIL import Image, ImageDraw, ImageTk
    from tkinter import font as tkfont

    root = widget._root()
    try:
        # Tk names system colors (SystemWindowText); Pillow needs #RRGGBB.
        red, green, blue = root.winfo_rgb(
            root.tk.call("ttk::style", "lookup", ".", "-foreground") or "#000000")
        color = f"#{red >> 8:02x}{green >> 8:02x}{blue >> 8:02x}"
    except Exception:
        color = "#000000"
    cache = root.__dict__.setdefault("_marblescape_menu_checks", {})
    if str(color) not in cache:
        size = max(12, tkfont.nametofont("TkMenuFont", root=root).metrics("linespace"))
        scale = 4  # Drawn larger, then reduced: smooth edges.
        mark = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
        points = [(0.22, 0.52), (0.42, 0.72), (0.80, 0.30)]
        ImageDraw.Draw(mark).line([(x * size * scale, y * size * scale) for x, y in points],
                                  fill=str(color), width=max(2, round(size * scale * 0.11)), joint="curve")
        mark = mark.resize((size, size), Image.Resampling.LANCZOS)
        cache[str(color)] = (ImageTk.PhotoImage(Image.new("RGBA", (size, size), (0, 0, 0, 0)), master=root),
                             ImageTk.PhotoImage(mark, master=root))
    return cache[str(color)]


def set_menu_entry_enabled(menu, entry, enabled):
    """Enable a menu entry or grey it out, without Windows' embossed look.

    On Windows Tk draws disabled menu entries with a white 3-D shadow, whatever
    the disabled color. A greyed-out entry therefore stays technically normal:
    it shows the disabled color, is not highlighted and does nothing.
    """
    label = menu.entrycget(entry, "label")
    commands = menu.__dict__.setdefault("_marblescape_commands", {})
    disabled = menu.__dict__.setdefault("_marblescape_disabled", set())
    commands.setdefault(label, menu.entrycget(entry, "command"))
    if enabled:
        disabled.discard(label)
        menu.entryconfigure(entry, state="normal", command=commands[label], foreground="",
                            activeforeground="", activebackground="")
    else:
        disabled.add(label)
        _grey_menu_entry(menu, entry)


def menu_entry_enabled(menu, entry):
    return menu.entrycget(entry, "label") not in getattr(menu, "_marblescape_disabled", ())


def follow_system(root, appearance_getter):
    """While ``system`` is chosen, switch the open windows with the Windows mode."""
    def poll():
        try:
            if not root.winfo_exists():
                return
            appearance = appearance_getter()
            if normalize_appearance(appearance) != getattr(root, "_marblescape_appearance", None) \
                    or resolve_appearance(appearance) != current_mode(root):
                apply_appearance(root, appearance)
        except Exception:
            pass  # Keep the current look if the setting cannot be read.
        try:
            root.after(SYSTEM_POLL_MILLISECONDS, poll)
        except Exception:
            pass  # The window has closed.

    root.after(SYSTEM_POLL_MILLISECONDS, poll)


def on_theme_change(widget, callback):
    """Run ``callback`` now and after every appearance change of the widget's root.

    Plain tk widgets receive no <<ThemeChanged>> event, so colors MarbleScape sets
    itself are refreshed from this per-root list.
    """
    root = widget._root()
    listeners = getattr(root, "_marblescape_theme_listeners", None)
    if listeners is None:
        listeners = root._marblescape_theme_listeners = []
    listeners.append((widget, callback))
    callback()


def _notify_theme_listeners(root):
    alive = []
    for widget, callback in getattr(root, "_marblescape_theme_listeners", ()):
        try:
            if not widget.winfo_exists():
                continue
            callback()
        except Exception:
            continue  # A widget that is closing is dropped from the list.
        alive.append((widget, callback))
    root._marblescape_theme_listeners = alive


def keep_background(widget, style_name="TFrame"):
    """Keep a tk widget's explicit background equal to the theme's ``style_name``."""
    from tkinter import ttk

    def refresh():
        style = ttk.Style(widget)
        # Sun Valley sets the background on the root style only.
        color = style.lookup(style_name, "background") or style.lookup(".", "background")
        if color:
            widget.configure(background=color)

    on_theme_change(widget, refresh)


def style_scale(scale, value_label=None):
    """Follow the theme with a classic Tk slider and show clearly when it is disabled.

    Sun Valley styles only ttk widgets; a disabled ``tk.Scale`` looked unchanged.
    ``set_scale_enabled`` switches it; ``value_label`` (a ttk label) greys with it.
    """
    def refresh():
        enabled = str(scale.cget("state")) != "disabled"
        if enabled:
            colors = {"background": scale.option_get("background", "Background"),
                      "troughcolor": scale.option_get("troughColor", "Background"),
                      "activebackground": scale.option_get("activeBackground", "Foreground")}
            scale.configure(sliderrelief="raised", **{key: value for key, value in colors.items() if value})
        else:
            colors = palette(scale)
            scale.configure(sliderrelief="flat", background=colors["scale_slider_disabled"],
                            activebackground=colors["scale_slider_disabled"],
                            troughcolor=colors["scale_trough_disabled"])
        if value_label is not None:
            value_label.state(["!disabled"] if enabled else ["disabled"])

    scale._marblescape_refresh = refresh
    on_theme_change(scale, refresh)


def set_scale_enabled(scale, enabled):
    scale.configure(state="normal" if enabled else "disabled")
    refresh = getattr(scale, "_marblescape_refresh", None)
    if refresh is not None:
        refresh()


def style_swatch(swatch):
    """Frame a color preview in the text color, visible on both backgrounds."""
    from tkinter import ttk

    swatch.configure(relief="flat", borderwidth=0, highlightthickness=1)

    def refresh():
        color = ttk.Style(swatch).lookup(".", "foreground")
        if color:
            swatch.configure(highlightbackground=color, highlightcolor=color)

    on_theme_change(swatch, refresh)


def _pixels(widget, value):
    """Sum of a Tk padding value: an int, a tuple or Tcl text such as "8 4"."""
    if isinstance(value, (tuple, list)):
        parts = list(value)
    else:
        parts = widget.tk.splitlist(str(value)) if str(value).strip() else []
    return [int(float(str(part))) for part in parts]


def follow_wrap_width(label, minimum=200):
    """Wrap ``label`` where its section ends instead of at a fixed width.

    The label keeps its initial wraplength until the section is laid out.
    Then it wraps at the section's inner right edge, so a text stays on one
    line whenever the window is wide enough and wraps only when it is not.
    """
    master = label.master

    def right_padding():
        padding = 0
        try:
            values = _pixels(master, master.cget("padding"))
            padding = values[0] if len(values) == 1 else values[0] if len(values) == 2 else values[2] if values else 0
        except Exception:
            padding = 0
        try:
            padx = _pixels(label, label.grid_info().get("padx", 0))
            padding += padx[-1] if padx else 0
        except Exception:
            pass
        # A labelled frame's border.
        return padding + (2 if master.winfo_class() == "TLabelframe" else 0)

    def update(_event=None):
        try:
            width = master.winfo_width()
            if width <= 1 or not label.winfo_exists():
                return
            wrap = max(minimum, width - label.winfo_x() - right_padding())
            if int(float(str(label.cget("wraplength")) or 0)) != wrap:
                label.configure(wraplength=wrap)
        except Exception:
            return

    master.bind("<Configure>", update, add="+")
    label.bind("<Map>", update, add="+")
    label._marblescape_follows_width = True


# Sun Valley draws an entry's field with one image per state.
_FIELD_IMAGES = (("disabled", "textbox-dis"), ("invalid", "textbox-error"),
                 ("focus", "textbox-focus"), ("hover", "textbox-hover"))


def entry_field_color(entry):
    """The color Sun Valley fills ``entry``'s field with in its current state."""
    from tkinter import ttk

    root = entry._root()
    states = set(map(str, entry.state()))
    key = next((image for state, image in _FIELD_IMAGES if state in states), "textbox-rest")
    namespace = "sv_dark" if current_mode(entry) == "dark" else "sv_light"
    try:
        image = root.tk.call("set", f"ttk::theme::{namespace}::I({key})")
        width = int(root.tk.call("image", "width", image))
        height = int(root.tk.call("image", "height", image))
        red, green, blue = (int(value) for value in root.tk.call(image, "get", width // 2, height // 2)[:3])
        return f"#{red:02x}{green:02x}{blue:02x}"
    except Exception:
        style = ttk.Style(entry)
        return style.lookup("TEntry", "fieldbackground") or style.lookup(".", "background") or "#ffffff"


def entry_placeholder(entry, variable, text):
    """Show ``text`` in grey inside ``entry`` while it is empty and not focused.

    The hint sits where typed text starts and has the field's color in every
    state; a click on it puts the cursor into the entry. Returns the label.
    """
    import tkinter as tk
    from tkinter import ttk

    label = tk.Label(entry, text=text, anchor="w", borderwidth=0, padx=0, pady=0,
                     highlightthickness=0, cursor="xterm", takefocus=0)

    def refresh(*_args):
        try:
            if not entry.winfo_exists():
                return
        except tk.TclError:
            return
        # Gone while the entry has content or the cursor (a click into it).
        if variable.get() or "focus" in set(map(str, entry.state())):
            label.place_forget()
            return
        style = ttk.Style(entry)
        try:
            left = int(float(str(style.lookup("TEntry", "padding") or "0").split()[0]))
        except (ValueError, IndexError):
            left = 0
        label.configure(background=entry_field_color(entry), foreground=palette(entry)["placeholder"],
                        font=entry.cget("font") or style.lookup("TEntry", "font") or "TkDefaultFont")
        # One pixel of field border, then the entry's own left padding.
        label.place(x=left + 1, rely=0.5, anchor="w")

    pending = {"id": None}

    def run():
        pending["id"] = None
        refresh()

    def later(*_args):
        # Class bindings change the entry's state after the widget's own ones;
        # one refresh waits at a time and is dropped with the entry.
        if pending["id"] is None:
            try:
                pending["id"] = entry.after_idle(run)
            except tk.TclError:
                pass

    def destroyed(event):
        if event.widget is entry and pending["id"] is not None:
            try:
                entry.after_cancel(pending["id"])
            except tk.TclError:
                pass
            pending["id"] = None

    def click(_event):
        entry.focus_set()
        entry.icursor("end")
        return "break"

    def pointer_over_label(_event):
        # Over the label the entry keeps its hover look, as over its own text.
        if "disabled" not in set(map(str, entry.state())):
            entry.state(["hover"])
        later()

    def pointer_left_label(event):
        x, y = event.x_root - entry.winfo_rootx(), event.y_root - entry.winfo_rooty()
        if not (0 <= x < entry.winfo_width() and 0 <= y < entry.winfo_height()):
            entry.state(["!hover"])
        later()

    label.bind("<Button-1>", click)
    label.bind("<Enter>", pointer_over_label)
    label.bind("<Leave>", pointer_left_label)
    for sequence in ("<FocusIn>", "<FocusOut>", "<Enter>", "<Leave>", "<Configure>"):
        entry.bind(sequence, later, add="+")
    entry.bind("<Destroy>", destroyed, add="+")
    variable.trace_add("write", later)
    on_theme_change(entry, refresh)
    return label


_MARKUP = re.compile(r"\*\*(.+?)\*\*|(?<![*\w])\*([^*\n]+?)\*(?![*\w])", re.S)


def parse_markup(text):
    """[(chunk, tags)] of ``text``: **bold** and *italic*; everything else as it is."""
    parts, position = [], 0
    for match in _MARKUP.finditer(text):
        if match.start() > position:
            parts.append((text[position:match.start()], ()))
        parts.append((match.group(1), ("bold",)) if match.group(1) is not None
                     else (match.group(2), ("italic",)))
        position = match.end()
    if position < len(text):
        parts.append((text[position:], ()))
    return parts


def plain_text(text):
    """``text`` as shown: without the ** and * markers."""
    return "".join(chunk for chunk, _tags in parse_markup(text))


def markup_text(parent, text):
    """A read-only text with **bold** and *italic* that wraps at its width and is
    exactly as tall as its lines (Info tab). Text can be selected and copied."""
    import tkinter as tk
    from tkinter import font as tkfont

    widget = tk.Text(parent, wrap="word", width=1, height=1, borderwidth=0, highlightthickness=0,
                     padx=0, pady=0, takefocus=0, cursor="")
    # Created once and only reconfigured: new font objects on every theme
    # change could be finalized on another thread.
    bold, italic = tkfont.Font(root=widget), tkfont.Font(root=widget)
    widget._marblescape_fonts = (bold, italic)  # Tk forgets fonts Python no longer holds.
    widget.tag_configure("bold", font=bold)
    widget.tag_configure("italic", font=italic)

    def fonts():
        # The labels' font: Sun Valley's body font, larger than Tk's default.
        from tkinter import ttk
        name = ttk.Style(widget).lookup(".", "font")
        if not name and "SunValleyBodyFont" in tkfont.names(root=widget):
            name = "SunValleyBodyFont"
        try:
            base = tkfont.nametofont(name or "TkDefaultFont", root=widget)
        except tk.TclError:
            base = tkfont.nametofont("TkDefaultFont", root=widget)
        if str(widget.cget("font")) != base.name:
            widget.configure(font=base)
        actual = base.actual()
        bold.configure(**dict(actual, weight="bold"))
        italic.configure(**dict(actual, slant="italic"))

    fonts()
    for chunk, tags in parse_markup(text):
        widget.insert("end", chunk, tags)
    widget.configure(state="disabled")

    def fit(_event=None):
        try:
            lines = widget.count("1.0", "end-1c", "update", "displaylines")
        except tk.TclError:
            return
        count = (lines[0] if isinstance(lines, tuple) else lines or 0) + 1
        if int(widget.cget("height")) != count:
            widget.configure(height=count)

    def colors():
        from tkinter import ttk
        style = ttk.Style(widget)
        background = style.lookup("TFrame", "background") or style.lookup(".", "background")
        foreground = style.lookup("TLabel", "foreground") or style.lookup(".", "foreground")
        options = {key: value for key, value in (
            ("background", background), ("foreground", foreground),
            ("selectbackground", style.lookup(".", "selectbackground")),
            ("selectforeground", style.lookup(".", "selectforeground"))) if value}
        widget.configure(inactiveselectbackground=options.get("selectbackground", ""), **options)
        fonts()

    widget.bind("<Configure>", fit, add="+")
    on_theme_change(widget, colors)
    return widget


def keep_palette_color(widget, option, key):
    """Keep a tk widget option on the palette color ``key`` of the current mode."""
    on_theme_change(widget, lambda: widget.configure(**{option: palette(widget)[key]}))
