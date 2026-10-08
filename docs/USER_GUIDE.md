# User guide

Settings, profiles, rotation, backups, storage, and configuration reference.

[Back to the main README](../README.md)

## Contents

- [Getting started](#getting-started)
- [Windows tray menu](#windows-tray-menu)
- [Settings window](#settings-window)
- [General tab](#general-tab)
- [Downloads & Updates tab](#downloads--updates-tab)
- [Image tab](#image-tab)
- [Image profiles and rotation](#image-profiles-and-rotation)
- [Import and export profiles](#import-and-export-profiles)
- [Storage and History](#storage-and-history)
- [Latest snapshot (no profile)](#latest-snapshot-no-profile)
- [Data coverage](#data-coverage)
- [Settings backups](#settings-backups)
- [Image metadata](#image-metadata)
- [Configuration](#configuration)

## Getting started

A condensed, sectioned version of this guidance is available inside MarbleScape
under **Info**.

### Recommended workflow

1. Open the source website from the **Sources** tab in MarbleScape, explore its
   views and adjust the visualization until the imagery matches your intended
   result. This gives you a clear preview of the source data.
2. Transfer the relevant choices to the **Image** tab: image source, satellite,
   mission, product, layer, projection, area, custom latitude and longitude,
   zoom, date, coverage options and other source-specific settings.
3. Configure output width, height, aspect ratio, fit mode and wallpaper position
   for your monitor under **General**.
4. Select **Apply Image**, review the resulting wallpaper and refine the settings
   if necessary.
5. Save the current Image settings as a profile under **Profiles**, so
   the same view can be restored or included in a rotation.

### Image detail

For cleaner edges, finer details and fewer stair-step artifacts, choose a source
resolution one available size above the required output when bandwidth and
provider limits allow it. **Automatic** remains the efficient starting point.

### Matching provider settings

Provider websites and MarbleScape can use slightly different labels or expose
different subsets of the same catalogue. Use the actual satellite view and
geographic result as the reference. Satellite imagery can contain seams,
processing artifacts and areas with missing or partial imagery; see
[Satellite imagery artifacts](IMAGERY_ARTIFACTS.md).

Copernicus map overlays (**Labels** and **Country borders**) are independent of each
other and apply to the Sentinel-1 and Sentinel-2 mosaic products too. A
precomputed cloudless mosaic can still contain transparent No Data pixels,
residual clouds, snow, bright terrain artifacts or source-tile seams; compare
the same period in Copernicus Browser when checking an apparent gap.

## Windows tray menu

Manual Windows launches open Settings and the tray icon. Windows autostart uses
`--background` and opens no Settings or update-notice window. Diagnostic and
one-shot commands do not show a tray icon.

### Menu entries

From top to bottom:

- **Open image folder** (opens the Latest folder) and **Settings...**;
- **Profile rotation** on/off (it keeps the rotation list as last saved, and an
  open Settings window shows the new state at once);
- **Force loading new image**, the current activity status and **Next check**;
- **Support this project**;
- **Start minimized with Windows** (per-user Windows startup), **Restart** and **Exit**.

Clicking or double-clicking the tray icon opens Settings. If Settings is already
open or minimized, it is brought to the foreground; repeated clicks do not create
additional windows.

### Status and next check

The lower menu shows "↻ Checking for new image...", "↻ Fetching new image..." or
"Standing by...", followed by **Next check** in the selected display time zone. The
↻ symbol marks running work for every image source, here and for checks in the
profile table's symbol column left of **Status** (which marks downloads with ⭳ and
Copernicus rendering with ⧉). While all catalogues refresh (by hand, daily or at
startup), the status adds it after a pipe, for example
"Standing by... | Refreshing catalogues 2/5"; the Settings footer shows the same.

### Force loading new image

Available in the tray menu and in Settings under **Image**. While MarbleScape is
standing by, either control requests an immediate download even when the
provider timestamp has not changed. Identical content does not create a
duplicate History image.

### Restart, exit and applied changes

Changes made in Settings are written to the active local TOML file and applied
by the running application without restarting it; a new image is requested when
required.

- **Exit** cancels active image transfers and catalogue refreshes, including
  pending connections and blocked reads, and waits for the image worker to finish.
  Should anything still hang after 10 seconds, MarbleScape ends anyway.
- **Restart** remains available for troubleshooting. It uses the same
  cancellation path and waits for the old process to release the single-instance
  lock before activating the new one. An old process that still runs after 15
  seconds is ended.
- Both are also in Settings under **General** > **Actions**.

Only one MarbleScape runs at a time. Windows Task Manager may still show two
Python processes for it: a small starter of the Python environment and the
program itself.

### Updates and support

- **About** > **Check for updates** opens the same notice as the startup check when
  a newer public release is available. A manual check still shows a release that
  was skipped at startup.
- **Support this project** (tray, and **About** with a heart before it) opens a small dialog with
  monochrome buttons for starring the GitHub repository, Ko-fi and PayPal. It is centered
  over Settings when Settings is open, otherwise on the screen, and launches no
  website until you select a link. Repeated requests reuse the open dialog;
  **Close** or **Esc** dismisses it.

## Settings window

### Tabs

- **General:** **Startup and wallpaper**, **Output device**, **Monitor output**, **Date and time** and **Appearance** (light/dark).
- **Downloads & Updates:** image update checks, transfer display, download retries, catalogue refresh and catalogue retries.
- **Image:** source, source-specific selection, framing and **Force loading new image**.
- **Profiles:** named Image snapshots, their order and the rotation.
- **History & Storage:** Latest and History folders, retention and storage status.
- **Access:** what an image source needs to be reached, chosen under **Image
  source**; for **Copernicus Browser** the OAuth client (**Client ID** and **Client
  secret**). **Save** stores the credentials; they are kept protected and never
  logged. The Copernicus account credits close the tab as an own **Credits**
  section.
- **Backup:** JSON import/export of settings only or settings and all profiles.
- **Sources:** links to the satellite imagery viewers used by MarbleScape,
  grouped by provider. Technical API and data endpoints are not listed.
- **Info:** short, separate sections on the main topics. Explanations that used
  to appear below individual settings (monitor output, render quality, source
  resolution, fit/crop, imagery notice, retries, daily catalogue refresh and
  rotation) are collected there.
- **About:** About MarbleScape, Project & updates, Help & support, Privacy & local
  data, and License & credits, including the installed version, the imagery
  notice, the manual update check, reference links and **Support this project**.

### Window layout

The resizable window opens centered at the size it last had, at first at the
minimum width of 900 and a height of 700 logical pixels, adjusted for Windows
scaling and the desktop work area. A new size is saved at once (no **Save**) as
`display.settings_window_width` and `settings_window_height` at 100 % scaling
(0 = default); a maximized window keeps the last normal size. It also opens on
the tab used last (`display.settings_tab`, saved at once; General if that tab no
longer exists). **Save** (or **Apply
Image**), **OK** and **Close** are equally wide, as wide as the widest of them. Unavailable
context menu entries are plain grey without the embossed Windows look. Each tab heading gets an equal
share of the tab-bar width. Each tab scrolls vertically when needed (scrollbar or
mouse wheel); keyboard navigation reveals focused settings automatically.
Typing in a dropdown jumps to the first entry that starts with the typed letters,
or else contains them; letters typed within a second extend the search, and the
same letter again steps to the next entry with it. In an open list **Enter** chooses
the marked entry; a focused, closed dropdown takes the matching entry at once (a
draft until Save, like every choice).
The footer stays visible outside the scrolling area. On the left, from top to
bottom: the **Next check** timestamp, the activity status and the download text.
On the right: the save notice beside the main action and **OK**/**Close**, and the
progress bar, the download status and **Cancel download** on the download text's
row.

### Apply Image, Save, OK and Close

- **Apply Image** (while **Image** is selected) saves only that tab's source and
  framing choices and keeps Settings open. It requests a new image only when they
  changed; General output and Storage edits are left untouched. It always stays
  available.
- **Save** right of it (Image tab only; on every tab **Save** stands right before
  **OK**) saves the same choices but keeps the picture on screen: the saved settings apply at the next regular image check, one update
  interval after saving. The note below the buttons names the time, e.g. "✓ Saved -
  loads at the next update (*19:45*)", until the Image tab changes again. While a
  rotation profile is shown, it stays until no rotation profile is shown; the note
  then reads "✓ Saved - rotation keeps showing its profiles".
- **Save** (on every other tab) saves non-Image settings without starting a new
  image load. It is available only while such settings differ from the saved
  ones; reverting an edit by hand greys it out again.
- **OK** validates and saves the entire dialog, closes it and applies changed
  image settings.
- **Close** discards changes made since the last save.

Below the buttons, right-aligned, "Unsaved changes" (orange) shows while a tab with
**Save** has changes waiting, and a green "✓ Saved" confirms a save until the next
change. On the **Profiles** tab the notice reads "This tab saves automatically."
(blue), and a saved change shows "✓ Saved" for a few seconds. The **Image** tab
shows the note of its **Save** (see above); its header marks changed settings.
The note never moves the buttons: a long one reaches left up to the activity text,
and one that does not fit ends in "…".

After each image update the same place tells how it went, with the time, e.g.
"✓ Image downloaded · 12:40" (green), "✓ Image restored
from cache" (blue), "✓ Image up to date" (blue), "? Image source no longer
listed", "? Source unavailable", "! Network issue", "! Image unavailable" (orange)
or "× Download cancelled". Catalogue refreshes note there too: "✓ Catalogues
refreshed" or "✓ Catalogues up to date" after refreshing all catalogues, "? Catalogue
refresh incomplete: *NOAA, Himawari*" (the sources not read completely; their saved
catalogue stays in use) or "! Catalogue refresh failed: network issue"; the Image
tab's **Refresh catalogue** (every source, also EUMETSAT and Copernicus) notes
"✓ Catalogue refreshed: *Himawari*" or its problem, while automatic reloads stay
silent; "✓ Active storms updated: *NOAA (+1 new, 1 ended)*" (blue) shows only when
the hourly storm check changed a list. The newest of an update note and a save note shows;
"Unsaved changes" always shows. **Info** > **Status** compares these notes with the
profile table's Status column.

Everything in **Profiles**, including the table view and its History
checkboxes, needs no Save; it is stored at once. If such a change is stored while
the image requested by **Apply Image** is still downloading, that download
restarts at once instead of waiting for the next update check.

### Download progress and Cancel download

During an image transfer, the footer can show the current download speed,
transferred size, percentage and a progress bar (configured on the
[Downloads & Updates tab](#downloads--updates-tab)).

- An exact total and percentage appear when the server supplies a valid
  `Content-Length` for the complete transfer. MarbleScape makes no extra requests
  merely to determine a size.
- Dynamic tiled or multi-request images show the transferred amount and an
  indeterminate bar until the total is known. Himawari NICT and CIRA SLIDER also
  show finished/all parts, because their tile count is known in advance.
- The "Completed." label appears only after the image is installed, not as soon
  as transferred bytes reach 100%.

**Cancel download** is enabled while an image transfer can still be stopped.
Cancelling keeps the current wallpaper, discards the unfinished result and
creates no History or profile-cache entry. A cancelled rotation download does not
use up one of its failure attempts; that profile becomes eligible again at the
next regular rotation interval.

### Status and storage

The read-only **Status and storage** section shows the current image size, latest
image count, estimated History count, maximum total image count, estimated
storage, current Latest/cache/History usage and the next check time. Usage is
shown in MB, switching to GB at 1,000 MB. Estimates use the saved configuration
and current image size (unsaved edits do not count, except the three profile cache
sliders, which the estimate follows while they move); time-based retention
estimates assume a new image each cycle.

**Total storage estimate** includes the most the
[profile image cache](#profile-image-cache) may hold: for saved profiles their
variants within the size limit, plus the Latest snapshot size limit. Each current
picture counts even when that is more.

### Startup update check

At manual tray startup, MarbleScape checks the newest public GitHub release
without delaying image updates. When it is newer than the installed version, a
small window offers **Skip this version** and **Open GitHub**; skipping is stored
in the configuration and applies only to that version. A private repository
cannot be checked without GitHub authentication: **About** then reports that no
public version is accessible, and the project button can still open the
repository in your signed-in browser.

## General tab

### Output device

**Display** lists the connected displays and opens on the first one. Its size,
aspect ratio, background color, position and **Render quality factor** show
below. Values are saved by monitor ID.

- **One display:** its changes are the shared settings, which displays connected
  later use too.
- **Several displays:** a change applies to the selected display only.
  **Apply to all displays** makes the selected display's settings the shared ones
  of every display (also of displays connected later) and removes the other
  displays' own values; like other General settings it waits for **Save**.
- If Windows cannot identify any display, the list shows **All displays**, which
  edits the shared settings.

- **Disconnected displays** keep their settings but receive no wallpaper; they get
  their saved settings again on the next wallpaper update after reconnection.
- **Display changes:** when a display is connected, wakes up, is rotated or changes
  resolution, MarbleScape sets the picture on screen again a few seconds later,
  without a download. This also covers a Windows start where a display is ready only
  after MarbleScape set the wallpaper.
- **Displays without a name:** after a start or wake-up, Windows can list a display
  without its device name. MarbleScape finds the display at the same place, so its
  own settings still apply.
- **Pause wallpaper updates** (right below **Display**, unchecked by default) leaves
  the selected display's current picture in place; downloads continue, and the
  display's position and output settings stay for when you uncheck it. It is saved
  with **Save**/**OK** and is not copied by **Apply to all displays**. It replaces the
  former position **Do not update (keep current wallpaper)**: a display saved with it
  is paused and keeps the shared position.
- **Restore previous wallpaper** immediately restores the image MarbleScape saved
  before it first replaced the wallpaper on that display, then pauses its wallpaper
  updates. This is saved at once and needs no **Save**. The copy is kept in
  `content/previous_wallpapers`. An image replaced before this feature was installed cannot be recovered; for a Windows slideshow
  the saved copy is the image visible when MarbleScape first updated the display.
- **Render quality factor** sizes the picture for the display, for every image
  source, and can only use detail present in the shared downloaded image. With one
  display (or after **Apply to all displays**) it is the shared value, saved as
  `output.display_render_scale`; a display with its own value keeps it. It is a
  device setting: profiles, exports and PNG metadata never contain it. EUMETSAT's
  **Render quality factor** uses it while set to **Default (General)**.
- A display's **background color** fills placement margins; background already
  rendered into the shared image remains unchanged.

Windows uses one system-wide placement mode, so switching to per-display placement
may change how an existing wallpaper on a skipped display is scaled.

### Monitor output

Resolution and aspect-ratio presets fill the editable width, height and ratio
fields; choose **Custom** or edit the fields directly for another size. Sizes are
listed by their exact aspect ratio as "ratio | width × height (name)", for
example "16:9 | 3840 × 2160 (4K UHD)", "9:16 | 2160 × 3840 (4K UHD)" (portrait)
or "~21:9 | 3440 × 1440 (UWQHD)"; common names appear only where they exist.
Aspect ratios show the usual way of writing them first and the reduced ratio in
brackets when it differs, for example "16:10 (8:5)" or "10:16 (5:8)". When
`height = 0` in the configuration, the height is calculated from `width` and
`aspect_ratio`.

Monitor output controls the wallpaper dimensions and shows the same fields for
every source. Each source sets its image detail in **Image** > **Rendering** instead:
**Source resolution** (NOAA, Himawari, CIRA SLIDER), **Render resolution** (NASA
Worldview), **Image resolution** (Copernicus, with brightness and contrast
correction) or **Render quality** (EUMETSAT). **Fit
mode**, **Zoom** and **Background color** determine the framing.
Behind **Source resolution**, **Render resolution** and **Image resolution** the
label names the pixel size the choice gives with the current settings, e.g.
"Source resolution (5424 × 5424)". It follows the choice, **Monitor output**, **Fit
mode** and **Zoom**, so it also shows what **Automatic** or **Auto** takes. The
size shows once the source's catalogue is loaded.

**Background color** fills margins around the image. With Copernicus it also fills
transparent areas of the wallpaper copy, while a **No-data color** or **Blur** is part of
the image itself. MarbleScape also gives Windows' own desktop color (**Settings** >
**Personalization** > **Background**) the background color, so areas Windows leaves
uncovered match. Windows has one such color for all displays; MarbleScape keeps
your previous one and restores it with **Restore previous wallpaper** once no
connected display is updated by MarbleScape any more.

**The saved picture is as large as the displays need:** with Windows wallpapers on,
MarbleScape enlarges the shared output (keeping its aspect ratio) until it covers
every active display, its physical size and its own **Monitor output**; paused
displays do not count. A 2160 × 1215 shared output with a display set to 3840 wide
saves a 3840 × 2160 picture, so the detail **Automatic** source resolution downloads
for that display is kept instead of being enlarged again. EUMETSAT stays within its
4000-pixel WMS limit. Copernicus **Image resolution** **Auto** uses the same rule.

Saving a new shared output size, aspect ratio or background color
renders the picture on screen again at once, with the same profile, even while
rotation runs; it downloads that picture once more. Other General settings take
effect without a new download.

### Wallpaper position and fit mode

**Position** offers **Center**, **Tile**, **Stretch**, **Fit**, **Fill** and **Span** for the
selected output device; **Pause wallpaper updates** under **Output device** keeps a
display's current picture. MarbleScape prepares
a monitor-sized image when positions differ between displays. A position change
uses the existing local Latest image without requesting a new source image, also
while the source is temporarily unavailable, and leaves the regular image-check
schedule intact.

Image **Fit mode** frames the view inside the generated file: **fit** retains the
whole view, **crop** fills the output and trims edges.

### Date and time

**System time (recommended)** follows the Windows time zone and daylight-saving
rules; displayed local values include their effective UTC offset. **UTC** is the
alternative. The choice affects displayed acquisition and next-check times and
the calendar boundary used to resolve relative quarter/month selections. Provider
request timestamps and cached observation times remain in UTC.

### Appearance

**Mode** sets the look of MarbleScape's windows (Settings, dialogs and the startup
error window), using the Windows 11-style Sun Valley theme:

- **System (recommended)** follows the Windows app mode (**Settings** >
  **Personalization** > **Colors**) and switches open windows within about two seconds
  when it changes.
- **Light** and **Dark** stay fixed.

**Save** or **OK** applies a new choice to the open window at once; it is stored as
`[display] appearance` and never changes the downloaded image. Title bars follow
the mode on Windows 10 and 11, and the MarbleScape icon stays in each window's
title bar. The tray menu follows the mode too each time it opens (Windows 10
version 1903 and later): dark grey with white text and check marks in dark mode.
The check marks in the Settings menus (**Columns**) have the text color of the mode;
the small submenu arrows are drawn by Windows and turn white only when highlighted.
Windows' own dialogs (file, color and message boxes) keep the Windows look.

In both modes, fields, lists and sliders that are not available for the current
choice show grey text (a slider also a flat grey handle and a greyed value), like
unavailable check boxes and buttons. Color previews have a frame in the text
color, white in dark mode.

### Actions

The last section of **General**, with its buttons side by side:

- **Show log** opens `content/marblescape.log` in the default text editor: what
  MarbleScape did, with times, for example each update check and how long it took,
  failed downloads and catalogue refreshes. It starts anew at about 1 MB; the two
  files before (`marblescape.log.1`, `.2`) are kept. Copernicus credentials and
  access tokens never appear in it.
- **Restart** and **Exit** work like the tray entries (see
  [Restart, exit and applied changes](#restart-exit-and-applied-changes)). While
  Settings has unsaved changes, they ask first.

## Downloads & Updates tab

### Image update checks

**Update check interval** uses separate value and unit dropdowns: minutes, hours,
days, weeks or months (a month is an elapsed 30-day interval). The duration is
stored in minutes, so older configurations remain valid. New configurations start
at 15 minutes; a saved interval is kept. **Next check:** shows the next scheduled
image check in the display time zone. **Save**/**OK** starts using the new interval.
Below 2 minutes an orange hint on the line below the dropdowns notes that most
sources publish a new picture only every 5-15 minutes (Copernicus every few days),
so shorter checks mostly find nothing new ([how often each view updates](IMAGE_SOURCES.md#how-often-each-view-updates));
such an interval still works. With **months**, the line below the dropdowns
explains that a month is an elapsed 30-day interval.

### Progress display

Each part of the footer progress (speed, size, percentage, progress bar) can be
switched on or off. The speed unit is **Automatic**, **KB/s**, **MB/s** or **Mbit/s**. **Keep
completed download visible until next download** retains the successful 100%
result; otherwise it disappears after a short delay.

### Retries

- **Download retries:** 1-9 retries after the first attempt (2-10 attempts in
  total) for temporary image-transfer failures. Invalid requests and
  authentication errors are reported without retrying.
- **Catalogue retries:** independently 1-9 retries after the first metadata
  request. If every attempt fails, MarbleScape uses the most recent catalogue data
  in `content/catalogues.json` when available.

### Catalogue refresh

**Refresh all catalogues** forces a refresh now and shows its status, progress bar
and result ("Completed." or "Finished with issues." with details below). This
also covers refreshes started at startup or by the schedule.

**Daily catalogue refresh** sets a local system time with three dropdowns
(HH / MM / SS); the saved time and when the last refresh of all catalogues
actually completed are shown below (a planned slot that was skipped or moved by
a new time does not count). The initial time is **03:00:00**, independent of the display time-zone
preference. **Save**/**OK** saves the time without starting a refresh.

- **Covered sources:** NOAA, Himawari, CIRA SLIDER, NASA Worldview and EUMETSAT,
  plus Copernicus dates for the current selection and saved Copernicus profile
  locations when OAuth credentials are configured. Jobs do not overlap.
- **Missed runs:** a missed daily run is caught up once at the next start (also a
  silent Windows autostart), even after several days offline. Successful slots are recorded in
  `content/catalogue_refresh_schedule.json`; the first start without this record
  also refreshes. Failed or interrupted jobs are not marked complete and retry
  after five minutes, while cached data remains available. A skipped
  daylight-saving hour catches up; a repeated hour runs once.
- **Active storms:** NOAA's storm list changes within hours. Between the daily
  refreshes, MarbleScape checks only NOAA's storm page when the GOES area list is
  shown and the last check is over an hour old (after a failed check, it waits ten
  minutes). New storms, renamed storms and ended storms appear without a full refresh.
- **Incomplete refreshes:** when single NOAA pages cannot be read, the refresh still
  keeps everything it read, such as new storms; only the unreadable areas keep their
  cached entries, and the note below **Refresh catalogue** names them.
- **Conditional requests:** catalogue pages with an ETag or Last-Modified value
  are requested conditionally, and unchanged responses are reused from
  `content/catalogue_http.json`. Sources without these validators require a full
  response.

The latest image itself is checked separately on each image update.

## Image tab

From top to bottom: the **Profile** header, the status of the picture on screen
(source, acquisition time and age), **Source**, **Rendering**, **Recommendation**
(Copernicus),
[**Find location**](#find-location) (only for sources with latitude and longitude),
**Imagery updates** and **Catalogue refresh**. The Copernicus OAuth client and credits are on the
**Access** tab.

**Imagery updates** > **Check for and download newer images** is saved in each image
profile: the **Image** tab's choice applies to the loaded settings, the profile
table's **Updates** column switches it per profile, and **Check for new image** skips
profiles where it is off.

### Image sources

Choose **EUMETSAT**, **NOAA GOES**, **Solar (SUVI)**, **Himawari**, **CIRA SLIDER**,
**NASA Worldview** or **Copernicus Browser**. Each source keeps its own selection
when you switch sources. Source details are in the
[Image source guide](IMAGE_SOURCES.md).

- **NOAA GOES:** the **Satellite** dropdown selects **GOES-East** or **GOES-West**; each has
  its own area, product and resolution.
- **NOAA, Himawari and CIRA SLIDER:** dependent dropdowns for category/satellite,
  area/sector, product/layer and source resolution. Only choices offered for the
  selected area and product are listed.
- **Himawari (NICT):** **Plot shorelines** in **Rendering** draws the coastlines in a
  color of your choice; for the full disk, **Center on coordinates** with
  **Latitude** and **Longitude** puts a place in the middle; the **Active storms**
  category follows a storm. Details: [Himawari imagery](IMAGE_SOURCES.md#himawari-imagery).
- **Solar:** the available SUVI wavelength products.
- **NASA Worldview:** **Layer category**, **Imagery layer**, **Latest available** or a fixed date, and
  **Render resolution**. The default is the latest VIIRS NOAA-20 Corrected Reflectance
  True Color layer. Its **Render resolution** controls the global GIBS WMS image before
  fit/crop, zoom, background and **Monitor output** are applied.

Copernicus controls such as **Gap fill**, **Maximum cloud cover**, **No-data color**, **Labels**
and **Country borders** are described in
[Copernicus Browser imagery](IMAGE_SOURCES.md#copernicus-browser-imagery).

Still-image sources default to a GeoColor/GeoColour visualization where the
provider offers one, otherwise to the closest natural or true-color product.
Solar stays on its wavelength product, because GeoColor does not apply to the Sun.

### Find location

**Find location** above **Imagery updates** looks up a place and fills the source's
**Latitude** and **Longitude**. It is shown for **Copernicus Browser**, for an
EUMETSAT [**Custom area**](#custom-area) on the **Geographic** projection and for the
Himawari NICT full disk (**Transfer** also switches **Center on coordinates** on).

- **Search field** (grey hint "type..."): type a
  place, address or landmark and press **Search** or **Enter**. Up to 40 places from
  OpenStreetMap are listed with their coordinates. **Clear** empties the field and
  the list. The grey hint disappears when you click into the field or it has content.
- **Click** a place to copy its coordinates (latitude, longitude) to the
  **Coordinates** field below the list, where you can still edit them. **Transfer** fills **Latitude** and **Longitude**; a
  **double-click** on a place does both at once. Like every change in Settings,
  **Save** or **OK** keeps them.
- **Preview** (Copernicus) opens the Copernicus Browser in your web browser at the
  place in the **Coordinates** field, else at the saved **Latitude** and **Longitude**, with
  the current **Map zoom**. Choose product and date there: the Browser's links for
  them need a parameter it encrypts itself, so MarbleScape cannot pass them. The
  EUMETSAT viewer cannot be opened at a place, so **Preview** is grey for a **Custom area**.
- Coordinates copied from any map can be pasted into the **Coordinates** field, or
  into the search field, which recognizes coordinates without a search.
- Coordinates are accepted as *53.55, 9.99*, *53.55 9.99* or *53.55° N, 9.99° E*;
  South and West are negative. Copernicus maps reach about 85° North and South.

The search sends only the search text, and only when you search; see
[Privacy and network](PRIVACY_AND_NETWORK.md#find-location). Results are not saved.

### Recommendation (Copernicus)

**Recommendation**, a small section between **Rendering** and **Find location** shown for
Copernicus, holds **Use auto recommendation** and **Compare variants...**.

**Use auto recommendation** (**No** by default) lets each image check choose the
settings: with **Yes**, MarbleScape searches the catalogue for the view at every
check and renders with the recommendation of the chosen **Priority**: **Fewest
clouds**, **Data coverage** or **Newest** for regular layers, **Newest** or **Data coverage** for mosaics,
as **Compare variants...** would recommend them (**Newest** of regular layers is the rule's own).

- **Newest** (orange) is for watching events such as an eruption, a fire or the first snow,
  where every acquisition counts: it takes every cloud estimate (100%), so the newest
  acquisition over the middle of the view is always on top, however cloudy or small. The
  shortest **Fill gaps** (none, 7, 14, 30 or 60 days) that covers at least 90% of the view
  fills the rest from earlier acquisitions; without one the most covered variant is taken.
  The picture can be cloudy or a patchwork of days. For mosaics **Newest** is the newest
  published period, as **Latest available**; their gaps take the **No-data color**.

- **Date / time**, **Gap fill**, **Maximum lookback** and **Maximum cloud cover** are then greyed out:
  they keep their saved values and render again when you choose **No**. **Yes** sets the
  date to **Latest available**; place and map zoom never change.
- When the chosen priority has no recommendation (for example no variant covers
  90% of the view), the other one is taken; without either, or when the
  catalogue does not answer, the saved settings render.
- Regular layers decide from the catalogue alone (no processing units; about six
  catalogue requests per check). **Always use precise check** (below the
  priority, regular layers only) measures them at every check instead, about 0.6
  processing units and up to 26 requests each, and opens **Compare variants...** with
  **Precise check** on. A mosaic's **Data coverage** measures each period once with a small
  mask; **Newest** is the same as **Latest available**.
- Below the priority, "Last choice" shows what the rule took for this view in
  this session. The picture's PNG metadata records it as `auto_choice`. The
  rule, its priority, the precise check and the choice of the profile's newest
  picture also show in the profile table (columns **Auto recommendation**,
  **Auto priority**, **Precise check** and **Auto choice**, hidden in saved layouts;
  switch them on under **Columns**), in **Selected profile details** and in the
  **Image** header.
- Elevation models have no dates; the choice is greyed out there.

**Compare variants...** compares cloud limits and Gap fill for the place and map zoom before
any picture is rendered, using only the Copernicus catalogue (no processing units):
for every satellite tile over the view its date, its own cloud estimate and its
footprint.

- **Variants:** **Maximum cloud cover** 10, 20, 30, 50 and 100% with **Single latest
  acquisition** and **Fill gaps** over 7, 14, 30 and 60 days, replayed the way the
  picture is rendered. The first row shows your current settings in grey. Click a
  column heading to sort by it (again: descending); the current row stays on top,
  and the sorting ends when the window closes.
- **Two recommendations:** **Fewest clouds** has the lowest cloud estimate among
  the variants covering at least 90% of the view; **Data coverage** covers at
  least 98% from as few and as new acquisitions as possible. Each has **Apply**.
  **Fewest clouds** takes the variants with at most 10% clouds today and picks the most
  reliable one over the last 90 days. A box without a recommendation says why.
  **Fewest clouds** is marked blue and **Data coverage** green: the box heading, and a
  dot of that color before the recommended row in the table.
- **Newest** (regular layers), a section across the window above the two boxes,
  names the variant the **Newest** priority would take; its row in the table has an
  orange dot. To see its picture, select that row and press **Load preview**, or
  double-click it.
- **Dots** in a narrow first column mark the rows the priorities chose, one dot each
  (blue, green, orange). A row chosen by two or three priorities shows two or three
  dots; the rows themselves keep the normal text color.
- **Last 90 days:** the same check for each of the last 90 days: on how many of
  them a setting gave full coverage. The satellites' paths repeat, the weather
  does not, so this shows how reliable a setting is.
- **Place and zoom:** the window opens with the **Image** tab's place and zoom and
  asks nothing until you press **Check**. **Latitude**, **Longitude** and **Map zoom** can be
  changed in the window; the results then turn grey until **Check**. Beside them, **Get
  location from:** has two buttons: **Find** takes the **Coordinates** field of
  **Find location**, and a **Transfer** there fills the open window too; **Source** goes
  back to **Latitude** and **Longitude** in the **Source** section of the **Image** tab, including changes not yet
  saved. A place picked from the **Find location** list is named below
  the coordinates while they are still that place's.
- **Precise check** (off when the window opens; on for mosaics) measures today's coverage and
  clouds in the view itself instead of estimating them from the tiles: one small
  mask (160 x 90) per distinct set of tiles, mosaicked like the wallpaper, so
  usually fewer than the 26 rows. Clouds come from Sentinel-2's scene
  classification (L2A) or cloud mask (L1C) and Landsat's quality band; other
  collections, such as radar, give the coverage only. The column then reads
  **Clouds in view**, the boxes say "measured", and the recommendations use these
  values. The last 90 days stay estimates. The check box shows the estimated
  processing units per mask for the place in the fields.
- **Load preview** renders the view with a box's or the selected row's settings
  the way the wallpaper would look, small (384 x 216), with your tones, no-data
  fill, borders and labels. Nothing loads by itself; the button shows the
  estimated processing units, about 0.4 for most views (measured). Each
  collection has a coarsest pixel size (Sentinel-2 L1C 200 m, L2A 1500 m), so a
  wide view renders larger and costs more. Loaded
  previews are kept while the window is open; a row whose picture is loaded shows
  it at once with **Show preview**, in a small window of its own. A double-click
  on a row does the same as its button.
- **Apply** takes the cloud limit, Gap fill, place and zoom into the **Image** tab as
  a draft; **Save** or **OK** keeps them.
- While the catalogue answers, the window shows "Fetching data..." with a progress bar.
- **Mosaics** (quarterly, monthly or annual) compare their newest published
  periods (8 quarters, 12 months or 6 years) instead of cloud limits and Gap fill.
  **Newest** is the newest published period; **Data coverage** the newest one
  covering at least 98% of the view. The catalogue does not tell where a period
  holds data, so its coverage needs **Precise check** (one mask per period).
  **Apply** takes the newest period as **Latest available** and an older quarter or
  month as **Relative to now** with its number of periods back, so the picture moves
  on with time; an older year is taken as that year.
- **Limits:** without **Precise check**, clouds are the tiles' own estimates, not the
  clouds over your view, and footprints also count empty swath edges, so the real
  coverage can be lower.

### Source resolution

The NOAA, Himawari or CIRA SLIDER source resolution controls the downloaded image.
MarbleScape lists every safely renderable resolution the provider advertises,
including very large images. A listed resolution confirms that the variant exists,
but large transfers take longer and can be interrupted by the server, a timeout or
a connection reset. If this happens repeatedly, choose a smaller resolution.

**Automatic (recommended)** is the default source/render resolution:

- With two or more monitors it uses the largest width and height needed by the
  monitors receiving a wallpaper, including larger per-monitor output sizes, so
  mixed landscape and portrait setups are covered. A monitor set to **Do not
  update** does not count. With one monitor it uses the configured output size.
- It selects the smallest advertised source that satisfies this target, fit/crop
  mode and zoom without enlarging visible source pixels; otherwise the next
  sufficient size, or the largest one when none is large enough.

Manual sizes and **Largest available** remain selectable. **Automatic** reduces
bandwidth, memory use and provider load while keeping the detail the wallpaper
can display. For CIRA SLIDER sectors that SLIDER pads to a square (such as GOES
CONUS), sizes and **Automatic** use the visible image without the black padding; see
[CIRA SLIDER imagery](IMAGE_SOURCES.md#cira-slider-imagery).

### EUMETSAT view and presets

EUMETSAT projection, fit mode, zoom, preset, render quality and TrueColor night
controls apply only to EUMETSAT. They appear in **Image** > **Source** directly below
**Product / layer** (render quality in **Image** > **Rendering**) and are saved with **Apply Image** or **OK**. A preset fills
its satellite layer, projection, fit mode and zoom; individual values can then be
adjusted.

A new configuration starts with **All data themes**, **MTG - 0 Degree** > **RGB
Composites** > **GeoColour RGB**, projection **GEOS: MSG RSS** (the disk seen from
9.5° East, which centres Europe and Africa; a thin sliver at the eastern limb has
no MTG data), fit mode **fit**, zoom 1, preset **Full Earth** and **Black
TrueColor night side** switched on (it affects only the True Colour layer).
**Full Earth** returns to this projection. Saved selections are kept. Details:
[EUMETSAT catalogue](#eumetsat-catalogue-themes-missions-and-layers),
[projections](#eumetsat-projections) and [view presets](#view-presets).

### Zoom

**Zoom** is a dropdown for EUMETSAT, NOAA, Solar, Himawari, CIRA SLIDER and NASA
Worldview (Copernicus has its own **Map zoom**):

- **EUMETSAT presets and the other sources:** 0.5, 0.75, 0.9, **1 (Default)**,
  1.1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8 and 10. 1 shows the preset or the picture as
  it is; higher values enlarge it.
- **EUMETSAT Custom area:** 1 to 50, see [**Custom area**](#custom-area).
- A saved zoom between the steps, for example from the settings file, stays as its
  own entry such as **1.3 (saved)** until you choose another step. A zoom the
  source cannot use (NOAA, Solar, Himawari, CIRA SLIDER and NASA Worldview accept
  0.05-20, a Custom area at most 1000) shows **1 (Default)** instead.
- NOAA, Solar, Himawari, CIRA SLIDER and NASA Worldview download a finished picture
  and enlarge its centre (NASA Worldview: the world map around 0° / 0°). Two lines
  below **Zoom** show the result for the chosen zoom first, then the facts behind it,
  for example "Zoom 2: sharp (up to zoom 2.8)" and "Largest source 5000×3000 on
  1920×1080". Above that limit the first line turns orange and says by how much the
  picture is enlarged, for example "Zoom 4: enlarged 1.4× (sharp up to zoom 2.8)".
  NOAA decodes large JPEG pictures at about 4000 pixels or more per side, which the
  lines take into account. A Himawari storm view (about 3000 km across at zoom 1) is
  enlarged on top of **Zoom**, and the lines count that: on 3840×2160 even the
  11000×11000 source is enlarged from about zoom 0.8 ("Zoom 1.5: enlarged 1.9×").
  Clouds stay smooth when enlarged; coasts and small islands look softer.

### Custom area

The preset **Custom area** shows your own region on the **Geographic** map, framed
like a Copernicus location.

- **Latitude** and **Longitude** (below **Preset**) set the centre in decimal
  degrees, for example *47.5*; a decimal comma such as *47,5* also works. South
  latitudes and West longitudes are negative, for example Iceland at about *-19*.
  [**Find location**](#find-location) fills them from a place name.
- **Zoom** sets the size: 1 spans the whole world width, 2 half of it, **5 (about
  Europe)** and **25 (about Germany)**. The dropdown offers 1, 1.5, 2, 3, 4, 5, 6, 8,
  10, 12, 15, 20, 25, 30, 40 and 50; the height follows the output aspect ratio.
  EUMETSAT pictures have about 1-2 km per pixel, so above about zoom 20 (an area
  about 2,000 km wide) they get blurry. A saved zoom up to 1000 is kept.
- The satellites see Europe, Africa, the Middle East, the Atlantic and the Indian
  Ocean. The Americas, East Asia and the poles lie outside their view and show only
  the basemap; far north (Scandinavia) is seen at a flat angle and looks softer.
- A line below the fields shows the resulting size in degrees and kilometres while
  you type, and notes the satellites' view and blur above zoom 20. Near the poles or
  the 180th meridian the area is moved just far enough to stay on the world map, and
  a zoom too small for the output ratio is limited to the full map height; the line
  says so.

Selecting **Custom area** takes the centre and size of the previous preset (from **Full
Earth** it starts with the whole world at zoom 1) and switches to the **Geographic**
projection and **crop**. The settings file stores the area as `bbox` with zoom 1,
so profiles, exports and PNG metadata keep it exactly; Settings shows it again as
centre and zoom, and **Save** keeps it exactly while those fields stay unchanged.
Another preset hides the fields; the saved area stays for the next time. A custom
area entered in the settings file for another projection is kept unchanged, and
**Zoom** then keeps its usual meaning. With **fit**, Save reports when a monitor with
another ratio would show map beyond the edge of the world map.

### Imagery updates

**Imagery updates** can disable regular provider checks and downloads. MarbleScape
then downloads the selected latest image once only when no verified local image
exists for those exact image settings and output dimensions; later runs reuse it
without contacting the provider. The setting is saved in each image profile.
**Force loading new image** bypasses this mode for one download.

**Check new image**, left of it, runs the regular check now instead of at
**Next check**: a picture loads only when the provider lists a newer one. Both
buttons are available while MarbleScape is standing by.

## Image profiles and rotation

A profile is a named snapshot of the **Image** tab. Profile-list changes are saved
immediately after any confirmation (see [Saving](#saving-and-the-image-header)).

### Creating and applying profiles

**Create Profile from Image** saves the current Image settings as a new profile.
A window asks for its name and suggests the one the Image tab shows (for
example *Bahamas* or *Bahamas (modified)*; empty for the Latest snapshot); keep
it or change it. The buttons **Create Profile from Image**, **Apply**, **Load**,
**Update**, **Rename**, **Delete**, **Import profile** and **Export profile** sit
below the **Rotation status**, above **Selected profile details**. **Apply**,
**Load**, **Update** and **Rename** do the same as in the right-click menu.

- **Double-click** a profile, or right-click > **Apply**, to save and activate it
  immediately and request its image.
- **Load** (in the menu or as a button) fills the Image form; **Apply Image** then saves those
  Image-tab choices.

### Profile actions

Right-click a profile for, from top to bottom:

- the frequent actions **Apply**, **Load**, **Update**, **Check for new
  image** and **Force loading new image**;
- **Edit:** **Rename**, **Duplicate**, **Delete** (**Del**); **Move up**
  (**Ctrl+Up**), **Move down** (**Ctrl+Down**);
- **Copy cell** and **Copy row** (**Ctrl+C**);
- **Toggle updates**, **Toggle history** and **Toggle rotation**, each checked while it is on
  for every selected row;
- **Open profile history folder** (it also creates the folder);
- **Import / Export:** **Import profile**, **Export profile**, shared with the buttons;
  **Columns:** show or hide columns, **Reset sorting**, **Reset columns**.

A submenu is grey while none of its actions is available. Right-click on a selected
row keeps a multiple selection; an unselected row becomes the sole selection.
**Info** > **Keyboard shortcuts** lists every key and mouse shortcut.

- **Single-profile actions** (**Apply**, **Load**, **Update**, **Rename**, **Duplicate**)
  are disabled for multiple selections; **Move up**/**Move down** moves all selected rows.
- **Update** (with confirmation) saves the current settings into the profile.
- **Rename** opens a wide name-entry dialog, asks before overwriting the old name,
  saves at once and renames the profile's History folder.
- **Duplicate** inserts an independent copy below the original with a new UUID and
  the suffix " (Copy)", then " (Copy 1)", " (Copy 2)" and so on.
- **Delete** works for one or several rows (button, context menu or **Del** key).
  The confirmation names the number of profiles and defaults to **No**. If all
  profiles are deleted, rotation is disabled as well. When the profiles have a
  History folder with content, the confirmation adds **Also delete History images
  (N images, size)**, unchecked by default; a note names files MarbleScape did not
  create. Checked, it deletes the whole History folders, including such files.

### Force loading new image

Works on one or several profiles and never changes the active profile.

- A selected profile the wallpaper currently shows is downloaded anew right away
  and updates the wallpaper.
- Every other selected profile is downloaded in the background, one after another,
  into its own profile cache; the wallpaper does not change. When the profile comes
  up later (rotation or **Apply**), its new picture is ready at once.
- Waiting profiles show ⋯ "QUEUE" in the **Status** column, the loading one its
  progress. A due rotation step or a forced update of the shown picture goes first.
- A picture you apply (**Apply Image** on the **Image** tab, or **Apply** for a profile
  or the Latest snapshot) also goes first: a background download already running
  still finishes, then the applied picture loads, then the rest of the queue.
- A failed profile is logged and skipped; **Cancel download** stops the running
  download and empties the queue.
- As with rotation, the replaced picture moves to the profile's History when its
  History is on.

The Latest snapshot row works the same once it has an image: shown, it reloads
right away; otherwise its settings are downloaded in the background into its own
cache slot as an image without a profile, the replaced one going to `_no profile`
when **History (no profile)** is on. Applying the row later shows that picture at
once.

### Check for new image

Works on one or several profiles (**Ctrl+A** selects all) and never changes the
active profile. Unlike **Force loading new image**, it downloads only what is new:

- Each selected profile's provider is asked for its latest picture; this reads
  only the provider's listing (for Copernicus the catalogue search, which uses no
  processing units).
- A picture newer than the one in the profile cache is downloaded into the cache,
  as with **Force loading new image**. An identical or older listed picture downloads nothing;
  a newer cached picture is never replaced by an older one.
- Profiles with **Imagery updates** off are skipped; **Force loading new image** still works for
  them.
- The Latest snapshot row can be checked too, with its own settings, as described
  for **Force loading new image**.
- A selected profile the wallpaper currently shows gets its regular check at once
  and updates the wallpaper only when a newer picture exists.
- The **Status** column shows ⋯ "QUEUE", then ↻ "CHECK" and, for a newer
  picture, the download progress. When the last profile is checked, the line
  below the profile buttons shows the result, for example "Checked 3 profile(s):
  1 new picture(s), 2 up to date.", including skipped and failed profiles.
- **Cancel download** stops the check as well. **Force loading new image** for a profile that is
  waiting for its check replaces the check with a forced download.

### Arranging the table

- **Order:** the table order is the rotation order. Drag a row up or down: after a
  few pixels a colored line shows where it will land; a plain click only selects.
  Several selected rows move as a block. Near the top or bottom edge the table
  scrolls along; **Esc** cancels. **Ctrl+Up**/**Ctrl+Down** move selected rows one
  step. The pinned Latest snapshot row stays first.
- **Selection:** **Ctrl+click** for single rows, **Shift+click** for a range,
  **Ctrl+A** for all rows while the table has focus.
- **Filter** (right of the table title) shows only profiles whose name contains
  the typed text, ignoring case; **Clear** empties it. The filter is not saved.
  Hidden rows leave the selection, and moving visible rows leaves hidden rows in
  their places.
- **Type to find:** type a profile-name prefix inside the table to select and
  reveal its row. Characters less than one second apart extend the prefix; after a
  pause a new search starts. Repeating a single letter cycles matching names when
  the repeated prefix has no match. Search always uses the profile name, even when
  that column is hidden or moved, and never applies, edits or copies a profile.
  **Esc** or leaving the table resets the prefix.
- **Copy:** right-click a value to copy that cell or its row; **Ctrl+C** copies the
  selected row as tab-separated text.
- **Table height:** drag the grip below the table (vertical double arrow) down for
  more rows or up for fewer, from 5 to 40 rows; a double-click restores 7.

### Sorting

Click a heading to sort ascending (**▲**), again for descending (**▼**).

- Text uses case-insensitive natural order (*Profile 2* before *Profile 10*);
  coordinates, percentages and zoom sort numerically.
- Lookback durations compare in hours, period offsets in months, resolved
  dates/times chronologically.
- Resolution sizes compare by pixel count, then width/height; unresolved automatic
  sizes and missing values remain last in both directions.
- Cache sorting groups uncached/cached entries, then orders cached ones by save time.
- Equal values keep the rotation order; live cache/time updates keep the sort.

Sorting changes only the view, not the rotation order or profile IDs. **Columns** >
**Reset sorting** restores the rotation view. Moving rows in a
sorted view takes the visible order as the new rotation order and clears sorting.

### Columns

Right-click a column heading, or use **Columns** above the table, to show or hide
any column, including **Profile name** and **Profile ID**; **Columns** stays available
even with every column hidden. The menu lists the columns in areas (profile, source,
time, resolution, Copernicus image, tones, auto recommendation, picture) and opens
again after each click, so several columns can be switched in a row. Drag a heading (not its separator) sideways to
move a column, with a vertical line, edge scrolling and **Esc** as for rows. Drag a
heading separator to resize; widths are not stretched back automatically.
Scroll horizontally to see the rightmost columns.

A new table shows 15 columns: **Rotation**, **Profile name**, the Status symbol,
**Status**, **History**, **Updates**, **Source**, **Satellite / mission**, **Product**,
**Layer**, **Time selection**, **Time**, **Area / location**, **Data coverage %** and
**Cache status**;
the others are hidden until switched on under **Columns**. **Columns** > **Reset
columns** returns to these columns in the default order and widths,
after a confirmation; sorting stays (**Reset sorting** clears it).

Default order: **Profile name**, Status symbol, **Status**, **History**, **Updates**, **Source**,
**Satellite / mission**, **Product**, **Layer**, **Time selection**, **Time**, **Period selection**, **Periods back**, **Resolved
period**, **Area / location**, **Lat**, **Long**, **Gap fill**, **Max.
cloud cover**, **Brightness correction**, **Zoom**, **Maximum lookback**, **Resolution
selection**, **Resolution**, **Labels**, **Country borders**, **Cache status**, **Last download**, **Profile
ID**, **Short ID**, **No-data color**, **Image size selection**, **Contrast correction**, **Auto
brightness**, **Auto contrast**, **Data coverage %**, **Auto recommendation**, **Auto
priority**, **Precise check** and **Auto choice**. The fixed leftmost column
is **Rotation**.

| Column | Shows |
|---|---|
| **Rotation** | Checkbox: is the profile part of the rotation. Clicking it in a selected row toggles all selected normal profiles; **Toggle rotation** does the same. **Create Profile from Image** and **Duplicate** start excluded, newly imported profiles too; **Overwrite** keeps the checkbox. Local only, never exported. |
| **Status symbol** | No heading; left of **Status**, 28 pixels wide. The symbol of the **Status**: ● the row on screen, ⋯ waiting (QUEUE), ↻ checking (CHECK), ⭳ downloading, ⧉ Copernicus rendering (RENDER), ⊘ LOST, ☁ SOURCE, ↯ NETWORK, ⊖ UNAVAIL (also on the row on screen, instead of ●); blank otherwise. Named "Status symbol" in the **Columns** menu, where it can be hidden like any column. Local only. |
| **Status** | "ACTIVE" (● in the symbol column) for the row whose picture is on screen: the shown rotation profile, the applied profile, or the Latest snapshot row for a picture without a profile (also one from a profile's "(modified)" settings). It moves once the new picture is shown; "QUEUE" (⋯) for **Force loading new image** and **Check for new image**; "CHECK" (↻) while a provider is asked for a newer picture. While a profile downloads (a download without a profile shows in the Latest snapshot row), its progress only (⭳ in the symbol column): a percentage when the size is known (NOAA, EUMETSAT, JMA Himawari), finished/all parts for tiled sources (Himawari NICT, CIRA SLIDER) until every tile has started, the amount downloaded otherwise (NASA Worldview, Copernicus); "DOWNL" before any progress is known. Copernicus shows "RENDER" (⧉) while its server computes the image. The footer below shows the download in full. After a failed attempt: "LOST" when the provider no longer lists the profile's area, product or size (for example an ended storm or a category no longer offered); "SOURCE" when the provider failed (a server error, or no answer while this computer has internet); "NETWORK" when this computer has no network or internet (as Windows reports it; MarbleScape sends nothing extra to find out); "UNAVAIL" for anything else, for example a picture without image data. The row on screen shows "ACTIVE · LOST" and so on. This is only the current state: the profile is still tried at its next update or rotation step, and its next success clears it. Without network a rotation keeps its profile and waits one interval instead of switching. 130 pixels wide by default, enough for every status; it can be dragged narrower, which cuts the text. Local only. |
| **History** | ☑/☐: History on for this profile (**History & Storage** > **History (profile)**). Clicking saves the switch at once; with several rows selected it switches all of them. Only the switch is saved; other **History & Storage** edits stay drafts. The Latest snapshot row switches **History (no profile)**. Editing the switch on **History & Storage** updates the checkbox, saved there only with **Save**/**OK**. Local only. |
| **Updates** | ☑/☐: **Imagery updates** > **Check for and download newer images** of the profile. Clicking switches it and saves it in the profile at once; with several rows selected it switches all of them. For the profile shown in the **Image** header, the **Image** tab's checkbox follows (and the running settings, when the profile is applied), so it stays unmodified. The Latest snapshot row shows its value but cannot be switched. Saved in the profile and exported with it. |
| **Satellite / mission**, **Product**, **Layer** | What the profile shows, with the names of the cached catalogues (for example *GeoColor* instead of `GEOCOLOR`; without a cached catalogue the saved ID): the satellite or mission (*GOES-19*, *Sentinel-2*, *MTG - 0 Degree*; for CIRA SLIDER its satellite group, for Himawari NICT or JMA), the product, and the layer of Copernicus, EUMETSAT (its enabled layers) and NASA Worldview (its imagery layer); a dash where a source has none. The area is in **Area / location**. They replace the former **Selection** column, at its place and with its visibility. |
| **Time selection** | How the profile chooses its date: "Fixed", "Latest", "Timeless" (NASA Worldview) or, for Copernicus mosaics, "Rolling · *1 quarter back*" (also "current quarter", "*3* months back"). Sorting by it sorts these names. |
| **Time** | The date or time that choice gives: for Latest the acquisition time of the last used provider image in the display time zone, e.g. "*2026-10-06 14:20* UTC+02:00", or "not loaded yet"; for Fixed the date, e.g. "*2026-09-24*"; for Rolling the period, e.g. "*2026 Q3*" or "*2026-07*"; "-" for Timeless. Sorting by it is chronological. |
| **Period selection**, **Periods back**, **Resolved period** | Quarterly/monthly mosaics: saved mode, offset and currently resolved target. A specific period stays fixed; **Relative to now** recalculates when the calendar advances. |
| **Area / location**, **Lat**, **Long** | Copernicus: the saved **Example scene** or "Custom Lat/Long", with the coordinates in **Lat** and **Long**; an EUMETSAT **Custom area** shows its centre, a Himawari full disk with **Center on coordinates** its coordinates. Other sources: area or preset, a dash for the coordinates. |
| **Gap fill** | Copernicus selection and lookback, or enabled EUMETSAT gap filling. |
| **Max. cloud cover**, **Brightness correction** | Saved Copernicus percentages (the limit, not a measured cloud share); a dash for other sources. A layer may ignore an unsupported cloud filter or brightness adjustment. |
| **Zoom** | Only the number: Copernicus map zoom, or the saved image zoom of other sources. |
| **Maximum lookback** | Copernicus days or EUMETSAT hours (used only when the mode needs gap filling); a dash for mosaics and other sources. |
| **Resolution selection** | The profile's detail choice: Copernicus **Image resolution**, **Source resolution**/**Render resolution** of NOAA, Himawari, CIRA SLIDER and NASA Worldview, or EUMETSAT **Render quality**. The output size is a device setting, shown in the details as "Output resolution … (global)". Copernicus **Auto** can resolve to a larger PNG ([how Auto sizes Copernicus images](IMAGE_SOURCES.md#image-resolution)). |
| **Shorelines** | Himawari **Plot shorelines** as "On · #RRGGBB" or "Off"; a dash for JMA's stills and other sources. Hidden at first; it follows **Country borders** in a saved column order. |
| **Labels**, **Country borders** | Each Copernicus overlay as "On · #RRGGBB" or "Off"; a dash for other sources. Profiles saved before the overlays were separate show both with the earlier common switch and color. |
| **Cache status** | Current local cache: dimensions, size and save time, or "Not cached". The Latest snapshot row shows the cached image without a profile. Not a profile setting. |
| **Short ID** | The first 8 characters of the Profile ID (for the Latest snapshot row, of its snapshot ID), enough to tell profiles apart at a glance. Hidden at first in an existing table; switch it on under **Columns**. |
| **Last download** | Newest verified PNG generation time for that profile UUID, from the profile cache and from Latest/History (including custom folders, `_no profile` and profile folders), so it stays when an image moves to History. Custom Latest folders are not searched in subfolders. Not the acquisition date; "-" without a matching image. |
| **No-data color** | The applicable Copernicus choice (hex color, **Transparent**, **Blur** or **Edge Blur**), otherwise a dash. |
| **Resolution** | The size the profile's newest picture really had: for Copernicus the saved PNG, for the other sources the downloaded source picture (CIRA SLIDER without its padding), for EUMETSAT the rendered size. A dash before a picture records it; pictures of other sources saved before this column existed show a dash until the next one. Hidden at first in an existing table; switch it on under **Columns**. |
| **Image size selection** | **Auto** or a fixed Copernicus **Image resolution**. |
| **Contrast correction**, **Auto brightness**, **Auto contrast** | Saved settings of a Copernicus layer with a tone rule ([Tone rules](IMAGE_SOURCES.md#tone-rules)), otherwise a dash; **Brightness correction** too. **Auto brightness** and **Auto contrast** are the **auto** checkboxes beside the sliders. Manual percentages stay saved while auto is on. |
| **Data coverage %** | See [Data coverage](#data-coverage). |
| **Auto recommendation** | "Yes" or "No": **Use auto recommendation** of a Copernicus profile; a dash for other sources. See [Recommendation](#recommendation-copernicus). |
| **Auto priority** | The priority the rule follows, in its color: "Fewest clouds" (blue), "Data coverage" (green) or "Newest" (orange); a dash while the rule is off. |
| **Precise check** | "Yes" or "No": **Always use precise check**; a dash while the rule is off and for mosaics, which have no such option. |
| **Auto choice** | The settings the rule took for the profile's newest picture, e.g. *Max. cloud cover 100% · Fill gaps, 3 days*, or "Saved settings" when it found none; a dash before such a picture exists. |

**Selected profile details** below the list describe the selected entry. On the
left: the image source, its configuration, area or coordinates, product, layer,
example scene and period selection; on the right: source and global output resolution,
fit/zoom, the global wallpaper position and, for Copernicus, **Use auto
recommendation** ("Yes" or "No") with its priority (in the priority's color) and
precise check; below both: the acquisition time in UTC, the cache status, the
data coverage and the auto choice. Names come from the cached
catalogues (for example *Full Disk* instead of `full_disk`); without one, the
saved ID shows. Rows that do not apply to a
source are hidden: Copernicus shows no fit/zoom (its map zoom is the source
resolution), only mosaics show the period selection, the priority and precise
check show only while the rule is on (mosaics have no precise check), and the
data coverage and the auto choice appear once a picture records them.

### Table layout settings

Double-clicking a column border fits that column to its heading and widest
value. **Rotation**, **History** and **Updates** share one default width and can all shrink to
the same narrow minimum that shows only the checkbox.

Visibility, column order, widths, sorting and table height are saved at once
(a dragged column border when the mouse is released), globally in
`marblescape_config.toml`, not in individual profiles:

```toml
[profile_list]
columns_version = 16
visible_columns = ["name", "active", "zoom", "maximum_lookback", "output_resolution", "map_labels", "map_borders", "cache_status"]
sort_column = "zoom" # Empty string: rotation order
sort_descending = false
column_widths = { name = 240, output_resolution = 200, cache_status = 420 }
column_order = ["name", "last_download", "source", "zoom"] # Other columns follow automatically.
table_rows = 12 # Visible rows, 5 to 40; default 7.
```

- `visible_columns = []` hides all columns. Without a saved `visible_columns` the
  table shows the default columns; existing visibility preferences are kept on upgrade.
- `column_widths` maps stable column keys to integer pixel widths, including hidden
  columns. Missing entries use the defaults. Minimum 45 pixels (80 for Profile ID),
  maximum 16384; invalid keys, types or values are rejected. An explicit
  `[profile_list.column_widths]` subtable is also supported.
- These preferences travel with settings backups but not with profile exports or
  image metadata, and they do not invalidate cached imagery.
- Schema history: schema 9 adds the Rotation checkbox and Data coverage % to schema
  8's image-size and tonal columns; schema 10 adds the local **Active** column, now
  headed **Status**; schema 11 adds the local **History** column next to it. Once
  saved, hidden columns stay hidden.

### Saving and the Image header

**Create Profile from Image**, **Duplicate**, **Rename**, **Update**, **Delete**, moving, **Import profile**, **Toggle rotation** and the
rotation settings are saved immediately, after any confirmation; no **Save** is needed
and **Close** does not undo them. This is the only Settings tab that works this way:
while the tab is shown the footer reads "This tab saves automatically." beside
the buttons. Each saved change shows a
green "✓ Saved" there for a few seconds. If saving fails, an error is shown and the table returns to the
last saved state.

The Image header is a frame whose title names the profile the tab's settings
come from:

- "*Name*" for an unchanged named profile, with "(*short ID*)" right-aligned on the
  same line; the short ID is the first 8 characters of its Profile ID, as in the
  table's **Short ID** column;
- "*Name* (modified)" while the rendering settings differ.
  Device settings (output size, background color, Latest folder) never mark a
  profile as modified. Restoring the saved values removes the suffix. Editing or
  **Save** alone never overwrites the saved profile; use **Update**.
- "Latest snapshot (no profile)" for unassociated images.

The suffix is a UI status, not part of the exported or embedded profile name.

Inside the frame, "On screen: ..." describes the picture on screen: its source,
acquisition time, age and data coverage, e.g. "On screen: Copernicus Browser:
latest acquisition *2026-10-07 07:37* UTC+02:00 · Data coverage: 100.00% · 7 h
old". Every source shows the age last, also a selected date, a mosaic period
("rolling quarter 2026 Q3 (-1Q) · mosaic 2026-07-01 · 99 days old") and EUMETSAT
(its oldest layer time): in minutes up to two hours, then in hours up to two days,
then in days. The frame always keeps two lines, so the tab does not move when
another source is shown. For Copernicus a second line shows **Use auto recommendation** for that
picture: the priority in its color (Fewest clouds blue, Data coverage green,
Newest orange) and the settings it chose, e.g. "Auto Recommend: Newest ·
*Max. cloud cover 100% · Fill gaps, 7 days*", or "Auto Recommend: No". While
the rule is on, the line above leaves out the saved Gap fill, since the choice
names the one the picture used. Saved but not yet loaded settings do not show
here until their picture loads; the note below the buttons says when.

### Profile names

Profile names show exactly what they contain. Invisible characters (zero-width
spaces, joiners, direction marks, blank-looking fillers) are removed, special
spaces become normal spaces and accented letters are stored in one composed form.
This applies to typed, renamed, imported and already saved names; the log notes
cleaned saved names, and the History folder follows. Emoji sequences joined by
invisible characters fall apart into single emoji. A name that is empty after
cleaning is rejected.

Names cannot begin with `_` or `*`. JSON/PNG input files whose names begin with
`_` are rejected, because the prefix is reserved for local History bookkeeping.
Existing unknown files are preserved; cleanup never deletes them.

### What a profile stores

Profiles store everything on the **Image** tab: the source and its selections,
EUMETSAT layers and view, fit/crop, zoom, EUMETSAT render quality and whether the
profile checks for newer imagery. General and History settings remain shared.

Output size, aspect ratio, background color and the Latest folder are device
settings: never part of a profile, so switching, applying, rotating or importing a
profile never changes them. Older profiles, exports and backups still load; their
saved values for these are ignored and disappear the next time the list is saved.

The library and rotation settings are stored in `profiles.toml` in the application
root beside `marblescape.exe` (or the Python sources). Profiles are included in a
**Settings and all profiles** backup.

### Rotation

**Enable rotation** and choose a positive interval with the value/unit dropdowns used
by **Downloads & Updates** > **Image update checks** (minutes, hours, days, weeks or 30-day months; default 15
minutes). Only profiles with a checked **Rotation** box take part, in table order.
**Now showing**, right below the table, names the profile on screen in its first
line, for example "Active profile: *Bahamas*". While rotation waits, the second line
names the next profile with its time and, after a pipe, the preload result, for
example "Upcoming profile: *Fiji* at *2026-10-04 16:33:16 UTC+02:00* | already current
(*16:32:47 UTC+02:00*)"; while a step runs, that step. The last line says
"Rotation: enabled" or "Rotation: disabled". Three lines stay reserved.

- **Random shuffle** visits every checked profile once per shuffled pass before
  starting a newly shuffled pass.
- **Keep last rotation position** stores the next position and the time of the
  last switch locally under `content`. After a restart within the interval, the
  picture shown before stays (a rotation profile from its cache, a manually applied
  profile from the settings file); the next profile loads when the interval since
  the last switch has elapsed. Without it, a restart begins with the first checked
  profile.
- **Load the next picture before the switch** (on by default) checks the
  next profile one minute before its switch, or half the interval for a 1-minute
  rotation (an orange hint on the line below **Change every** says so: large
  pictures may then switch late), and loads its picture into the profile cache when the provider has a
  newer one, so the switch shows it at once. Profiles with **Imagery updates** off and
  the profile already on screen are skipped. While it runs, the **Status** column shows
  "↻ CHECK" and any download progress; **Now showing** keeps the result after
  the next profile until the switch, for example "already current (*14:35:30*)" or
  "new picture loaded". A picture that takes longer, such as a
  Copernicus rendering, finishes before the switch. Turning the option off or on
  keeps the rotation schedule.

How a rotation runs:

- The first profile loads when rotation starts. After a successful load its full
  interval elapses before the next one; normal image update checks continue in
  between.
- **Set wallpaper automatically** must be enabled to apply images to the desktop.
- Failed loads use the global **Download retries** limit with a short retry delay;
  a profile that exhausts it is skipped. A profile whose area, product or size the
  provider no longer lists (**Status** "LOST"), or whose picture has no image data,
  is skipped after one attempt. Skipping never removes a profile: the next round
  tries it again. If every profile fails, the last wallpaper stays and rotation
  waits one interval before trying the list again.
- Profile-list or interval changes restart the schedule; a still-valid saved
  position is kept when requested.
- One-shot and diagnostic commands do not run rotation.

Applying a profile or Image settings by hand (**Apply**, **Apply Image**, **OK**)
counts as a profile switch: the applied picture stays for a full interval, and when
the applied profile is in the rotation, rotation continues with the profile after
it. A rotation step still loading is cancelled. When the same save also changes the
rotation list, the rotation restarts instead.

### Latest folder and profile cache

The Latest folder always holds the one picture on screen, whether it has no
profile, was applied by hand or comes from rotation; once rotation shows a
profile, its picture is copied there with a readable name, so another program can
always take the current picture from Latest. The picture it replaces goes to the
History folder of its own profile (or `_no profile`) if History is on for it.
If the picture in Latest is removed (for example by hand), the next check puts the
picture on screen back, from the cache without a new download when it is there,
and the profile table marks its row active again.

Profile images are kept in an internal, content-addressed cache under
`content/cache`, so returning to a profile needs no new download; you never need
to open it.

- A SQLite index maps each profile ID, its image-setting signature and the current
  provider frame signature to an immutable, SHA-256-named PNG.
- Returning to an unchanged profile still checks lightweight provider metadata but
  reuses the verified PNG instead of downloading or rendering again.
- Embedded profile names and UUIDs are part of the cached result, so visually
  identical profiles with different identities can need separate files.
- Changed image settings, changed source timestamps, missing files or failed
  integrity checks invalidate an entry. Deleted profiles leave the index, and
  unreferenced PNGs are pruned.

A manually applied profile (double-click or **Apply**) publishes through Latest.
While its settings are unchanged (no "(modified)" marker), every downloaded image
of it, including **Force loading new image**, is also kept in the profile cache.
Applying it again first checks the provider's latest frame; if nothing newer
exists, the cached image is copied back to Latest without downloading (with **Imagery
updates** disabled, without contacting the provider). The replaced Latest image is
archived as after a download. Modified settings never use or fill the
profile's entry; they count as an image without a profile (see below).
Rotation and manual apply share cache entries, and applying a different profile
does not invalidate rotation's cached images.

Applying a profile while a picture downloads does not wait for that download: if
the profile cache holds a picture made with exactly the profile's image settings
and the current output size, it becomes the wallpaper at once. The running
download still finishes and its picture goes to the cache of the profile it was
made for (or the Latest snapshot), not to Latest, so returning to that profile
needs no new download. Afterwards MarbleScape checks the applied profile's
provider as usual. Without a matching cached picture the profile loads right
after the running download. A settings change during a download likewise keeps
its picture in the cache instead of discarding it.

The last image downloaded without a profile (the **Latest snapshot (no profile)**
row) also keeps one slot in the profile cache. When a profile replaces it in
Latest and you later return to the same settings without a profile, MarbleScape
checks the provider's latest frame first: if nothing newer exists and the settings and output size are
unchanged, the cached image is copied back to Latest without downloading (with
**Imagery updates** disabled, without contacting the provider). Otherwise it downloads
as usual. A new image without a profile replaces the slot; **Clear cache** also
empties it.

## Import and export profiles

Buttons and the right-click menu share the same import/export actions. Individual
profile transfers do not include rotation, startup or account settings.

### Export

**Export profile** exports the selected rows, one JSON file per profile. A
single profile opens the normal **Save As** dialog with a custom filename;
several profiles open **Select folder** and use this default name:

```text
MarbleScape_Profile_<Profile name>_<UUID>.json
```

Invalid filename characters are replaced and repeated underscores collapsed; the
original name stays inside the JSON. Each file contains its profile UUID and a
SHA-256 integrity checksum. Existing destination files offer:

- **Overwrite:** replace this file after validation.
- **Skip:** leave it untouched and continue (the equivalent of answering **No**).
- **Save as copy:** use a free filename with " (Copy)", then " (Copy 1)", etc.;
  name and UUID inside the JSON stay unchanged.
- **Overwrite all / Skip all** (multiple selections only): use that choice for the
  remaining conflicts of this batch. Non-conflicting files are still exported.
- **Cancel**, **Esc** or closing the question: abort the entire export without
  writing any files, even after earlier approved conflicts.

Safety: validation and conflict decisions finish before writing. Overwritten files
are kept until the batch completes; staged files and destinations are read back,
compared byte-for-byte, parsed and validated before success is reported, and a
failure rolls back. Destinations that change externally during preparation are
rejected.

A result dialog shows the exported/skipped counts and destination; failures show
the reason, and cancellation is reported in the status line. If all conflicts are
skipped, the result says that no files were exported.

The initial folder is `export/profiles` beside the application. Successful exports
remember the chosen folder, independently of settings backups. If it becomes
unavailable or unwritable, the default is used and the old choice is forgotten.
**Import profile** opens in the same folder.

### Import

**Import profile** accepts several JSON files and/or PNG images at once, including
older bundled JSON exports. All files are checked before any question or change.
Valid profiles are added and saved at once, and a summary explains each rejected
file or profile. If there are issues, a scrollable overview lists blocked entries
and proposed safe additions first: **Cancel** leaves the library unchanged,
**Continue** proceeds only with readable candidates.

#### Validation

- Unsupported schemas, invalid values, checksum failures, damaged PNGs and
  conflicting text/EXIF are rejected. PNG validation includes pixels, chunk
  checksums, dimensions, the IEND length/checksum, and rejects trailing data.
- Source and selection descriptions must agree with the stored profile;
  Copernicus product/layer names are matched to their catalogue IDs.
- A mosaic period must match its recorded resolved date, and a fixed selection the
  same period. Relative selections are not recalculated against today.
- A named, complete MarbleScape profile record is required; unnamed images and old
  partial records are rejected.
- The optional rendered-image digest is informational: it does not authenticate
  pixels or block a valid profile from an edited image. An integrity checksum
  detects corruption, not authenticity.
- Limits: 100 profiles in the library, 100 input files, 1 MB per JSON, 100 MiB and
  100 million pixels per PNG. Validation is local; it cannot guarantee that a
  provider still offers a saved product.

#### Repairing older profiles

Missing rendering fields are never silently defaulted. For known older Copernicus
profiles, missing time-selection fields can be completed with `date_mode =
catalogue` and inactive offsets of `0`, preserving the saved fixed date or latest
selection. Known safe defaults for update checks, background color and mosaic
brightness can also be proposed. A prompt lists the exact additions:

- **Yes:** accept the additions for this profile.
- **Yes to all:** accept safe additions for the remaining profiles of this batch.
- **Skip:** omit this profile and continue.
- **Skip all:** omit this and the remaining candidates that need repairs; complete
  profiles still import.
- **Cancel**, **Esc** or closing the prompt: cancel the import; nothing is added.

These options do not bypass corruption checks. Missing coordinates, products,
active relative offsets or an ambiguous date mode are rejected. No confirmation
carries over to the next import.

Fields added during development keep backward-compatible values when absent from older
records:

| Missing field | Value used |
|---|---|
| Mosaic No-data color | white |
| Regular-layer No-data color | map background, or black if the record used the former **Gap fill** option **Fill areas without image data with black** (now **Single latest acquisition** with a black **No-data color**) |
| Labels color | black |
| Country borders | drawn like the Labels, as before |
| Contrast, Auto brightness, Auto contrast, Image size | 100%, off, off, **Auto** |

New profiles and configurations start with both overlays off and white, with
**Blur** as the **No-data color** (profiles saved with another choice keep it), and with both **auto** checkboxes (brightness and contrast
correction) on; they only act on layers with a tone rule. Saved choices stay as they are.

#### UUIDs and name conflicts

Imports keep the UUID stored in the JSON or PNG, including EXIF-only images. The
UUID alone identifies a profile, even after renaming:

- **Same UUID** (also within the current batch) asks the conflict question:
  **Overwrite** replaces settings and name at the current table/rotation position
  and keeps the UUID (if another profile already uses the incoming name, "
  (Imported)" is appended); **Import as copy** creates an independent profile with
  a new UUID and a free " (Copy)"/" (Copy 1)" name; **Skip**, **Overwrite all**,
  **Skip all** and **Cancel** work as for export, and **Cancel** discards everything
  from that import.
- **Different UUID** is a different profile and is added without a question, even
  when its name is already used (ignoring case). It is renamed with " (Imported)",
  " (Imported 2)" and so on; the existing profile, its History folder, History
  settings and **Rotation** checkbox stay untouched. The summary lists renamed profiles.
- **No UUID** (legacy records) can only be matched by name, ignoring case, and asks
  the same question. If several local profiles have that name, overwrite is
  disabled; rename or skip instead. Such records receive a UUID when added, or keep
  the existing one when overwriting. A malformed UUID is rejected, never
  regenerated.

### What is transferred

JSON and PNG transfer keeps the selected source's portable render settings,
including relative-period mode and offset, but excludes credentials, local paths,
unrelated source selections and local state (**Rotation**, **Status**, **History**). PNG import
restores the saved selection logic, not necessarily the historical image date.

## Storage and History

### Folders

In **History & Storage**, type a path or use **Choose...**:

- **Latest image folder** > **Custom latest folder**: one folder for every profile,
  holding the picture on screen.
- **History folder** > **Custom history folder**: the single History root, shared by
  `_no profile` and every profile folder. Profiles cannot use their own root.

Empty fields use `content/latest` and `content/history` under the output root;
relative paths use the application folder. The buttons below open the folders in
Explorer (**Open history folder** opens the History root). **Save** or **OK** applies the
paths and missing folders are created when needed. Latest and History must differ
and cannot point at the profile cache. The paths are saved as
`output.latest_folder` and `history.folder` and included in settings backups. The
tray's **Open image folder** always opens the Latest folder.

### History (no profile) and History (profile)

- **History (no profile)** archives profile-free images under `_no profile` with
  its own retention. Profile-free images are shown from Latest and also kept in
  the profile image cache, in the Latest snapshot's slot.
- **History (profile)** uses one folder per profile, `<safe profile name>_<full
  UUID>`, inside the History root. Choose a profile to set its own switch and
  count/time retention; this applies to every image of that profile, from rotation
  or applied by hand. The profile dropdown is alphabetical and follows the profile
  list each time the tab is opened.
- **Maximum files** (both sections) suggests 1, 5, 10, 25, 50, 100, 250 or 500 and
  also accepts any other whole number typed in.
- **Apply to all profiles** copies the displayed profile History settings to every
  normal profile as a draft; **Save**/**OK** persists them.
- **Open profile history** opens (and creates if needed) the chosen profile's
  folder. **Clear profile history** removes its archived images, after
  confirmation.

The storage estimate adds every profile's estimate. An image of a deleted profile
is archived as profile-free. Deleting a profile keeps its History folder unless
**Also delete History images** is checked; renaming renames it. Managed PNGs at
the root of an old History folder are moved to the matching subfolder using their
embedded profile identity (unassociated images go to `_no profile`);
unrecognized files stay in place.

### Profile image cache

The profile image cache keeps the pictures of saved profiles and of the Latest
snapshot, so showing a picture again needs no new download. Each profile keeps
one picture per image settings and output size, called a variant. A newer picture
with the same settings replaces its variant. Switching back to settings used
recently, for example an earlier cloud cover or layer, shows the cached picture
at once instead of downloading it again.

**History (profile)** > **Profile image cache** shows a short note; **Info** > **Profile
image cache** explains the details:

- **Saved profiles** reports the cached pictures of saved profiles and their
  size. A picture shared by several profiles is stored and counted once.
  - **Size limit** (0.5 to 10 GB in steps of 0.5 GB, default 2 GB) caps them.
    Beyond it, the least recently used variants are removed first. Each
    profile's current picture always stays, so many profiles with large pictures
    can exceed the limit.
  - **Variants per profile** (1 to 10, default 5) caps the pictures of one
    profile; the least recently used goes first. 1 keeps only the current picture.
- **Latest snapshot (no profile)** reports the cached pictures without a profile
  and their size.
  - **Size limit** (0.5 to 10 GB in steps of 0.5 GB, default 0.5 GB) caps them
    on their own.

Pictures of a modified profile ("*Name* (modified)" in the **Image**
header) and pictures without a profile belong to no saved profile. They share
the Latest snapshot's slot, which keeps any number of variants within the Latest
snapshot size limit. So trying out settings, for example several cloud cover
values, and returning to one of them shows its picture without a new download.
Because this slot has its own limit, trying settings never pushes out the
pictures of saved profiles; its current picture always stays.

Removed variants are not archived to History; History archives a profile's
picture when a new one replaces it on screen, as before. The sliders are drafts
until **Save** or **OK** and apply right after saving; while they move, only the storage
estimate follows them. They are saved as `cache.max_size_gb`,
`cache.variants_per_profile` and `cache.latest_snapshot_size_gb` and included in
settings backups; a configuration without them uses the defaults.

**Clear cache** asks for confirmation and removes only cache images and
metadata; Latest and History images stay. If a rotated profile is active,
MarbleScape requests a fresh image afterwards.

A cache from an earlier version is converted automatically on first start: each
profile's picture becomes its first variant.

## Latest snapshot (no profile)

The protected system row **Latest snapshot (no profile)** stays at the top of the
profile table, independent of sorting and rotation. It holds the portable settings
of the last successfully stored image downloaded without an active named profile.
Failed or cancelled downloads and named-profile downloads do not replace it; an
identical image keeps its snapshot identity. Before the first such download it
shows "No image yet".

### Actions

- **Apply / Load:** reuse the settings, including dynamic latest or
  relative period selections; applying does not guarantee the same future imagery.
- **Duplicate:** create a normal profile with a new UUID and "(Copy)"/"(Copy 1)".
- **Open profile history folder:** open the `_no profile` History folder.
- **Force loading new image / Check for new image:** reload or check the row's
  picture; see [Force loading new image](#force-loading-new-image-1) and
  [Check for new image](#check-for-new-image).
- **Update, Rename, Delete and Move:** unavailable. Bulk deletion keeps the row and
  confirms how many normal profiles will be deleted.
- **Export:** create a normal, validated profile JSON named "Latest snapshot
  (Imported)", using a stable export UUID for this snapshot.
- **Import its JSON or PNG:** create a normal editable profile, never overwriting
  the row. Repeated imports of the same snapshot use the UUID conflict dialog; a
  different snapshot becomes "Latest snapshot (Imported 2)" and so on.
- **Settings backups (both scopes):** include the snapshot once in
  `[latest_snapshot].record_json`; a full backup adds the profile library. A
  confirmed restore may replace the snapshot. Images are not backed up.

### Identity and History

Each distinct snapshot has a new `snapshot_id` (shown in the **Profile ID** column) and a
normal export `profile_id`. The protected row uses a reserved internal type and
identifier, not its display name; a normal profile with the same name has no
special privileges. Existing PNGs are not rewritten.

The row never gets a profile History folder: its archived images always use
`_no profile`. Startup moves managed images from an obsolete snapshot folder there
without deleting unrecognized files. A duplicated or imported snapshot is a normal
profile with its own UUID and History folder.

## Data coverage

**Data coverage** is the share of pixels with valid satellite data. It is appended
to the Copernicus source-status line (with `%`) and shown in the **Data coverage
%** column and **Selected profile details** as a number such as *98.42*.

- Supported Copernicus layers use binary satellite data masks, measured before
  background maps and labels.
- Black water or shadow pixels are valid if the mask says so. Clouds are valid
  image data too, so a completely overcast image still reports 100%; see
  [a completely white or grey Copernicus image](IMAGERY_ARTIFACTS.md#a-completely-white-or-grey-copernicus-image).
- Sources without a reliable mask show "Not available"; a profile without a
  matching local image shows "-". Restoring settings alone cannot restore a
  measured result.
- Details and **Last download** can use the matching verified profile-cache image;
  the value is also read from cached and archived History images.

Field definitions: [PNG metadata](PNG_METADATA.md#data-coverage).

## Settings backups

### Backup actions

- **Export settings only / Import settings only:** the saved configuration and
  Windows startup setting, without changing the profile library or rotation.
  Settings-only import can also read the settings part of a full backup.
- **Export settings and all profiles / Import settings and all profiles:** also
  `profiles.toml`, all profile UUIDs and rotation. Import replaces the entire
  library after explicit confirmation, rather than appending profiles.

Both imports apply immediately after confirmation, unlike individual profile
imports.

### Export location

Exports initially open `export/settings` beside the application, with these
default names (local export time at the end):

```text
marblescape-settings-YYYY-MM-DD_HHMMSS.json
marblescape-settings-profiles-YYYY-MM-DD_HHMMSS.json
```

A successful export remembers its directory for both scopes, independently of
profile exports, in `export/export_locations.json` (not in the backup). A missing
or unwritable directory resets to the default until a new destination is used;
cancelled or failed exports change nothing. **Open settings folder** and **Open
profiles folder** open the remembered destinations, and **Import settings**
(also in the startup recovery dialog) starts in the settings folder. Earlier `backups/settings` and
`backups/profiles` defaults migrate to `export`; custom destinations are kept, and
existing backup files are not moved.

### Format and validation

Backups contain TOML documents, readable snapshots and SHA-256 checksums; the scope
is recorded in format version 3, and full version-2 backups are still readable. A
settings-only backup cannot be imported with the full-restore action.

- **Export** validates before writing and rereads the temporary file and the
  destination (contents, checksums, snapshots, restorability) before reporting
  success. Existing backups are kept until verification succeeds.
- **Import** checks format, checksums, snapshots and profile settings before any
  change. Combined settings/profile writes roll back if the configuration write fails.
- **Older full backups:** export completes known legacy period fields in the backup
  copy without changing `profiles.toml`. Importing such a backup asks to confirm
  these safe additions after all profiles pass preflight. **Cancel** cancels the
  restore; **Skip** is not offered, because profile IDs and rotation references
  must stay together. Other invalid fields or integrity errors block the restore.

If the settings are too damaged for the tray to start, the startup error window
offers **Restore last working settings** and **Restore exported backup...** (see
[Configuration](#configuration)).

### Privacy and other computers

Both backup types may contain private settings; full backups also contain
profile names, locations and paths. Keep them private. Absolute paths may need
adjustment on another computer. Copernicus Client secrets are protected for the
Windows user that saved them; re-enter the secret on another computer or account.

## Image metadata

The complete field reference, PNG/EXIF structure, ExifTool setup and inspection
commands are in [PNG metadata and ExifTool](PNG_METADATA.md).

New PNG images carry matching `MarbleScape` UTF-8 PNG text and EXIF
ImageDescription records with:

- source, mission/product/layer where applicable, image dimensions, source time or
  mosaic period (never presented as a single acquisition time), Copernicus
  location and zoom, generation time;
- the profile name and UUID when a profile produced the image; images without one
  get no fabricated name or UUID. Manually applied profiles are identified while
  their settings still match. Unicode names round-trip losslessly through EXIF;
- for quarterly/monthly mosaics `date_mode`, `quarter_offset` or `month_offset`,
  `resolved_date` and `mosaic_period`;
- a sanitized `profile_settings` snapshot for profile import, without output size,
  background color or Latest folder (device settings of whoever imports it). The
  `width` and `height` fields still record the actual dimensions.

The record excludes OAuth credentials, tokens, local paths and the full
configuration. Profile names and coordinates may be private; review a PNG before
sharing it. Per-monitor wallpaper PNGs keep the source profile metadata; their
canvas size is distinguished from `profile_image_size`, and importing one restores
the source profile, not the desktop layout. Identical forced refreshes keep the
original generation time, so they create no duplicate History entries. Existing
images are not rewritten.

### Image filenames in latest, history and cache

New images in `latest` get a readable name built from their metadata:

```text
MarbleScape_<Created-UTC>_<Profile-or-location>_<Image-hash>.png
MarbleScape_2026-09-26T210000Z_Whitsundays_a1b2c3d4e5f6.png
```

- **Time:** the original generation time in UTC, not the acquisition time, so
  sorting by name sorts by creation; `created-unknown` when unknown.
- **Name part:** the profile name, or without a profile an example scene, rounded map
  centre, area or selection. Unsafe characters are replaced; at most 32 characters.
- **Hash:** the first 12 hexadecimal characters of the complete PNG's SHA-256, not
  the profile UUID; identical tokens mean identical images.

Everything else (source, product, acquisition time or mosaic period, size, full
names, IDs, exact coordinates, render settings) is in the embedded record.
MarbleScape reads what it needs from that record and uses only the `MarbleScape_`
prefix of the filename to recognize its images.

- **History** keeps the published name and metadata; an existing name gets `_1`,
  `_2`, etc. An image whose exact content is already in that History folder is not
  archived again, for example when switching between cached profiles.
- **Profile cache** keeps full SHA-256 filenames and its index; History copies of
  cached images get readable names. `latest` holds exactly one image, also during
  rotation.
- **Older files** are not renamed or rewritten. Earlier longer names, legacy
  timestamp-only names and the fallback for images without metadata remain valid.
  Retention uses file modification times, not filename dates.

## Configuration

### Files

- `marblescape_config.toml`: general, source, image, download, History and storage
  settings.
- `profiles.toml`: named image profiles and rotation.

Both are local and ignored by the repository, so personal preferences are not
published accidentally. The tracked `marblescape_config.example.toml` holds
neutral defaults.

Key tables:

- `source.provider`: `eumetsat`, `goes_east`, `goes_west`, `solar`, `himawari`,
  `slider`, `worldview` or `copernicus`.
- `sources.goes_east`, `sources.goes_west`, `sources.solar`, `sources.himawari`,
  `sources.slider`, `sources.worldview`: each source's `area`, `product` and
  `resolution`. For Worldview, `area` is the GIBS layer ID and `product` is
  `latest` or a fixed date offered by that layer.
- `sources.copernicus`: catalogue selection, date, location, zoom, maximum cloud
  cover, brightness correction, map options; the Gap fill choice as `gap_fill_mode`.
  `[copernicus]` holds the OAuth Client ID and protected secret.
- `[download]`: speed, size, percentage, progress-bar and completed-status
  preferences.

Switching providers keeps each source's settings.

### Automatic copies and recovery

After every successful tray start, and after Settings saves load successfully,
MarbleScape copies exactly the files it loaded to
`backups/automatic/<YYYY-MM-DD_HHMMSS>/` beside the configuration (local time). A
new copy is made only when a file changed; the five most recent are kept. They
contain the same private settings and stay on this computer.

If a file cannot be loaded or fails validation at tray start (for example after a
typo while editing by hand), MarbleScape shows the error and both file paths
instead of quitting:

- **Open settings file** / **Open profiles file** open the file in its associated
  editor (Notepad if none is set). Correct the reported line and save.
- **Restore last working settings** shows the time of the newest automatic copy
  and restores it after confirmation; later changes are replaced.
- **Restore exported backup...** restores a JSON backup from
  [Settings backups](#settings-backups): settings-only replaces the settings, full
  also all profiles, with the same validation and confirmation as the Backup tab.
- **Try again** loads the settings again; the tray starts once they are valid.
  **Exit** closes MarbleScape without changes.

Before a restore replaces anything, the current files are copied to
`backups/automatic/before-restore_<time>_<id>/`, so manual edits can be recovered;
these copies are never offered as working settings. The `--once` mode reports the
error and exits.

### Advanced options and output paths

Advanced options require editing TOML: the EUMETSAT service endpoint, archive
timestamps, arbitrary WMS layer stacks/styles/opacities, custom bounding boxes,
the shared timeout and output-root paths. Restart after manual edits. Backup import
applies a complete configuration, including these options.

Relative output paths are resolved from the application directory, regardless of
the working directory:

```toml
[output]
windows_root = "."                         # Application directory
# Alternatives (choose one value for windows_root):
# windows_root = "output"                  # An output subdirectory
# windows_root = 'D:\Images\MarbleScape'     # A custom absolute path
linux_root = "."
```

For a container, set `linux_root = "/output"` and mount that directory as a volume.

### Render quality

For EUMETSAT, **Image** > **Rendering** > **Render quality factor** (saved as
`output.render_scale`) offers **Default (General)** (default for new settings and
profiles), **Auto (max useful)**, **Standard (1.0×)**, **High (1.25×)**, **Very high
(1.5×)** and **Ultra (2.0×)**. **General** > **Monitor output** > **Render quality factor**
offers the same choices except **Default (General)**. Both are dropdowns only, so no
empty or invalid factor can be entered; a factor set by hand in the settings file
shows as "Custom (1.75×)" and stays until another choice is made.

- **Default (General)** (saved as `default`) uses **General** > **Monitor output** >
  **Render quality factor**, so one setting serves every source; any other choice
  is the profile's own value and stays when General changes. Profiles of other
  sources carry `default`, as their render quality has no effect there.
- Settings saved before General had its own value keep their behaviour: without
  `display_render_scale`, General starts with the image's former value.

- The final output resolution does not change: higher settings request a larger
  intermediate WMS image and resize it with Lanczos resampling (Pillow is required
  whenever the WMS size differs from the output).
- WMS requests are capped at about 4000 pixels per axis. Larger outputs are
  rendered within that limit and enlarged, so a factor above `1.0` adds no detail
  at 5K or 8K.
- **Auto (max useful)** stays automatic and recalculates the largest useful factor
  whenever the output resolution changes.

### TrueColor day/night option

**Black TrueColor night side** applies only to **MTG TrueColor**. It shows the sunlit
part of the image and fills the unlit part of the Earth disk with black; the area
outside the disk keeps the configured background color.

### EUMETSAT catalogue, themes, missions, and layers

**Image** > **Source** uses dependent dropdowns for **Data theme**, **Satellite /
service**, **Mission**, **Product type** and **Product / layer**. Each choice
restricts the following ones to combinations published together in the official
catalogue, so a choice from another service cannot remain selected by accident.

- The public catalogue currently exposes MTG, MSG, Metop, multi-mission and
  Sentinel-3 choices; channels, visualized products and RGB composites come from
  current official product metadata. GeoColour is preferred when available.
- **Data theme** filters are those of the EUMETSAT Product Viewer: **Atmosphere**,
  **Ocean** and **Weather**; a product may appear in several. Products without a
  viewer theme (for example MTG Cloud Phase or Fog / Low Clouds) are listed only
  under **All data themes**, the default. A saved Climate or Emergency filter from
  earlier versions becomes **All data themes**; the layer is unchanged.
- Products the viewer lists but EUMETSAT has not published yet appear in a line
  below **Preset** and **Black TrueColor night side** ("Listed by EUMETSAT, not
  available yet") with their technical names. They are not selectable and disappear
  from the line once available.
- **Refresh catalogue** under **Catalogue refresh - EUMETSAT** reloads this
  metadata; **Downloads & Updates** > **Refresh all catalogues** includes EUMETSAT with NOAA,
  Himawari, CIRA SLIDER and NASA Worldview. Copernicus stays separate, because its
  catalogue depends on OAuth access and location.

#### Gap filling for LEO layers

For suitable LEO single-overpass layers, **Fill gaps with earlier imagery** places
older passes underneath the newest image, with a maximum lookback of **12 hours**
or **24 hours**. The newest image is downloaded first and only its transparent No
Data pixels are replaced; valid newest pixels always stay on top. A failed older
pass is skipped, while a failed newest image fails the update. The wallpaper can
then contain several acquisition times.

The option is unavailable for GEO imagery and for accumulated, daily, blended,
climatological or orbital-track layers. Eligible entries currently include
Metop-A/B/C ASCAT and the individual Sentinel-3A/B OLCI and SLSTR sea-surface
temperature products. Sparse fire-detection layers are excluded, because
transparent pixels do not reliably mean missing coverage there. Prefer regional
views: large global reprojections need several WMS requests and can time out even
at a small output size.

#### Layer stacks

Local composition and the Full Earth server path use the configured layer order
from bottom to top; the regional server path reverses it, so arbitrary stacks can
look different when changing render mode or preset. Friendly basemap and overlay
names are resolved by the application. Exact WMS product names come from the live
capabilities document:

```powershell
python marblescape_download.py --list-layers
python marblescape_download.py --export-layers marblescape_layers.json
```

Each `[[layers]]` entry supports `enabled`, `opacity`, `style` and an optional
`time`. With `render_mode = "auto"`, a single server request is used unless
per-layer opacity or different timestamps need local composition. The TrueColor
black-night option and EUMETSAT LEO gap filling always use local composition (a
separate Earth mask, or an independent transparent WMS response per acquisition).

Profiles of other sources carry no layers, so applying one saves `layers = []`.
Choosing EUMETSAT afterwards starts again from the default stack (Natural Earth
plus the selected WMS layer). Settings never saves EUMETSAT without at least one
enabled layer with opacity above zero; it shows an error and keeps the working
configuration, because MarbleScape could not start with it.

### EUMETSAT projections

Select a projection in **Image** > **Source** or with `view.projection`. Settings
offers every projection of the [EUMETSAT viewer configuration](https://view.eumetsat.int/assets/data/config.json):

- **Geographic** (EPSG:4326)
- **GEOS: MSG FES, MTG FD** (geostationary, centered at 0 degrees)
- **GEOS: MSG RSS** (geostationary, centered at 9.5 degrees east)
- **GEOS: MSG IODC** (geostationary, centered at 41.5 degrees east)
- **Spherical Mercator** (EPSG:3857)
- **North Polar** (EPSG:3995)
- **South Polar** (EPSG:3976, the Antarctic CRS)

Projection is not part of the catalogue dependency chain: current layers advertise
the geographic, Mercator and polar CRSs, and GeoServer generates the three
geostationary views, so EUMETSAT can reproject a valid layer into every listed
projection. Changing projection in the UI selects **Full Earth**, resets **Zoom** to
1 and uses **fit** (**crop** for **Geographic**, to stay within valid bounds); **Full
Earth** means the projection's default overview, not global coverage. The layer is
kept.

A projection changes the view and processing cost, but cannot add coverage
outside the source observations:

- Areas outside the layer's coverage may show the basemap instead.
- With MTG, polar views can show a black center with imagery only towards the
  edge, because the satellite does not observe the poles; black-night masking
  also hides the basemap there. Choose a layer that covers the area you want.

**Info** > **EUMETSAT view & presets** summarizes presets, projections, coverage and
the night-side option.

### View presets

Available presets: `full_earth`, `europe`, `mediterranean`, `central_europe` and
`custom`. Regional presets use Geographic; projected regional presets are not
included yet.

`custom` is **Custom area** in Settings (centre and zoom, saved as `bbox` with zoom
1). In the TOML file, define `bbox` in logical x/y order: Geographic uses
`[west, south, east, north]` in degrees within longitude -180 to 180 and latitude
-90 to 90; a projected CRS uses meters. WMS 1.3.0 axis order is handled
automatically.

The default zoom is `1` for every source, including EUMETSAT's named presets
and projection changes. An explicitly saved zoom, such as the former EUMETSAT
default `1.1`, is still respected.
