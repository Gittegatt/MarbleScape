# PNG metadata and ExifTool

How MarbleScape stores image provenance and profile settings, and how to
inspect them locally without changing the image.

[Back to the main README](../README.md) · [Profile import/export](USER_GUIDE.md#import-and-export-profiles)

## Where the information is stored

MarbleScape embeds one JSON record twice in newly generated PNG images:

| Location inside PNG | Contents | ExifTool selector |
| --- | --- | --- |
| `iTXt` text chunk, keyword `MarbleScape` | The JSON record as UTF-8 text | `-PNG:MarbleScape` |
| `eXIf` chunk, EXIF `ImageDescription` (tag 270) | The same record as JSON with ASCII-safe Unicode escapes | `-EXIF:ImageDescription` |
| `eXIf` chunk, EXIF `Software` (tag 305) | `MarbleScape` | `-EXIF:Software` |

The two JSON strings can look different: a name such as `Süd` appears literally
in PNG text and as `S\u00fcd` in EXIF. Parsing either JSON string restores the
same fields and Unicode values. They are not required to be byte-identical.

Profile fields are inside this JSON, not separate standardized EXIF tags.
For example, `profile_name`, `quarter_offset`, and `latitude` are not exposed as
individual ExifTool tags. MarbleScape does not turn its map centre into EXIF GPS
coordinates or its acquisition time into `DateTimeOriginal`. Missing camera/GPS
fields therefore do not mean that the MarbleScape record is missing.

Embedding these records does not alter the rendered pixels. Storage overhead
depends on the settings and names; the JSON is stored twice, plus small chunk
and EXIF headers. The current writer limits the UTF-8 JSON record to 16 KiB;
ASCII Unicode escapes can make the EXIF copy larger. Existing images are not
retrofitted when the application is updated.

For PNG chunk support and arbitrary text keywords, see the official
[ExifTool PNG tag reference](https://exiftool.org/TagNames/PNG.html).

## What the fields mean

Fields depend on the source, selected product and whether a saved profile was
used. Absence of an inapplicable field is normal.

| Field or group | Meaning |
| --- | --- |
| `schema_version`, `software`, `software_version` | Record format (currently 4), application name and application version; these are different version numbers. Schema 3 remains readable. |
| `profile_name`, `profile_id` | Named profile and UUID, or the system snapshot's display name and stable normal export UUID. |
| `profile_kind`, `snapshot_id` | For profile-free images: `latest_snapshot` and a distinct snapshot UUID. Import creates a normal profile named "Latest snapshot (Imported)", never the protected system row. |
| `data_coverage_percent`, `data_coverage` | Measured percentage and mask method/counts, or `null` with method `unavailable`. See below. |
| `source`, `mission`, `product`, `layer`, `layers`, `area`, `resolution` | Applicable source and selection information; not every provider has every field. |
| `width`, `height` | Dimensions of the saved PNG in pixels, not the provider's native resolution. |
| `source_resolution` | `WIDTHxHEIGHT` of the source picture behind the PNG: the downloaded NOAA, Himawari, CIRA SLIDER (visible part) or NASA Worldview picture, or EUMETSAT's rendered WMS size. Absent for Copernicus, whose PNG is its source picture, and in older records. Descriptive only; profile import does not compare it. |
| `generated_at_utc` | Generation time of the source image, not the satellite observation time. An identical forced refresh retains the original value. |
| `source_time_utc` | Provider observation/frame time when available; not a per-pixel timestamp for a composite. |
| `latitude`, `longitude`, `map_zoom`, `map_labels`, `map_label_color`, `map_borders`, `map_border_color` | Copernicus map centre, requested extent/zoom, and the **Labels** and **Country borders** overlays with their `#RRGGBB` colors (`transparent` only in older records). Older records without `map_borders` drew the borders like the labels. |
| `shorelines`, `shoreline_color`, `center`, `latitude`, `longitude`, `center_latitude`, `center_longitude` | Himawari: **Plot shorelines** (`true`/`false`) and its `#RRGGBB` color while on; `center` with the saved `latitude`/`longitude` while **Center on coordinates** is on; `center_latitude`/`center_longitude` the place actually in the middle (those coordinates, or a storm's position at that time). Older records have none of them. |
| `selected_date`, `resolved_date`, `mosaic_period` | Saved date choice, the date resolved for this image, and the applicable mosaic period. A period is not a single acquisition. |
| `date_mode`, `quarter_offset`, `month_offset` | Fixed/latest catalogue selection or rolling quarter/month logic; the applicable offset is a count of calendar periods. |
| `coverage_mode`, `lookback_days`, `max_cloud_cover_percent`, `mosaic_brightness_percent` | Applicable Gap fill, lookback, tile-cloud limit and brightness values. The JSON's internal `coverage_mode` name differs from the UI's **Gap fill** and TOML's `gap_fill_mode`. |
| `profile_settings` | Complete portable rendering snapshot: source selection, view, EUMETSAT render quality (`output.render_scale`: `default` for General's display render quality, `auto` or a factor) and applicable layers. Includes saved options even when a layer does not use them. Output size, background color and the Latest folder are device settings and are not included; older PNGs that still list them import with those values ignored (their size must match the picture). |
| `no_data_color` | Copernicus mosaic fill as `#RRGGBB`, `transparent` (PNG alpha), `blur` (nearby image spread into the gaps, far away the whole image's average) or `blur_edge` (**Edge Blur**: far away the gap border's colors), defaulting to `blur` (white in records saved before it existed); also saved at `profile_settings.sources.copernicus.no_data_color`. Only source-masked gaps are affected. |
| `scene_no_data_color` | Regular (non-mosaic) Copernicus layers: `transparent` (map background), `#RRGGBB`, `blur` (the default) or `blur_edge`; also saved at `profile_settings.sources.copernicus.scene_no_data_color`. Absent in older records, which showed the map background, or black with the former `coverage_mode` `black`. |
| `image_size_selection` | Copernicus saved-PNG size choice (`auto` or fixed dimensions). Actual dimensions remain in the image fields; **Auto** can resolve larger than the profile's saved baseline. |
| `mosaic_contrast_percent`, `auto_brightness`, `auto_contrast` | Saved manual contrast and independent automatic-tone options, for mosaics and for regular layers with a tone rule (there also `mosaic_brightness_percent`). All chosen controls, including manual brightness, also live in `profile_settings.sources.copernicus` and profile JSON. |
| `mosaic_adjustments` | Applied tone algorithm, for mosaics and for regular layers with a tone rule: `tone-rules-v1` adds the rule letter (`rule`, a-f), the highlight knee (`highlight_knee`) and the bright area share (`bright_share`) to the `mosaic-tone-v2` fields (stretch low/high, contrast pivot, midtone gamma); older `mosaic-tone-v1` records (brightness gain, contrast factor/offset) still import. Also the upstream brightness, evaluated pixel count and scope. Measured on fully opaque satellite pixels before filling/overlays; never on labels or margins. |
| `auto_recommendation`, `auto_priority`, `auto_precise`, `auto_choice` | Only with **Use auto recommendation**: the priority (`fewest_clouds`, `newest` or `full_coverage`), whether regular layers were measured (**Always use precise check**) and the settings the rule took for this picture (`Saved settings` when it found none). The date, Gap fill and cloud fields above then describe the rendered picture, not the profile's saved values, and profile import does not compare them. |
| `rendered_image_sha256` | Informational digest of the rendered PNG bytes before metadata embedding. It is not checked during profile import, is not a checksum of the final file and is not a digital signature. |

In the saved settings, `date_mode = "catalogue"` with `date = "latest"` means
**Latest available**; a specific date means a fixed selection. `relative_quarter`
or `relative_month` stores the corresponding **Relative to now** mode. An offset
of zero means the current calendar quarter/month, not the newest published one.
On later profile import, relative selections advance with the calendar; they
are not frozen to the image's old `resolved_date`.

UUIDs inside records and the profile library use 32 hexadecimal characters.
Individual JSON export filenames format the same UUID with hyphens for readability.
Normal profile imports retain the stored UUID and name. A matching UUID triggers
a conflict question; **Import as copy** deliberately creates a new UUID and a copy
name. A different UUID with an already used name is added as a separate profile
named with " (Imported)", " (Imported 2)" and so on. Legacy records without an ID can receive one, but malformed IDs
are rejected. Full settings-and-profiles backup restore also retains saved UUIDs.

New readable `latest`/history filenames are derived from the embedded record:
the original UTC generation time, the profile name (or location) and a
12-character image hash, which is not the profile UUID. Source, product,
acquisition time and size are only in the record. Full metadata is unchanged by
naming or archiving; cached images keep full hash names.
See [Image filenames](USER_GUIDE.md#image-filenames-in-latest-history-and-cache)
for the scheme, length limits, unknown-time markers and legacy behavior.

Per-monitor wallpaper PNGs preserve the source profile but add
`image_role = "wallpaper_derivative"`, `profile_image_size`, and
`wallpaper_position`. Their `width`/`height` describe the actual wallpaper canvas;
`profile_image_size` describes the original profile image. Import restores that
source profile, not the global desktop layout. The source-render digest is omitted
from these derivatives.

## Data coverage

Example: `"data_coverage_percent": 98.42`. The UI formats this as
"Data coverage: 98.42%". It is separate from transfer progress and the configured
maximum cloud cover. The matching JSON in PNG text and EXIF contains:

```json
{
  "data_coverage_percent": 98.42,
  "data_coverage": {
    "method": "copernicus_data_mask",
    "valid_pixels": 9842,
    "evaluated_pixels": 10000,
    "mask_width": 100,
    "mask_height": 100,
    "scope": "satellite_view_before_overlays"
  }
}
```

The percentage is `valid_pixels / evaluated_pixels * 100`, rounded to two
decimal places. Counts refer to the satellite render grid before any final
resizing, basemap, attribution or map labels; gap filling has already completed.
Supported Copernicus evalscripts put their binary `dataMask` in the alpha channel;
for the RGB-only Landsat **Wildfires** layer MarbleScape appends it to the request.
Opaque black pixels count as valid, transparent pixels do not. Non-binary/style
opacity is not accepted as a valid-data mask. This is not cloud-free coverage.

Other providers currently lack an independently verified mask in this render
pipeline and explicitly store `null` plus `{"method":"unavailable"}`. In
particular, black space around a globe is not counted as missing imagery by a
color heuristic. If globe coverage is supported later, the expected Earth
footprint must first define the evaluated area.

Import validates the percentage against counts, mask dimensions and method and
compares PNG text with EXIF. It does not claim to authenticate the original
satellite pixels. Wallpaper derivatives retain this source-view coverage, not a
new measurement of their cropped desktop canvas. Schema-3 images remain readable
without coverage fields; the examples directory is unchanged.

Read these fields through the same ExifTool JSON extraction commands below;
they are fields inside `ImageDescription`, not separate EXIF tags.

## Prepare ExifTool

ExifTool is an optional external inspection tool. It is not bundled with
MarbleScape and is not needed for MarbleScape's own PNG profile import.

Download it from [exiftool.org](https://exiftool.org/) and follow the
[official installation instructions](https://exiftool.org/install.html).
For the Windows executable package, extract the entire archive, rename
`exiftool(-k).exe` to `exiftool.exe`, and keep its `exiftool_files` folder beside
it. Do not copy only the EXE.

The examples below assume `exiftool` is on PATH and the terminal is in the folder
containing your image. In PowerShell, an executable in the current folder can
instead be invoked as `./exiftool.exe`; a quoted full path needs `&`:

```powershell
& "C:\Tools\ExifTool\exiftool.exe" -ver
```

Use your actual image path instead of `image.png`. The normal examples also work
in macOS/Linux shells with their own paths; the JSON-parsing example is PowerShell.
The [official command-line examples](https://exiftool.org/examples.html) cover
additional workflows. The [application manual](https://exiftool.org/exiftool_pod.html#READING-EXAMPLES)
documents the read options used here.

## Read metadata without changing the PNG

### Inspect all tags or only MarbleScape's records

```powershell
exiftool -a -G1 -s "image.png"
exiftool -G1 -s -PNG:MarbleScape -EXIF:ImageDescription -EXIF:Software "image.png"
```

`-a` includes duplicate tags, `-G1` identifies their groups, and `-s` uses tag
names rather than descriptive labels. Standard image viewers or Windows Explorer
may not show the custom text record even though ExifTool can read it.

### Extract the JSON value

Run either command separately; each prints one record without tag labels:

```powershell
exiftool -b -EXIF:ImageDescription "image.png"
exiftool -b -PNG:MarbleScape "image.png"
```

Do not combine both selectors with `-b` when expecting one JSON object: that
would concatenate two records. Reading the ASCII-safe EXIF copy is convenient
when a shell has problems displaying UTF-8 text.

### Read individual profile fields in PowerShell

```powershell
$metadataJson = exiftool -b -EXIF:ImageDescription "image.png"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace(($metadataJson -join "`n"))) {
    throw "ExifTool could not read a metadata record. Check the image and the tag listing."
}
$metadata = ($metadataJson -join "`n") | ConvertFrom-Json
$metadata | Select-Object profile_name, profile_id, source, generated_at_utc, mosaic_period
$metadata.profile_settings.output | Format-List
$metadata.profile_settings.sources.copernicus | Format-List
```

The last line is relevant only to Copernicus images; other sources have their own
entry under `profile_settings.sources`. JSON parsing decodes Unicode escapes.

### Save a separate metadata report

```powershell
exiftool -b -EXIF:ImageDescription -w .marblescape-metadata.json "image.png"
```

This creates `image.marblescape-metadata.json` beside the image and leaves the
PNG unchanged. ExifTool's plain `-w` refuses to overwrite an existing output.
For a folder and its subfolders, writing one report per PNG:

```powershell
exiftool -r -ext png -b -EXIF:ImageDescription -w .marblescape-metadata.json "content/history"
```

These reports are not MarbleScape profile-export JSON files. Likewise,
ExifTool's `-j` output is its own tag-report format, with the embedded record
still a string. To import a profile, select the original PNG directly in
MarbleScape, or use **Export profile** to create the supported JSON format.

### Check structural warnings

```powershell
exiftool -validate -warning -error -a -G1 "image.png"
```

PNG CRC checking is enabled by ExifTool's validation option. Examine warnings
in context: ExifTool's checks do not establish that two JSON records agree, that
a profile is complete, or that a satellite observation is authentic. MarbleScape
performs its own profile-import validation as well.

## Import, troubleshooting and sharing

### Importing a profile from a PNG

In **Profiles** > **Import profile**, select one or more original PNGs
and/or MarbleScape profile-export JSON files. All selected files are checked
first; valid profiles are imported and failures are reported per file or profile.
General import behavior, limits, conflicts and the repair prompt are described in
the [profile-transfer reference](USER_GUIDE.md#import-and-export-profiles).

#### What a PNG import checks

- **Image:** readable pixels and dimensions; the final IEND block must have zero
  payload, a valid CRC and no trailing data.
- **Record:** supported schema and complete required settings. If both the
  MarbleScape text and EXIF records exist, they must describe the same JSON; a
  complete record in either place can be read. A conflicting or malformed record
  is never silently repaired.
- **Descriptions versus settings:** source and selection fields must agree with the
  stored profile (Copernicus product/layer IDs via their catalogue names), as must
  the descriptive No-data color, image size and tonal records. Fixed Copernicus
  sizes must match the PNG dimensions (or `profile_image_size` for a wallpaper
  derivative).
- **Mosaic periods:** period labels must match the recorded resolved date, and
  fixed selections that same period. Relative quarter/month selections are not
  compared with today's target, so older images stay importable with their rolling
  mode.
- **Tonal records:** applied tonal adjustments are validated for structure, finite
  ranges, algorithm and consistency with the manual/Auto choices. They are render
  provenance, not editable settings.

These checks detect structural corruption and contradictory settings, not image
authenticity; `rendered_image_sha256` stays informational. The **Image** header's
"(modified)" suffix is never part of the embedded profile name.

#### Older records

Optional descriptive fields missing from older records are not invented. Settings
introduced later use their backward-compatible values (mosaic No-data color white
`#FFFFFF`, contrast 100%, **Auto** off, image size **Auto**, Himawari shorelines and
centring off; see the
[full list](USER_GUIDE.md#repairing-older-profiles)); an explicitly supplied
malformed value is rejected. Missing legacy Copernicus time-selection fields can be
completed only with explicit consent when their meaning is unambiguous; the saved
date and pixels stay unchanged. Missing essential rendering data, conflicting
metadata and corrupt images stay blocked, even after **Yes to all** for other
profiles. PNG imports restore the actual image dimensions as the output baseline
while keeping the selected Auto/fixed size mode.

#### Transparent PNGs and wallpaper copies

Transparent mosaic PNGs keep their alpha channel through resizing, metadata
embedding, Latest/History/cache storage and transfer; labels and attribution may
still be drawn over gaps. For detected displays, Windows wallpaper copies are
flattened against the selected background color, while the original files stay
transparent. Such copies are marked `image_role = wallpaper_derivative` and keep
the original profile's transparency setting.

### Troubleshooting

| Problem | Likely cause | What to do |
|---|---|---|
| No MarbleScape record found | Screenshots, older images, WebP/JPEG conversions and image editors may lack or discard the data. | Use the original, newly generated PNG. |
| Metadata readable, but import rejected | The record is incomplete or contradictory; a few descriptive fields cannot reconstruct a profile safely. | Check `schema_version`, the name, complete `profile_settings` and the error message. |
| Different text and EXIF values | Only one of the two copies was edited. | Keep the original and correct the profile in MarbleScape before generating a new image. |
| Empty date, GPS or camera tags | MarbleScape stores its data as JSON, not in these tags. | Inspect the JSON fields. The file modification time is not the acquisition time, and map-centre coordinates are not a georeferenced pixel grid. |

All inspection commands above leave the PNG untouched; `-w` writes separate
reports. ExifTool can also change or delete metadata, but that is not needed here;
never run write examples on your only copy of an image.

### Sharing and privacy

MarbleScape's record excludes OAuth credentials, tokens and local storage paths,
but names, UUIDs and locations can still reveal private information. ExifTool's
general reports can also show local filenames, directories and file timestamps.
Review reports as well as images before sharing. Removing metadata or converting
the format can prevent a later profile import; keep a private original. See
[Privacy and network access](PRIVACY_AND_NETWORK.md).
