# Development and release builds

Planned work, release builds, tests, schema history and the invariants the
regression tests enforce.

[Back to the main README](../README.md)

## Contents

- [Planned features](#planned-features)
- [Building release archives](#building-release-archives)
- [Tests](#tests)
- [Schema history](#schema-history)
- [Invariants](#invariants)

## Planned features

- Support for additional imagery providers.
- More customization options for image composition.

These ideas are planned, but scope and release dates may change.

## Building release archives

This step is intended for maintainers, not for users of the prebuilt package.

### Build commands

On 64-bit Windows with 64-bit x86 Python and PowerShell 7, install the tested
build dependencies and run the release script:

```powershell
python -m pip install -r requirements-build.txt
pwsh -NoProfile -File .\build_release.ps1
```

To build and smoke-test only `marblescape.exe` in the project root, without
replacing existing release archives or checksums:

```powershell
pwsh -NoProfile -File .\build_release.ps1 -ExeOnly
```

### What the archives contain

The script creates separate source and Windows release-candidate archives in
`release`, plus a SHA-256 checksum file. Creating them does not clear them for
publication; complete the
[distribution review](LEGAL_AND_ATTRIBUTION.md#outstanding-distribution-review)
before publishing a new binary.

- **Source archive:** `MarbleScape-X.Y.Z-source.zip` with application modules, build scripts, tests, documentation,
  icon assets required by the build, licences and corresponding third-party
  source.
- **Windows package:** `MarbleScape-X.Y.Z-windows-x64.zip` with the executable,
  the matching application source ZIP under `source/`, and the verified upstream
  pystray source under `third_party/` (also in the source archive). See
  [Third-Party Notices](../THIRD_PARTY_NOTICES.md#rebuilding-with-a-modified-pystray)
  for rebuilding with a modified tray library.
- **Both archives** contain the README's example pictures: every WebP in
  `assets/examples` (the README picture and the single pictures it links to).
  Full-size example PNGs stay local and are never committed or packaged.
- **Never included:** the local virtual environment, configuration, credentials,
  profiles, shortcuts, downloaded images, caches, History images, generated
  binaries and unused local assets.

`build_release.ps1` lists every packaged file explicitly; new modules and data
files must be added there. Extract the Windows archive to use a new build;
building does not replace a running installation.

### Icon, version and signing

- The icon is MarbleScape's own drawing (a circle and an equator ellipse in
  #00C3FF): `python build_icon.py --draw` draws the PNG sizes anew. The build
  regenerates `assets/icons/marblescape.ico` from those PNGs with `build_icon.py`. The ICO contains the 16, 32, 48, 64, 128 and 256 px images; the
  tray can also use the 512 px PNG. The EXE embeds its icon and tray assets.
- `app_version.py` defines the version used by `--version`, the HTTP User-Agent
  and the EXE's Windows file/product version metadata, which
  `build_windows_version.py` generates. `X.Y.Z` in the package name comes from it.
- The executable is not code-signed, so Windows may show an
  unrecognized-publisher warning.

## Tests

### Running the suite

```powershell
python -m unittest discover -s tests
```

Several tests run only on Windows; without tkinter or pystray, the GUI test
modules fail to import.

### Rules for tests

- **Clipboard:** hidden Tk windows still use the real desktop clipboard. Mock it;
  profile copy tests assert the requested payloads through mocks, and Settings
  integration tests reject accidental clipboard access.
- **Local files:** Settings-dialog tests patch `SCRIPT_DIR`, `PROFILE_LIBRARY_PATH`
  and the output roots, so they never write the developer's `profiles.toml` or
  cache. Automatic working-settings backups store only the bytes recorded by a
  successful `load_configuration` (`LOADED_SETTINGS_FILES`) after validation,
  never the shipped template, so tests that patch the loader never write
  `backups/automatic` into the repository. Tests that reach `get_profile_cache()`
  outside the Settings harness (storage status, `_perform_update`) patch it with a
  cache in a temporary folder: the real `content/cache.sqlite3` would be migrated
  or given test pictures.
- **Tk threading:** Tk resources are disposed on their owning thread. Never call
  global `gc.collect()` from a GUI worker: it can finalize another GUI's Tcl
  variables on the wrong thread.
- **Images in Tk:** create `PhotoImage` objects with an explicit master, never
  Tk's implicit default root.
- **Round trips:** `tests/test_source_roundtrip.py` gives every image source
  non-default source, view, output and (EUMETSAT) layer choices and checks that
  profile JSON, PNG metadata and full backups keep all of them; defaults would hide
  a dropped field. Extend its `SOURCE_CHOICES` when a source gains a setting.

### What the tests cover

- **Profile transfer:** one JSON file per profile, preserved import UUIDs, partial
  failures, checksums, incomplete settings, damaged PNGs, Unicode EXIF/text round
  trips, selection/menu behavior and confirmation dialogs.
- **Import conflicts:** single-file Save As versus folder selection, UUID-only
  matching (names only for legacy records without a UUID), ` (Imported)` naming,
  overwrite/skip/copy and batch decisions, cancelling without partial changes, and
  rollback after simulated write errors.
- **Transfer integrity** (`tests/test_transfer_integrity.py`): PNG IEND
  CRC/length/trailing bytes, source/selection conflicts across providers,
  Copernicus catalogue names versus IDs, fixed/relative mosaic periods and
  boolean/numeric mismatches.
- **Backups:** both scopes, preserving profile bytes on settings-only restore and
  restoring IDs with a full backup; the missing Copernicus `date_mode` error,
  completing only known legacy period fields, preflight before prompts,
  **Yes**/**Yes to all**/**No**/**Skip** decisions, full-restore atomicity and independent
  export folders with unavailable-path fallback.
- **Profile table:** widths (restore, inline/subtable TOML, invalid values),
  numeric/unit/date/period sorting, cache refresh, heading toggles, all columns
  hidden with recovery via **Columns**, migration, type-ahead search, Profile
  name/actions above the details, export result dialogs, confirmed batch deletion
  and saving an empty library with rotation disabled. Table preferences never alter
  profile data or image/cache keys.
- **Copernicus rendering:** default/custom/invalid colors, actual dark pixels,
  independent overlay failures, picker state, PNG/JSON round trips for every
  provider and bundled mosaic layer; transparent pipelines (resize, Latest
  publication, matching EXIF/iTXt, PNG-to-profile-to-JSON, opaque wallpaper
  derivatives without changing original pixels); neutral/flat/transparent tone
  adjustments and 8K sizing.
- **Data coverage:** mask math, overlays, failure/identity rules, transfer/backup
  round trips and the Settings system-row controls, offline.
- **Catalogue schedule:** midnight, DST, cancellation, restart and changed schedules.
- **Image naming:** name parts, UTC conversion, Unicode/Windows-safe length limits,
  collisions, latest-state reuse, unchanged cache hash keys, History
  metadata/name preservation and retention across legacy and new prefixes.
- **Tray and dialogs:** activation, manual/background startup, open-window Exit,
  worker completion; the Support dialog over Settings and standalone, multiple Tk
  interpreters, repeated open/close, failed construction and retry, duplicate
  requests, centering and the two support links. Info/About tests check section
  headings and key facts.
- **Update loop:** rotation, reloads during downloads, manual apply and the
  profile refresh queue, with network and Windows mocked.

## Schema history

### Portable profile JSON

| Version | Change |
|---|---|
| 2 | Per-file integrity checks. |
| 3 | Device settings removed from profiles (see [Device settings](#device-settings-are-not-profile-settings)). Versions 1 and 2 still import; older readers reject version 3. |

### Profile list ([profile_list])

| Schema | Change |
|---|---|
| 4 | ID column and the manually applied profile reference. |
| 5 | Zoom/lookback/output/labels/cache columns; empty `visible_columns` allowed; `sort_column`/`sort_descending` stored globally. Optional `column_widths` maps stable column keys to validated pixel widths; missing entries use shared defaults. |
| 6 | `last_download` and persistent `column_order`. |
| 7 | `no_data_color`. |
| 8 | `image_size`, `mosaic_contrast`, `auto_brightness`, `auto_contrast`. |
| 9 | `rotation_enabled` and `data_coverage`. |
| 10 | Local-only `active` column, headed **Status**. |
| 11 | Local-only `history` column, inserted visibly after `active` in saved visibility lists and column orders. |
| 12 | `map_borders` (**Country borders**) next to `map_labels` (heading **Labels**) when that column is visible or ordered. |
| 13 | `short_id` (**Short ID**, first 8 characters of the ID): inserted after `id` in a saved `column_order`, but not added to a saved `visible_columns`, so it starts hidden. |
| 14 | `image_updates` (**Updates**, the profile's `source.check_for_updates`): inserted visibly after `history` (else after `active` or `name`) in saved visibility lists and after `history` in a saved `column_order`. Unlike History it is a profile setting: switching it saves the profile list at once (`persist_image_updates`), and for the profile shown in the Image header also the **Image** tab checkbox and, when applied, `source.check_for_updates` in the configuration. |
| 15 | `resolution` (**Resolution**, the newest picture's real size from its PNG record: `width`/`height` for Copernicus, `source_resolution` otherwise): inserted after `output_resolution` (heading **Resolution selection**) in a saved `column_order`, but not added to a saved `visible_columns`, so it starts hidden. |
| 16 | `selection` split into `mission` (**Satellite / mission**), `product` and `layer`: in saved `visible_columns` and `column_order` the three take its place (`split_selection_column`, also for any older version); a saved `sort_column = "selection"` becomes `mission` and its saved width is dropped. |
| 17 | `shorelines` (**Shorelines**, Himawari Plot shorelines): inserted after `map_borders` (**Country borders**) in a saved `column_order`, but not added to a saved `visible_columns`, so it starts hidden. |
| 18 | `status_symbol` (the Status symbols, no heading): inserted before `active` in saved `visible_columns` (when Status is visible) and in a saved `column_order`. |
| 19 | `time_selection` (**Time selection**: Fixed, Latest, Rolling · ..., Timeless) split off `time`, which now shows only the date, time or period (`_profile_time` returns both): inserted before `time` in saved `visible_columns` (when Time is visible) and in a saved `column_order`. |

The `output_resolution` column keeps its key; its heading is **Resolution selection** and it
shows the profile's own resolution choice. The columns `auto_recommendation`
(**Auto recommendation**, "Yes"/"No"), `auto_priority`, `auto_precise` and
`auto_choice` (from the newest picture's PNG record) came without a schema
change: saved `visible_columns` do not list them, so they start hidden, and
`normalize_profile_column_order` appends them.

### Profile cache database (content/cache.sqlite3)

| Schema | Change |
|---|---|
| 1 | One row per profile. |
| 2 | `source_time` (acquisition time of the cached picture). |
| 3 | Variants: primary key `(profile_id, configuration_hash, width, height)` and `used_at`; schemas 0-2 migrate automatically, each row becoming its profile's first variant. Older versions refuse schema 3. |

### PNG metadata

PNG metadata schema 4 (with schema-3 import compatibility) is separate from the
file formats above and is part of the profile-cache key. The writer is
`marblescape_image_metadata.py`; the reader and integrity checks are in
`marblescape_profile_transfer.py`. See [PNG metadata and ExifTool](PNG_METADATA.md)
for the layout and inspection commands. ExifTool is an optional external tool, not
a runtime/build dependency or bundled component.

## Invariants

### Device settings are not profile settings

`output.width`, `height`, `aspect_ratio`, `background_color` and `latest_folder`
(`DEVICE_OUTPUT_KEYS`) are dropped by `normalize_library` and
`normalize_image_settings_snapshot`, so `profiles.toml`, imports and backups lose
them; a profile's `output` holds only `render_scale`. Applying, rotating or loading
a profile never changes the global output, background or Latest folder.
- `render_scale` is `"default"`, `"auto"` or a factor >= 1.0
  (`parse_render_scale_setting(..., allow_default=True)`); `"default"` sets
  `RENDER_SCALE_INHERITED` and EUMETSAT renders with General's device setting
  `output.display_render_scale` (`DISPLAY_RENDER_SCALE`, `"auto"` or a factor; also
  the default of `windows.monitor_output_settings`). `effective_render_scale_setting`
  resolves it; `image_configuration_key` signs the effective value under the old
  `RENDER_SCALE`/`RENDER_SCALE_AUTOMATIC` keys, so `default` and an equal own value
  share EUMETSAT cache entries. A file without `display_render_scale` starts it from
  the former `render_scale` (`default` -> `auto`).

- A backup's readable profile copy is compared after the same normalization, so
  older backups restore; `normalize_device_output_settings` validates the global
  output of a backup offline.
- PNG schema stays 4: its `profile_settings` may omit the device keys, and an older
  PNG's recorded output size must still match the picture.
- A settings-only reload keeps showing the active rotation profile (its Image
  settings are applied over the reloaded file). When output size, aspect ratio or
  background color changed, it checks the shown image at once.
- `sync_system_background_color` sets the Windows desktop color
  (IDesktopWallpaper::SetBackgroundColor) after each wallpaper update and stores the
  user's color once in `content/previous_wallpapers/background_color.json`;
  `restore_previous_wallpaper` gives it back when no connected display is updated
  any more.

### Copernicus map overlays

`map_borders`/`map_border_color` (GISCO country borders) are separate from the OSM
label layer (`map_labels`/`map_label_color`); both default to off and `#FFFFFF`
and accept `#RRGGBB` or `transparent`.

- A saved selection without `map_label_color` normalizes to
  `LEGACY_MAP_LABEL_COLOR` (`#000000`), the color it was rendered with; one without
  `map_labels` to the former default, on. One without the border keys takes the
  labels' switch and color. Strict import accepts such records without repair.
- `map_overlay_signature` keeps image and frame signatures unchanged while borders
  match the labels, so existing cached images stay valid.

### Copernicus No-data colors

No-data choices are kept per layer kind and read through `no_data_choice`:
`no_data_color` for mosaics and `scene_no_data_color` for regular layers, each
`#RRGGBB`, `transparent` or `blur`.

- **Defaults:** new selections use `blur` for both. A saved selection without the
  keys keeps `LEGACY_NO_DATA_COLOR` (`#FFFFFF`) and `LEGACY_SCENE_NO_DATA_COLOR`
  (`transparent`, the map background), the look it was rendered with.
- **Separate key:** `scene_no_data_color` exists because older regular selections
  already store an unused `no_data_color`.
- **Cache keys:** `transparent` on regular layers is left out of image and frame
  signatures, so existing cached images stay valid; other choices are included.
- **Legacy Gap fill:** `black` (`LEGACY_BLACK_COVERAGE_MODE`) normalizes to `single`
  with `scene_no_data_color = "#000000"`; strict import accepts both without repair.
- **Blur** (`fill_no_data_blur`): nearest-pixel fill with a light blur near real
  pixels and a floating-point pull-push fill farther away. **Edge Blur**
  (`blur_edge`, `edge=True`) feeds the pull-push fill only the real pixels within
  `NO_DATA_EDGE_SHARE` (0.4 %) of the longer side from a gap, so a far gap takes
  its border's colors. `blur` keeps its exact pixels. New selections default to
  `blur` (before 2026-10-06 `blur_edge`); a saved choice stays.
- **Order:** the renderer fills alpha-masked gaps before independent map overlays;
  coverage is measured before either.
- **Transparent results:** detected monitors use per-display composition for alpha
  sources even without explicit monitor overrides.
- **Display paths:** IDesktopWallpaper can list an attached display with an empty
  device path while its real path shows as detached (GetMonitorRECT E_FAIL or
  E_INVALIDARG). `resolve_wallpaper_monitors` gives such a slot the path user32
  reports at the same rectangle (`windows_display_layout`) and keeps `slot: ""` for
  SetWallpaper; an unmatched empty slot is skipped. An empty ID can set every
  display, so it is written first, the real path afterwards for a later start.
- **Paused displays:** `windows.paused_displays` (JSON list; `"*"` pauses every display)
  holds General > Output device > **Pause wallpaper updates**. A saved position stays;
  `effective_wallpaper_positions` turns paused displays into `"none"` for the wallpaper
  code. Loading turns the former position `"none"` into a pause
  (`migrate_paused_positions`; a shared `"none"` becomes `"*"` with position `fill`).
- **Picture size:** `image_dimensions_for` enlarges every source's output with
  `displays_image_size` (the `copernicus_auto_size` rule: physical display sizes and
  per-display outputs, paused displays left out; EUMETSAT capped at
  `MAX_WMS_DIMENSION`) while Windows wallpapers are on; the cache key then holds
  `monitor_image_targets`.
- **Display changes:** `watch_display_layout` (tray mode, every
  `DISPLAY_WATCH_SECONDS`) compares the user32 layout with `WALLPAPER_LAYOUT`, saved
  by each successful `set_windows_wallpaper`, and sets the current picture again once
  a new layout holds for one more check.
- Both render colors are part of image/profile signatures and portable metadata.
  Older portable records may omit them, but explicit invalid values and
  contradictory PNG descriptive colors are rejected.

### Copernicus data masks and coverage

- `has_data_mask_alpha` recognizes the ways catalogue evalscripts put their binary
  `dataMask` into alpha (`, x.dataMask]`, `push`/`concat`, a trailing color-helper
  argument, an all-zero return). A test requires it for every catalogue and mosaic
  layer.
- `needs_data_mask_alpha` marks single RGB/grey outputs that read `dataMask` without
  returning it (Landsat Wildfires); `evalscript_with_data_mask_alpha` appends a
  wrapper that adds it as alpha. Their frame signature gains `("data_mask_alpha",
  True)`, so only those cached images render again.
- `marblescape_data_coverage.py` validates binary-mask counts and percentage;
  non-binary alpha is not a mask. Copernicus measures coverage before map
  composition; other providers return unavailable until their mask semantics are
  verified. PNG schema 4 stores the result in both provenance copies, import
  rejects mismatched counts/percentages, and derivatives keep source-view coverage.
  The rendered-image hash stays informational.

### Copernicus catalogue dates

- The catalogue filter adds `eo:cloud_cover>=0` to every cloud limit: Landsat night
  passes report -1 and return no pixels. Without any filter the catalogue omits
  them, with a cloud filter it lists them.
- The Settings date survives layer, product, mission and configuration changes of
  the same date granularity (`_kept_date`, `_date_unit`); the next date list
  confirms it or falls back to **Latest available** with a notice. While dates load, the dropdown
  shows `LOADING_DATES_LABEL`.

### CIRA SLIDER padding

- SLIDER pads non-square sectors to a square tile grid with black rows.
  `content_box_from_tile` measures the level-0 tile; padding counts only when it is
  exactly black, symmetric within 1% and at least 3% per side, else the box stays
  `FULL_CONTENT_BOX`. An all-black tile is undecidable and not cached.
- `SliderClient.content_box` caches the box per sector in memory and in
  `content/slider_content_boxes.json` (`configure_content_box_store`, set by
  `get_slider_client`). `latest()` adds `content_box` to the frame; `fetch_image`
  scales and clips to it, so padding is never drawn.
- Saved resolutions stay the grid values (`"5000x5000"`). `effective_resolution`
  and the products' `effective_resolutions` give the visible size for the Settings
  labels, the profile table and `choose_automatic_source_resolution`.
- `slider_frame_signature` adds `("content_box", box)` only for padded sectors, so
  square sectors keep their cached images.

### Copernicus Recommendation

- `CopernicusClient.search_view` searches the STAC Catalog with the view's bbox
  polygon (not the centre point, not `distinct`), every cloud estimate
  (`eo:cloud_cover>=0`, so night passes stay out) and pages through `context.next`.
- `max_cloud_cover` defaults to 100 for new selections (before 2026-10-06: 30); a saved
  selection without the key normalizes to `LEGACY_MAX_CLOUD_COVER` (30).
- `marblescape_copernicus_advice` draws each footprint on a 128-column grid of the
  view (Web Mercator, as rendered; date-line copies folded to the view) and replays
  each variant like the renderer: the newest day whose tile covers the view's centre
  within the cloud limit, then that day's tiles (single) or the lookback's tiles,
  newest first (`mostRecent`). Results are cached per variant and newest day, so the
  90-day replay is cheap. Thresholds: `FULL_COVERAGE` 98, `FEWEST_CLOUDS_COVERAGE`
  90, `CLEAR_CLOUDS` 10.
- `marblescape_copernicus_advice_window.RecommendationWindow` asks nothing until
  **Check** (`READY_TEXT`) and runs checks and previews in worker threads that hold
  no Tk object; `CopernicusSettings.open_recommendation` builds those closures from
  plain values. DEM layers and missing credentials show a reason instead of a check.
- Mosaics: `advise_mosaic` takes the newest `MOSAIC_PERIODS` periods from the
  catalogue features (`period_start` of each feature date, the newest catalogue date
  per period). Their `Variant` carries `period`, `granularity`, `offset`
  (`period_offset` counts like `rolling_quarter_start`/`rolling_month_start`) and
  `newest`; coverage stays `None` until Precise check, and Data coverage is the newest
  period with at least `FULL_COVERAGE`, else the most covered measured one.
- Precise check: `CopernicusClient.view_mask` renders `mask_evalscript(collection)`
  (red: `dataMask`; green: clouds from Sentinel-2 L2A `SCL` 8/9/10, L1C `CLM`,
  Landsat `BQA` bit 3; other collections give coverage only) through
  `_process_tile(..., evalscript=...)`, so the render's filters and mosaicking apply.
  `measure_rows` reads one mask per distinct `Replay.tile_key` (mosaics: per period),
  `MASK_WORKERS` (4) at a time, and marks outcomes `measured`/`measured_clouds`.
- Small requests: `small_frame` draws the full output's view with `view_scale`,
  which `_view_pixels` honours, so Process bboxes and `_map_overlay` keep the same
  area, inside `PREVIEW_BOX` or `MASK_BOX`, finer where `PIXEL_LIMITS` needs it
  (Sentinel-2 L1C 200 m, L2A 1500 m, else `DEFAULT_PIXEL_LIMIT`, times
  `PIXEL_LIMIT_MARGIN`). An HTTP 400 that states an unlisted collection's limit is
  learnt for the session. `small_units` estimates processing units from the
  2026-10-06 measurements (`PREVIEW_UNITS_PER_TILE`, `MASK_UNITS_PER_TILE`).
- Previews: `render_preview` runs `fetch_image` on the small frame (tones, No-data
  fill, overlays) and returns RGB within `PREVIEW_BOX`; the window keeps them per
  (variant, checked place).
- **Use auto recommendation**: profile keys `auto_recommendation`, `auto_priority`
  (`fewest_clouds`, `newest` or `full_coverage`; `fewest_clouds` is the first box,
  Fewest clouds for scenes and Newest for mosaics; `newest` is Newest: for mosaics
  the first box too, for scenes `newest_row`, the 100% (or radar) variants' shortest
  Gap fill reaching `FEWEST_CLOUDS_COVERAGE`, else the most covered; never coerced,
  so strict import stays exact; Newest is the palette's `newest` orange everywhere) and `auto_precise`. `get_copernicus_frame` calls
  `recommended_profile`: `fetch_advice` (precise for a mosaic's `full_coverage`,
  whose masks `_MASK_CACHE` keeps for the session, and for scenes with
  `auto_precise`), `auto_row` (the priority, else the other, else none) and
  `applied_profile` (as Apply). Errors and no choice render the saved settings and
  are logged; `frame["auto_choice"]` names the choice. The frame's profile is the
  applied one, so frame signatures follow the choice, while `image_configuration_key`
  drops the three keys as long as the rule is off, so existing cache entries stay
  valid. PNG provenance adds `auto_recommendation`, `auto_priority`, `auto_precise`
  and `auto_choice`; import then skips the chosen date, Gap fill and cloud fields.
  In Settings `_apply_auto_lock` (after the quarter, coverage and cloud refreshers)
  greys out what the rule chooses; `last_choice` per `view_key` feeds the
  **Last choice** line.

### Mosaic tone adjustments

`marblescape_mosaic_adjustments.py` applies a common RGB LUT to the assembled RGBA
mosaic before gap filling and overlays. Statistics use only alpha=255 pixels;
neutral settings preserve pixels. Tone rules a-f (see the tables under
[Tone rules](IMAGE_SOURCES.md#tone-rules)) are combinations of four building
blocks on top of `mosaic-tone-v2`: the highlight shoulder (fixed knee 0.85, or
the adaptive knee 0.65-0.80 that reaches white at value 255, chosen by the share
of stretched luminance above 0.75 between 10% and 25%; rule c switches to it only
with that share at 25% or more and `stretch_high` >= 240), auto brightness (on or
neutral), the large bright area adaptation (black point raised half, gamma not
above 1.0, blended over the same share) and, for Landsat true colour, a soft
evalscript (linear to output 0.7 at reflectance 0.28, then an exponential
shoulder with k 0.9 that reaches white at reflectance 1.0). `mosaic-tone-v2` (one LUT for all channels):
Auto contrast stretches between the 2% quantile of the darkest and the 98%
quantile of the brightest channel, at most 1.5-fold (`MAX_STRETCH_FACTOR`, the range
widened around its middle), ending at `WHITE_POINT` (0.965), with a soft roll-off
above `HIGHLIGHT_KNEE` (0.85)
that fades out while the share of stretched pixels above `BRIGHT_LEVEL` (0.9) grows
across `BRIGHT_SHARE_RANGE` (8-20%), so large bright surfaces keep the straight
stretch;
without it, manual contrast is a rational S-curve around the median (slope
`contrast` at the pivot, its inverse at black and white); last, a midtone gamma
applies Auto brightness or, with Auto contrast, the manual brightness
(`100 / brightness`). Auto brightness alone moves the median toward 0.42 (gamma
0.5-1.15). With Auto contrast it lifts only while the 65% luminance quantile is
below 0.25 (gamma down to 0.8) and otherwise darkens a median above 0.42 (gamma
up to 1.15). The constants were tuned against before/after sheets of local
Sentinel-2, Landsat and mosaic pictures; for mosaics, previews must use the 100%
picture the app requests with Auto tones (a profile's own brightness is not sent
upstream then). `TONE_REVISION` is part of the cache key of Auto contrast mosaics
(`("tone", "mosaic-tone-v2", TONE_REVISION)`): raise it whenever retuning changes
their pictures, so cached ones render again. When the brightest channel's 98%
quantile reaches `SATURATED_LEVEL` (250), the source already clipped its
highlights: Auto contrast leaves the picture unchanged (stretch 0-255, no roll-off
or white point). That rule left `TONE_REVISION` unchanged, because
Sentinel-2 cloudless mosaics rendered at 100% stay well below it (at most 238 in
the tuning set); a rare cached exception refreshes with its next source frame.
`evalscript_brightness` sends 100% upstream whenever an Auto option is on. PNG
provenance validates the applied algorithm and parameters (`mosaic-tone-v1`
records still import); JSON stores the selected controls. Defaults:
`image_size = auto`, contrast 100%, both Auto on for new selections; a saved
selection without the Auto keys predates them and normalizes to off.
`copernicus_frame_signature` adds `("tone", "mosaic-tone-v2")` only where v2
renders differently (`mosaic_tone_changed_in_v2`), so other cached mosaics stay
valid.
Fixed image sizes bypass the WMS render cap; Auto resolves the saved baseline
against connected monitor requirements. Cache keys include these choices, and
output-size checks prevent reusing a smaller cached image.

### Profile list saving

The profile list has no draft. `ProfilesSettings._save()` hands every change (after
its confirmation) to `on_save`, which writes `profiles.toml` at once and updates the
Save button's baseline; a failed write restores `_saved_state`.

- History folder renames/deletions run only after a successful write.
- Rotation inputs set by code use `_quiet`, so they do not trigger a save.
- The column layout (global config) is written at once through `on_layout_change`;
  widths are compared on mouse release. Save and Apply write it via
  `write_table_layout`, which reads the live layout, so the Save baseline never
  counts the layout as pending. Settings backups round-trip widths; runtime
  snapshots deep-copy the mapping for rollback.
- History checkboxes call `on_history_toggle`, which writes only those `enabled`
  flags (and `history.enabled` for the snapshot row) and mirrors them into the open
  History & Storage draft; other draft edits stay unsaved.
- Typed sorting only moves Treeview rows; profile data and rotation order stay.
  Type-ahead uses casefolded raw names and a one-second monotonic timeout and only
  selects rows.
- The profile header compares the current portable rendering settings with the
  selected profile; "(modified)" is display-only.
- Import preflight aggregates blocked entries and safe additions before any
  mutation; **Skip all** repairs still permits complete entries; **Cancel** rolls back the
  whole import.
- Rotation membership is local: excluded from portable JSON/PNG, new imports start
  excluded, an overwritten UUID keeps its membership. The column menu follows the
  dragged heading order.
- LOST/SOURCE/NETWORK/UNAVAIL: `note_profile_outcome` keeps each saved profile's last failed attempt in
  `PROFILE_FAILURES` (in memory, never saved or exported) until its next success, from
  the update loop, background downloads/checks and the rotation preload.
  `profile_failure_state` gives "LOST" when the exception chain holds a provider
  `SelectionLostError` (NOAA, Himawari, CIRA SLIDER, NASA Worldview: saved area,
  product or size no longer listed); "SOURCE" for an `HTTPError` in the chain, or a
  connection error (`CONNECTION_ERRORS`, also a `URLError` reason) while
  `internet_connected()` (Windows Network List Manager, cached 30 s) is true;
  "NETWORK" when it is false (without Windows: a `socket.gaierror`); else "UNAVAIL".
  A LOST rotation step is skipped after one attempt like `NoImageData`; the next
  round tries it again. NETWORK calls `RotationScheduler.failure(keep=True)`: the
  profile stays next and the rotation waits one interval.
- Strongest active storm (`STRONGEST_STORM_ID`: `storm_strongest` for each GOES side,
  `jma_storm_strongest` for Himawari) is always listed first in Active storms. The
  provider's `_area` resolves it to the strongest listed storm
  (`storm_rank`/`strongest_storm` by name for NOAA, JMA's category for Himawari; ties:
  the newest number), keeping the entry's id; NOAA's region check uses `storm_id`.
  The choice is kept for `STRONGEST_STORM_TTL` (1 hour; every `list_storms` renews
  it), since reading all NOAA storm pages takes about half a minute; a Himawari
  storm's position still follows every `STORM_TTL`. When `latest` fails for a choice
  made before that check began (choice number `_strongest_serial`), it drops the
  choice and tries once more with a fresh storm list.
  Without a storm `_area` raises `UnavailableError` (UNAVAIL, not LOST). Its products
  are those of the chosen storm, so the catalogue cache treats them as optional
  (`OPTIONAL_PRODUCT_AREAS`).
- Active storms: `CatalogueClient.refresh_storms("noaa" | "himawari")` reads only the
  storm list (`NOAAClient.list_storms`: `floater_index.php` and the storm pages;
  `HimawariClient.list_storms`: JMA `targetTc.json` and each `specifications.json`)
  when the cached areas are used and `<source>_storms_checked_at` in `catalogues.json`
  is older than `NOAA_STORM_CHECK_SECONDS` (retry after `NOAA_STORM_RETRY_SECONDS`).
  An incomplete discovery or full refresh stores what it read (`merge_areas`); only
  `catalogue_missing_categories` and `catalogue_failed_areas` keep cached entries.
- Himawari views: `sources.himawari` adds `shorelines`/`shoreline_color` and
  `center`/`latitude`/`longitude` (`marblescape_himawari.normalize_profile`). A storm
  area (`jma_storm_TC<number>`, `nict_target_area`) is the D531106 full disk whose frame
  carries `center` from JMA (`advancedHours` 0) or NICT's `json/D531108/<time>.json`;
  `get_himawari_frame` adds the saved coordinates to other full-disk frames. A frame's
  `center` is placed exactly in the middle. Storm areas (`is_storm_area`) render with
  Fit and `storm_view_zoom` (about `STORM_VIEW_KM` across at Zoom 1), and Automatic
  resolution uses the same zoom (`_resolved_profile_resolution`).
  `disk_pixel` is the CGMS geostationary projection (sub-satellite longitude 140.7).
  Unused values (a color while off, coordinates while off) are left out of the image
  cache key, so earlier pictures keep their key. Coastline tiles (the `ffff00` variant,
  recolored from its alpha) are cached in `content/himawari_shorelines`.
- The **Status** column (`profile_status`, text) shows "ACTIVE", "QUEUE", "CHECK" or the
  running download from `profile_download_status()` (the `DOWNLOAD_PROGRESS` subject set
  by `perform_update`): only its progress, or "RENDER" while Copernicus renders, so the
  column stays narrow; the footer names it in full. The symbol goes to its own column
  `status_symbol` (`STATUS_SYMBOL_COLUMN`, empty heading, 28 px like a checkbox column
  at its minimum, "Status symbol" in the Columns menu): ● `ACTIVE_SYMBOL`, ⋯
  `QUEUE_SYMBOL`, ↻ `ACTIVITY_SYMBOL`, ⭳ `DOWNLOAD_SYMBOL`, ⧉ `RENDER_SYMBOL`, and for a
  failure `FAILURE_SYMBOLS` (⊘ LOST, ☁ SOURCE, ↯ NETWORK, ⊖ UNAVAIL), which replaces ●. Its id stays `active`; its default width
  `STATUS_COLUMN_WIDTH` (130 px) fits every status, which
  `test_status_column_fits_the_longest_status_text` checks against the table font
  of both themes. Like other columns it can be dragged down to 45 px (the text is
  then cut), and a saved width is kept.
- The coverage column derives from verified published/cache provenance and is not
  an editable setting; profile JSON may carry `data_coverage_percent` as
  descriptive data.

### History, Latest and the profile cache

- History routes archived PNGs into `_no profile` or a profile-name/full-UUID
  folder, by the embedded `profile_id` of a still-saved profile and that profile's
  policy (unknown IDs fall back to `_no profile` and the global policy).
  Per-profile retention lives in the global History config, not in profiles.
  Legacy managed root PNGs are routed by metadata; unrecognized files stay.
- The no-profile image is shown from Latest (and kept in the cache's snapshot
  slot); named profile images use the content-addressed cache. Once a rotation
  profile's cached image is shown,
  `publish_cached_profile_image` copies it to Latest (archiving the replaced image
  like a manual apply), so Latest always holds the picture on screen; the
  wallpaper still uses the cached file. Queued background refreshes never touch
  Latest.
- The profile refresh queue (`PROFILE_REFRESH_QUEUE`) holds forced downloads and
  checks; IDs in `PROFILE_CHECK_ONLY` are checks (**Check for new image**), and a
  forced request for a queued check turns it into a forced download.
  `refresh_profile_cache(profile, check_only=True)` skips profiles with
  `check_for_updates` off, reads the provider's listing and downloads only when
  `profile_cache_is_current` is false: the cache `lookup` misses and the listed
  acquisition time is newer than `stored_source_time` (an older listing never
  replaces a newer cached picture). `finish_profile_refresh` counts the outcome;
  once no check is queued (or the queue is cleared), `PROFILE_CHECK_SUMMARY` gets a
  new serial and text, which the Profiles tab shows. `CHECK_NOW_EVENT` runs the regular,
  unforced check of the shown picture before queued work. `shown_profile_row_id`
  maps the shown picture to its table row (an image without a profile to the Latest
  snapshot row). The queue also takes `latest_snapshot.SYSTEM_ID`:
  `next_profile_refresh` returns it with the snapshot's settings, and
  `refresh_profile_cache` clears `APPLIED_PROFILE_ID` for it and stores into
  `CACHE_ID` with the no-profile signature; `_perform_update` writes anonymous
  provenance for that slot and `save_profile_image` archives its predecessor to
  `_no profile` under History (no profile).
- The tray's **Profile rotation** switch reads `profiles.toml` under
  `CONFIGURATION_FILE_LOCK` instead of the update loop's `IMAGE_PROFILE_LIBRARY`,
  which lags behind a list saved in Settings while a download runs. It bumps
  `ROTATION_SWITCH` (serial, enabled); the Settings status passes it as
  `rotation_switch`, and the **Profiles** tab sets its **Enable rotation** switch quietly
  and refreshes the host's saved state, so a later table save keeps the switch.
- Rotation preload (`rotation.preload_next`, default true in `profiles.toml`):
  `RotationScheduler.upcoming()` names the next profile and its deadline (the shuffled
  pass is fixed in advance). `ROTATION_PRELOAD_LEAD_SECONDS` (60 s, at most half the
  rotation interval) before the deadline the main loop runs
  `refresh_profile_cache(check_only=True)` for it once per step
  (`preloaded_rotation_step`), unless it is the shown profile; the sleep wakes for it.
  A failure is only logged. `preload_next` is excluded from the scheduler fingerprint,
  so toggling it keeps the timing.
- While `unmodified_applied_profile_id()` holds, `perform_update` also installs a
  manually applied profile's PNG in the cache (without archiving, since Latest
  already did), and the main loop looks the cache up before downloading (`lookup`,
  or `lookup_configuration` with Imagery updates off) and republishes a hit.
  Otherwise the image has no profile and the same happens under the reserved
  `marblescape_snapshot.CACHE_ID` (32 zeros, never a UUID4), one slot that every
  new no-profile image replaces. `synchronize_profile_image_cache` retains it, and
  the Settings status maps its entry to the Latest snapshot row's ID.
- `image_cache_configuration_key` drops `applied_profile_id` (only
  `image_profile_id` names the pictured profile), so rotation and manual apply share
  entries and a manual switch keeps rotation entries valid;
  `image_configuration_key` keeps it to detect the switch.
- `archive_history_copy` skips an image whose bytes already exist in the target
  History folder, because cache hits republish identical files.
- The applied selection owns a reused picture. Without a rotation profile the
  main loop asks the applied profile's cache slot first and Latest second (no
  profile: Latest first). The cache key holds the applied profile only while its
  settings are unmodified (`unmodified_applied_profile_id(configuration)`);
  modified settings count as the Latest snapshot whichever profile was applied,
  so the snapshot row with equal settings reuses that picture, and a profile
  updated to equal settings gets its own key. When the reused picture names another
  owner (`shown_identity_differs`), `publish_with_identity` publishes a copy with
  only the identity changed (`relabel_picture`: profile ID and name, or the Latest
  snapshot's IDs) into Latest and the slot's cache; replacing Latest's own copy
  archives nothing. The table's Active row, History routing and the PNG record
  then agree with the applied selection.
- Cache variants (`marblescape_cache.py`, schema 3): `profile_images` has the
  primary key `(profile_id, configuration_hash, width, height)`, so each profile
  keeps one row per image settings and size; a newer frame overwrites its row.
  `used_at` (strictly increasing within the process) orders the rows: the newest
  is the profile's current picture (`current`), and `lookup`/`lookup_configuration`
  hits and installs touch it. `install` returns the previous `current` picture for
  History archiving, as with one row per profile. `enforce_limits` keeps the newest
  `max_variants` rows per profile (not for `unlimited_variants`: the snapshot's
  `CACHE_ID`, which also holds every modified profile's pictures, is limited only
  by size), then drops the least recently used rows that are
  not current until the distinct files fit `max_bytes`; a row whose file is some
  profile's current picture is skipped (it would free nothing). Profiles in
  `separate_limits` (the snapshot's `CACHE_ID`) are trimmed the same way against
  their own byte budget and never count toward `max_bytes`, so pictures without a
  profile cannot evict saved profiles' pictures; `status()` then adds `main` and
  `separate` usage and leaves them out of `profiles`/`variants`. `install` runs it
  without pruning, so the caller archives first and prunes after; a cross-profile
  `lookup` binding and `synchronize_profile_image_cache` (startup and every reload,
  so saved limits apply at once) prune too. `get_profile_cache()` sets the limits
  from `[cache]` (`max_size_gb` decimal GB, `variants_per_profile`,
  `latest_snapshot_size_gb` for the snapshot's separate limit). Schemas 0-2
  migrate in one transaction: each profile's row becomes its first variant with
  `used_at = updated_at`.

### Update loop and reloads

- A settings reload sets `CONFIGURATION_RELOAD_EVENT`. A download running at that
  moment is not discarded: `_perform_update` stores its picture in the cache of the
  profile it was made for (`cache_profile_id`, else the unmodified applied profile,
  else the Latest snapshot's `CACHE_ID`), never in Latest, and raises
  `UpdateSuperseded`. Background refreshes and the rotation preload count it as a
  new picture. For the shown selection (no pending rotation step) the main loop
  then runs the next cycle at once (`superseded_download`), so a kept picture that
  still applies is shown from the cache.
- Applying a profile in the Profiles tab while `DOWNLOAD_PROGRESS` is active
  starts `show_cached_profile_now` in a thread: the profile's current cache image
  (the Latest snapshot row: its slot) is published to Latest and set as wallpaper
  when its PNG provenance has the same portable image settings and the size of
  `image_dimensions_for`. `LATEST_IMAGE_LOCK` serializes Latest installs with the
  worker; the worker re-checks the provider after its download.
- `image_load_pending` remembers an image requested by a non-settings-only reload
  (**Apply Image**) until a cycle finishes without being discarded. Meanwhile
  settings-only reloads and queued profile refreshes do not postpone it.

### Latest snapshot

`marblescape_snapshot.py` validates the protected system snapshot stored in the
global configuration, outside the profile library. The virtual row is pinned and
excluded from rotation and mutation; exporting converts it to an ordinary profile
with its own stable UUID. New distinct images get new snapshot/export IDs;
identical refreshes keep identity. Backups include it in both scopes and validate
it before restore. Persistence failures keep the saved image and show a completion
warning.

### Background indexes and catalogues

- `PublishedImageIndex` reads bounded PNG provenance in a background index, keyed
  by UUID and file stat; it never counts cache-only files or blocks the Tk loop.
- `CatalogueSchedule` stores successful local calendar slots atomically, catches up
  missed runs once, coalesces overlapping requests and retries failures after five
  minutes.
- The EUMETSAT catalogue uses only the viewer's `view.Theme.*` categories and keeps
  viewer-decorated products without a Product Navigator record as `available:
  False` entries for the "not available yet" line. `_CatalogueDiskCache` ignores a
  cached catalogue whose `eumetsat_version` differs from `CATALOGUE_VERSION`.

### Export verification

`write_verified_export_batch` is shared by profile JSON and both settings-backup
scopes: it flushes/fsyncs staging files, rereads bounded bytes, compares them with
the intended bytes and runs the format validator. Published destinations are
checked again before originals are discarded; injected corruption verifies batch
rollback. `marblescape_transfer_locations.py` manages local destinations and
`marblescape_import_dialog.py` provides the shared consent dialog; neither changes
profile identities or stores credentials.

### Image naming

`marblescape_image_naming.py` derives published names from bounded PNG provenance
without decoding all pixels or modifying the PNG. Names hold only creation UTC,
profile name or location and a 12-character image hash; source, product, period
and size stay in the record, and only the `MarbleScape_` prefix is read back from a
filename.

### Window appearance

- `marblescape_theme.py` applies `[display] appearance` (`system`, `light`, `dark`)
  per Tk root: every tray dialog root (`create_tray_dialog_root`) and the startup
  recovery window. Toplevels inherit it. `system` reads `AppsUseLightTheme` and
  `follow_system` polls it every two seconds; Save applies a new choice to the open
  Settings root at once. The appearance is not part of any image key.
- Sun Valley is loaded per interpreter by `_use_sun_valley`, which sources
  `sv_ttk`'s Tcl file itself because `sv_ttk.set_theme` rejects a patched
  `tkinter.Tk` (the Settings test harness), so dialog tests run themed.
  `configure_colors` is called explicitly: the first switch of a root sends no
  `<<ThemeChanged>>`, so plain tk widgets and the root style would keep the old
  colors.
- Fast sprites: Tk 9 tiles the stretched center of Sun Valley's small 9-slice
  sprites with many alpha-blended copies, which made resizing slow (Button,
  Entry and Combobox about 30× slower than the classic theme). While sourcing,
  `::ttk::style` is aliased to `::marblescape_sv::style`, which gives every
  `element create ... image ... -border` a copy whose center is widened by
  `SPRITE_WIDEN_PIXELS` (240 × 24 px), only along axes whose center rows or
  columns are uniform pixel by pixel, and adds `-width`/`-height` equal to the
  original image so requested sizes stay identical. Afterwards the alias is
  removed and the original command restored; any error inside `patch` leaves
  the element unchanged. `fast_sprite_count` reports the patched elements, and
  `test_widened_sprites_keep_every_widget_size_and_restore_ttk_style` guards
  sizes and the restore. Rendering was compared pixel by pixel (0 differences
  in light and dark).
- Sun Valley's `<<ThemeChanged>>` handler (`configure_colors` on the root's class)
  runs `tk_setPalette`, which resets the foreground of every widget once the event
  loop runs. `apply_appearance` binds `<<ThemeChanged>>` on the `all` tag, which
  runs after the class binding, and notifies the theme listeners again for the root.
- The Support dialog draws its star and cup icons in the text color of the mode and
  repaints them in place (`PhotoImage.paste`) on an appearance change.
- `tk_setPalette` also writes the text color into every ttk label, entry, combobox
  and spinbox and into the option database, which hides the style's grey disabled
  text. `_release_text_colors` (on every theme load and `<<ThemeChanged>>`) clears
  that color where it equals the theme's, adds `*TLabel.foreground` and the like as
  empty `interactive` options for later widgets, and maps disabled fields to the
  grey of disabled check buttons (`.` disabled foreground).
- Classic `tk.Scale` sliders are not styled by Sun Valley: `style_scale` keeps them on
  the palette and gives a disabled one a flat, grey thumb, a pale trough and a
  disabled value label (`set_scale_enabled`). `style_swatch` frames color previews
  with a 1 px highlight in the text color of the mode.
- Sun Valley skips its menu colors on Windows and `tk_setPalette` leaves menu check
  marks black. `_style_menus` sets `selectcolor` of every menu, and the
  `*Menu.selectColor` option for later menus, to the text color of the mode.
- Plain tk widgets get no `<<ThemeChanged>>`; colors MarbleScape sets itself are
  refreshed through `on_theme_change` (`keep_background`, `keep_palette_color`,
  the profile table's active row). Sun Valley sets backgrounds only on the root
  style `.`, so lookups fall back to it.
- The Settings tab strip uses `TAB_BUTTON_STYLE` (`MarbleScapeTab.Toolbutton`),
  built from Sun Valley's notebook-tab images because its Toolbutton has no
  selected look; other themes fall back to `Toolbutton` by name. Text is measured
  with `font measure` in the window's own interpreter. Style layouts belong to
  one ttk theme, so the empty `MarbleScapeTabless.TNotebook.Tab` layout that
  hides the built-in tabs is re-created on every appearance change.
- The **Image** > **Source** label column is `source_label_column_minsize`: at least 180
  px and wider than the widest label control (`SOURCE_LABEL_COLUMN_WIDEST`) plus
  15 px in the active theme, so all source frames stay aligned.
- The Info tab shows `INFO_SECTIONS` through `markup_text`: a read-only `tk.Text`
  that renders `**bold**` (what the user clicks or chooses) and `*italic*`
  (examples) with copies of `TkDefaultFont`, takes the theme's colors on every
  appearance change and sets its height to its display lines on `<Configure>`.
  Texts MarbleScape shows are written in "quotes". `plain_text` strips the markers;
  tests compare key facts against it. Other Settings texts stay plain labels.
- Each mapped window gets a matching title bar through `DwmSetWindowAttribute`.
  Without `sv_ttk` everything keeps the default ttk theme.
- The Settings window is at least `SETTINGS_MIN_WIDTH` (900 px at 100 % scaling) wide
  and opens at the size saved in `[display]` (`settings_window_width/height`, at
  100 % scaling), by default `SETTINGS_DEFAULT_WIDTH` (the minimum) x
  `SETTINGS_DEFAULT_HEIGHT` (700). The size is written at once like the table
  layout, never as a draft. At the minimum the whole tab strip
  fits without its overflow scrollbar (so switching tabs never scrolls it), and every
  tab and the footer fit without clipping in both themes; only the profile table
  scrolls sideways. `test_every_tab_fits_the_minimum_window_width` enforces it, so a
  wider control or a longer tab title needs a new row or a higher minimum.

### Tray, dialogs and threads

- Settings polls `save_is_pending` every 400 ms to enable **Save** and show
  "Unsaved changes". Building what Save would write takes about 100 ms with a
  large profile list, so it is rebuilt only when `save_check_inputs` changes (form
  variables, display drafts, Access credentials, `library_fingerprint()`, table
  layout, Windows startup, the configuration and profile files' size and time) or
  after `SAVE_CHECK_REFRESH_SECONDS`. A new input of the dry-run save must join
  that key; otherwise Save follows it only after the refresh interval.
- Tray shutdown waits for the image worker and owned GUI threads before releasing
  the instance mutex. `stop_application` starts `start_forced_exit_timer`: a real
  run (the entry point sets `FORCED_EXIT["armed"]`, tests never do) calls
  `os._exit(0)` after `FORCED_EXIT_SECONDS` (10). A restart passes
  `MARBLESCAPE_RESTART_FROM_PID`; `acquire_instance_for_start` waits
  `RESTART_WAIT_SECONDS` (15) and then `end_previous_instance` ends that process,
  only when its program is python.exe, pythonw.exe or the frozen executable.
- `log()` also writes `content/marblescape.log` (`RotatingFileHandler`, about 1 MB,
  two older files) once the entry point calls `enable_log_file()`; tests never
  write it. `redact_log_text` masks the Copernicus client ID, secret and protected
  secret, bearer tokens and `access_token`/`refresh_token`/`client_secret`/`password`
  values. Uncaught errors of threads and the main thread are logged too.
- Provider `_request` methods turn `http.client.HTTPException` (a response cut off
  midway, `IncompleteRead`) into their `UnavailableError`, so the Status is SOURCE.
  While NOAA's pages fail, `NOAAClient` keeps the known regions (index read again
  after 5 minutes), product pages (after 10 minutes) and strongest storm (list read
  again after `STRONGEST_STORM_RETRY`, 10 minutes).
- The shared About/tray Support dialog owns its `PhotoImage` objects explicitly.
  Tray requests while Settings is open are queued for its GUI thread, which creates
  a centered `Toplevel`; without Settings, the dialog uses its own root.
