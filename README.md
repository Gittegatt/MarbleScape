# MarbleScape — Satellite live imagery for your desktop.

> This is an unofficial third-party utility. It is not affiliated with or
> endorsed by EUMETSAT, NOAA, NICT, JMA, CIRA/CSU, NASA, or the Copernicus Data Space Ecosystem.

<br>

<p align="center">
  <img src="assets/examples/example_marblescape_globe_geocolor.webp" width="49%">
  <img src="assets/examples/example_marblescape_globe_truecolor.webp" width="49%">
</p>

<p align="center">
  <img src="assets/examples/example_marblescape_europe_geocolor.webp" width="49%">
  <img src="assets/examples/example_marblescape_europe_truecolor.webp" width="49%">
</p>

MarbleScape Wallpaper downloads current satellite imagery from EUMETSAT
EUMETView WMS, the NOAA STAR GOES Image Viewer, the NICT/JMA Himawari viewers,
the CIRA SLIDER tile service, NASA Worldview/GIBS, or the Copernicus Data Space Sentinel Hub APIs. NOAA includes GOES-East,
GOES-West, and Solar/Sun SUVI imagery. Himawari includes NICT full-disk, Japan,
and AHI-band imagery plus JMA regional and target-area products. Copernicus
includes visualizable Sentinel-1, Sentinel-2, Sentinel-3,
Sentinel-5P, Copernicus DEM, and Landsat 8/9 datasets exposed by the official
Copernicus Browser catalogue. MarbleScape can set the result as the Windows
desktop wallpaper. The downloader also runs on Linux, while the tray menu,
startup integration, and automatic wallpaper update are Windows-only.

## Privacy and network access

The application does not contain analytics, telemetry, advertising, or built-in
API keys. It stores configuration and downloaded images locally. User-provided
Copernicus OAuth credentials are used only for direct Copernicus Data Space API
requests as described below.

Network access depends on the selected source. EUMETSAT uses the WMS
endpoint configured in `marblescape_config.toml`, which defaults to:

```text
https://view.eumetsat.int/geoserver/wms
```

Its dependent theme, mission, product-type, and layer lists use the public
EUMETView product metadata at `view.eumetsat.int` and the EUMETSAT Product
Navigator API at `api.eumetsat.int`. No EUMETSAT account or API key is required.

GOES-East, GOES-West, and Solar/Sun use the official NOAA STAR catalog
and image hosts:

```text
https://www.star.nesdis.noaa.gov
https://cdn.star.nesdis.noaa.gov
```

The NOAA catalog supplies available areas, products, image sizes, and
latest image links. The Windows application warms these catalogs in the
background at startup, including when EUMETSAT is selected. This downloads
metadata only. No NOAA account or API key is required.

Himawari uses the official NICT and JMA public image services:

```text
https://himawari8.nict.go.jp
https://jh190005-4.kudpc.kyoto-u.ac.jp/himawari
https://ds.data.jma.go.jp/mscweb/data/himawari
```

The shared startup catalogue job also warms Himawari metadata. No Himawari
account or API key is required. Image pixels are downloaded only when Himawari
is selected or an active rotation profile uses it.

CIRA SLIDER uses its public catalogue, latest-time metadata, and PNG tile host:

```text
https://slider.cira.colostate.edu
```

The shared startup catalogue job warms SLIDER's satellite, sector, product, and
source-size metadata. No CIRA account or API key is required. Product pixels are
downloaded only when CIRA SLIDER is selected or an active rotation profile uses
it. MarbleScape does not request SLIDER's separate map or latitude/longitude
overlays, so its wallpapers contain the clean product imagery.

NASA Worldview imagery is accessed through the public Global Imagery Browse
Services (GIBS) WMTS catalogue, time-domain, and WMS endpoints:

```text
https://gibs.earthdata.nasa.gov/wmts/epsg4326/best
https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi
```

The shared startup job downloads the GIBS capabilities metadata so its current
visualization layers, dates, and render sizes are available in Settings. This
metadata document is several megabytes; imagery pixels are requested only when
NASA Worldview is selected or used by an active rotation profile. No NASA
account or API key is required. MarbleScape requests the selected data layer
without Worldview's separate map labels, borders, or coordinate overlays.

Copernicus uses the official identity, STAC Catalog, and Process endpoints:

```text
https://identity.dataspace.copernicus.eu
https://sh.dataspace.copernicus.eu/catalog/v1/search
https://sh.dataspace.copernicus.eu/process/v1
https://gisco-services.ec.europa.eu
```

Copernicus rendering requires a free Sentinel Hub OAuth client created under
`User Settings > OAuth clients` in the
[Copernicus Data Space Sentinel Hub portal](https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings).
MarbleScape uses the CDSE OAuth, Catalog, and Process APIs directly. It does not
use Planet Insights, require a Planet subscription, or require a Configuration
Instance. Configuration Instances are needed for OGC services such as WMS/WCS;
MarbleScape sends the selected visualization as an inline Process API evalscript.
The portal's **Credits** view displays the effective quota assigned to the
account. Quota assignments can differ from the public General Users table. For
example, the account used during development showed 30,000 monthly requests and
30,000 monthly Processing Units in September 2026. These values describe that
account rather than a guaranteed allowance for every user. The Client ID is
stored in the configuration; on Windows the Client secret is encrypted for
the current Windows user with DPAPI. It can instead be supplied through
`MARBLESCAPE_COPERNICUS_CLIENT_ID` and
`MARBLESCAPE_COPERNICUS_CLIENT_SECRET`. GISCO map tiles are requested only
when the Copernicus background or labels require them.

The OAuth client provides authentication only. Its API access, rate limits,
monthly request allowance, and Processing Unit (PU) allowance are inherited
from the associated CDSE user account and account type. Creating another OAuth
client does not provide another quota. For the active account, the values shown
in its Credits view are authoritative. The
[current CDSE quota table](https://documentation.dataspace.copernicus.eu/Quotas.html)
describes the general account categories; service limits and individual
assignments can change.

Catalogue searches and image processing use the account separately. Processing
cost depends on factors including requested pixels, input bands, temporal
samples, and processing options. A wallpaper larger than one Process API request
is assembled from multiple requests, so higher output resolutions generally use
more requests and PUs. Availability in the wider Copernicus Data Space catalogue
also does not guarantee that every mission or product can be processed through
Sentinel Hub. The available Sentinel Hub collections, their processing options,
and the user's permissions determine what MarbleScape can render. Some services,
including Batch Processing V2, are unavailable to Copernicus General Users.
When access is denied, a rate or monthly quota is exhausted, or a collection is
unavailable, MarbleScape reports the source error and keeps the previous
wallpaper instead of replacing it with an incomplete result.

Console output may contain local configuration and output paths. Remove or
redact those paths before publishing diagnostic logs.

The Windows menu item `Start with Windows` stores the local launch command in:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

The command includes the current installation and the active `--config` path,
with Windows path quoting. Temporary flags such as `--once` are excluded.
Settings reports startup enabled only when the registration matches this
launch target and configuration. Registry changes are rolled back if saving
the accompanying configuration fails.

## Run the Windows executable

A reviewed prebuilt package supports 64-bit Windows 10 and 11 and does not
require a separate Python installation. Publish a newly built package only
after completing the distribution review documented below.

1. Download a published, reviewed `MarbleScape-windows-x64.zip` and
   `SHA256SUMS.txt` from
   [GitHub Releases](https://github.com/Gittegatt/MarbleScape/releases).
2. Extract the complete ZIP archive into a user-writable folder. Do not run the
   application from inside the ZIP archive.
3. Optionally verify the download before starting it:

   ```powershell
   Get-FileHash .\MarbleScape-windows-x64.zip -Algorithm SHA256
   Get-Content .\SHA256SUMS.txt
   ```

   The displayed ZIP hash must match the corresponding line in
   `SHA256SUMS.txt`.
4. Run `marblescape.exe`. The executable is not code-signed, so Windows may show
   an unrecognized-publisher warning. Continue only after obtaining the file
   from a trusted release and verifying its checksum.
5. Find the MarbleScape icon in the Windows notification area. It may be inside
   the overflow menu behind the up-arrow. Right-click the icon and select
   `Settings...` to configure the image.

The tray icon appears immediately. The first wallpaper is applied after the
selected source has been checked and its image download has completed. Use
`Open image folder` to inspect the generated image, `Start with Windows` to
enable per-user startup, and `Exit` to stop the application.

## Run from source

Source operation requires:

- Python 3.11 or newer;
- Pillow 10.0 or newer;
- pystray 0.19.5 or newer;
- Windows 10 or 11 for tray and wallpaper integration.

Install the dependencies and start the application:

```powershell
python -m pip install -r requirements.txt
python marblescape_download.py
```

If `marblescape_config.toml` is missing, the application automatically creates it
from a legacy `earthscape_config.toml`, `satscape_config.toml`, or
`eumetview_config.toml` (in that order) if available, otherwise from the neutral
`marblescape_config.example.toml` template. On Windows,
`start.bat` performs the same source-based start and keeps a console window open
for messages.

Useful command-line options:

```powershell
python marblescape_download.py --list-layers geo
python marblescape_download.py --export-layers marblescape_layers.json
python marblescape_download.py --validate-config
python marblescape_download.py --print-urls
python marblescape_download.py --once
python marblescape_download.py --version
```

## Linux usage

Linux supports image downloading and local composition, but not the Windows
tray menu, Windows startup integration, or automatic wallpaper application.

```bash
python3 -m pip install -r requirements.txt
python3 marblescape_download.py --once
```

Remove `--once` to keep checking for new imagery until the process is stopped.
The output location is controlled by `linux_root` in
`marblescape_config.toml`.

## Windows tray menu

During normal continuous operation, a MarbleScape icon appears in the Windows system tray area. Its menu provides access to:

- the current image folder;
- the settings dialog for common image, wallpaper and history options;
- a forced image refresh and current activity status;
- per-user Windows startup;
- restart and exit.

`Open image folder` appears directly above `Settings...` in the first menu
section. **Profile rotation** can be switched on or off in its own tray section
above **Force loading new picture**. **Support this project...** opens a small
dialog with monochrome buttons for starring the GitHub repository, Ko-fi, and
Buy Me a Coffee. Changes made in Settings are written to the active local TOML file and
applied by the running application without restarting it. A new image is
requested when required. Double-clicking the tray icon opens Settings.
Diagnostic and one-shot commands do not show a tray icon. `Restart` remains
available for troubleshooting.

The lower menu shows `Checking for new image...`, `Fetching new image...`,
or `Standing by...`, followed by `Next check` in the selected display time zone.
`Start with Windows` is in the bottom section above `Restart`.

**Force loading new picture** is available both in the tray menu and in
Settings under **Image**. While MarbleScape is standing by, either control
requests an immediate download even when the provider timestamp has not
changed. Identical content does not create a duplicate history image.

## Settings

In Settings, the read-only `Status and storage` section displays the current
image size, latest image count, estimated history count, maximum total image
count, estimated storage, current latest/cache/history image-file usage, and next
check time. Usage is shown in MB, switching to GB at 1,000 MB. Estimates use
the saved, active configuration and current image size; unsaved edits do not
affect them. Time-based retention estimates assume a new image each cycle.

Settings is organized into eight tabs:

- **General:** Wallpaper, global display time zone, and Updates.
- **Image:** Source, source-specific image selection, Force loading new picture, View, and Output.
- **Download:** Transfer speed unit, progress text, progress-bar visibility,
  and completed-status retention.
- **Profiles & Rotation:** Named Image snapshots, their order, and the rotation interval.
- **History & Storage:** History configuration and current storage status.
- **Backup:** JSON import and export.
- **Sources:** Visible, clickable URLs for every official imagery, catalogue,
  authentication, processing, and map service used by MarbleScape, grouped by provider.
- **About:** Installed version, project link, imagery notice, and a manual GitHub update check.

The update check contacts GitHub only when the button is pressed and compares
the installed version with the newest public release or tag. A private
repository cannot be checked by the application without GitHub authentication;
in that case About reports that no public version is accessible and the project
button can still open the repository in the user's signed-in browser.

General > **Date and time** provides **System time (recommended)** and **UTC**.
System time follows the Windows time zone and daylight-saving rules; displayed
local values include their effective UTC offset. This choice affects displayed
acquisition and next-check times only. Provider requests, comparisons, and
cached timestamps remain in UTC.

The resizable window opens centered at approximately 800 x 700 logical pixels,
adjusted for Windows scaling and the available desktop work area. Each tab
scrolls vertically when needed, using its scrollbar or the mouse wheel.
Keyboard navigation reveals focused settings automatically. Activity status,
the single-line **Next check** timestamp, and **Apply / OK / Cancel** stay
visible outside the scrolling area. The timestamp has reserved width to avoid
moving the buttons when its value changes.

During an image transfer, the footer can show the current download speed,
transferred size, percentage, and a progress bar. The Download tab controls
each part independently and offers Automatic, KB/s, MB/s, and Mbit/s for the
speed. An exact total and percentage are available when the server supplies a
valid `Content-Length` for the complete transfer. For dynamic tiled or
multi-request images, MarbleScape shows the transferred amount and an
indeterminate bar until the aggregate total is known. It does not make extra
requests merely to determine a size. **Keep completed download visible until
next download** retains the successful 100% result in the footer; when disabled,
the result disappears after a short delay. **Cancel download** is enabled in
the fixed footer while an image transfer can still be stopped. Cancelling keeps
the current wallpaper, discards the unfinished result, and creates no History
or profile-cache entry. A cancelled rotation download does not use one of its
three failure attempts; that profile becomes eligible again at the next regular
rotation interval.

The Image tab selects the imagery source: **EUMETSAT**, **GOES-East**,
**GOES-West**, **Solar (SUVI)**, **Himawari**, **CIRA SLIDER**, **NASA Worldview**, or **Copernicus Browser**. For NOAA,
Himawari, and SLIDER sources, choose a category/satellite, area/sector,
product/layer, and source resolution
from dependent dropdowns.
Only choices offered for the selected area and product are listed.
Solar uses the available SUVI wavelength products. Each source retains
its own image selection when switching sources.

For NASA Worldview, choose a layer category, imagery layer, latest or fixed
date, and render resolution. The default is the latest available VIIRS
NOAA-20 Corrected Reflectance True Color layer.

Available still-image sources default to a GeoColor/GeoColour visualization
where the provider offers one. Sources that use another name default to the
closest natural or true-color product. Solar remains on its wavelength product
because GeoColor does not apply to the Sun.

The separate Output section provides wallpaper resolution and aspect-ratio
presets. Selecting a preset fills the editable width, height, and ratio
fields; choose `Custom` or edit those fields directly for another size.
The NOAA, Himawari, or CIRA SLIDER source resolution controls the downloaded image, while Output
controls the resulting wallpaper dimensions. Fit mode, zoom, and background
color determine its framing. EUMETSAT projection, layer composition,
render quality, and TrueColor night controls apply only to EUMETSAT.
NASA Worldview's Render resolution controls the global GIBS WMS image supplied
before the same fit/crop, zoom, background, and Output settings are applied.

For NOAA, Himawari, and CIRA SLIDER, MarbleScape lists every safely renderable source resolution advertised by
the selected provider, including very large images. A listed resolution
confirms that the provider offers that variant, but it does not guarantee that
every download will finish: large transfers take longer and can be interrupted
by the image server, a network timeout, or a connection reset. If this happens
repeatedly, select a smaller source resolution and try again.

**Automatic (recommended)** is the default source/render resolution. It chooses
the smallest advertised image that can satisfy the configured Output size,
fit/crop mode, and zoom without enlarging the visible source pixels. When no
exact size exists it selects the next sufficient size; when no listed size is
large enough it uses the largest one. Manual sizes and **Largest available**
remain selectable. This reduces bandwidth, memory use, and provider load while
retaining the detail the wallpaper can display.

EUMETSAT view presets appear in Image > Source, directly below Image source. Selecting
one fills its satellite layer, projection, fit mode, and zoom defaults;
individual values can then be adjusted. **Apply** saves and activates changes
while keeping Settings open, **OK** applies and closes the window, and **Cancel**
discards changes made since the last Apply.

Image **Fit mode** determines framing inside the generated output file:
`fit` retains the view, while `crop` fills the output and trims edges.
General > Wallpaper > **Position** applies Windows' center, tile, stretch,
fit, fill, or span mode to that finished file. These modes can look identical
when the file already matches the desktop dimensions. A position change uses
the existing local latest image without requesting a new source image or image
URL. It also works when the source is temporarily unavailable and leaves the
regular image-check schedule intact.

### Image profiles and rotation

In **Profiles & Rotation**, give the current Image settings a name and select
**Add current image**. Select an existing entry to update, rename, delete,
or load it into Image. **Apply profile** next to **Load into Image** saves and
activates the selected profile immediately and requests its image. Use Move
up/down to choose the rotation order.
These changes remain drafts until **Apply** or **OK**; Cancel discards drafts.
Loading a profile fills the Image form; Apply requests its image.

The compact profile list shows **Profile name**, **Source**, **Selection**, and
**Time**. The successfully applied rotation entry is marked **Active**. Latest
profiles show the acquisition time of their last successfully used provider
image in the global display time zone, for example
`Latest · YYYY-MM-DD HH:MM UTC+02:00`, or `Latest · not loaded yet` before their
first successful load. A fixed Copernicus, EUMETSAT, or NASA Worldview date appears as
`Fixed · YYYY-MM-DD`. **Selected profile details** below the list expands the
saved configuration, highlight, area or coordinates, product and layer, source
and output resolution, fit/zoom settings, global Windows wallpaper position,
the acquisition time explicitly in UTC, and cache status for the selected entry.

Profiles store the source and its selections, EUMETSAT layers and view,
fit/crop, zoom, output size, render quality, background color, and custom
latest folder. They also store whether that profile checks for newer imagery.
General and History settings remain shared. The library and
rotation settings are stored in the standalone `profiles.toml` in the
application root beside `marblescape.exe` (or the Python sources). Existing
`[image_profiles]` data in an older main configuration is migrated automatically
the first time it is loaded. Profiles are included in the JSON settings backup.

Enable rotation and choose a positive interval in minutes, days, or weeks.
The first profile loads when rotation starts. After a successful load, its
full interval elapses before the next profile loads; normal image update
checks continue in between. Set wallpaper automatically must be enabled to
apply images to the Windows desktop. A failed profile load gets **three total
attempts**, with a short retry delay. After the third failure, the next profile
is tried. If every profile fails, the last wallpaper stays and rotation waits
one interval before trying the list again. Program restart begins with the
first profile; profile-list or interval changes restart the schedule.
One-shot and diagnostic commands do not run rotation.

Rotation images use a persistent content-addressed cache under
`content/cache`. A SQLite index maps each stable profile ID, its image-setting
signature, and the current provider frame signature to an immutable SHA-256
named PNG. Returning to an unchanged profile still checks lightweight provider
metadata, but it reuses the verified local PNG instead of downloading or
rendering the image again. Identical cached output can be shared by multiple
profiles. Changed image settings, changed source timestamps, missing files, or
failed integrity checks invalidate the entry. Deleted profiles are removed
from the index and unreferenced PNGs are pruned.

Image > **Image updates** can disable regular provider checks and downloads.
With checks disabled, MarbleScape downloads the selected latest image once only
when no verified local image exists for those exact image settings and output
dimensions. Later runs reuse that image without contacting the image provider.
The setting is saved independently in each image profile. **Force loading new
picture** deliberately bypasses this mode for one download.

**History & Storage > Profile image cache** reports its profile count, image
count, and size. **Clear cache** removes only profile-cache images and metadata;
it leaves normal Latest and History images intact. If a rotated profile is
active, MarbleScape requests a fresh image after clearing.

## Image sources

### Quick source selection

| Requirement | Recommended selection |
| --- | --- |
| Updates on a minutes-scale | GOES, Himawari, EUMETSAT, or CIRA SLIDER |
| Natural-looking geostationary Earth disk | EUMETSAT MTG GeoColour or CIRA SLIDER GeoColor |
| No separately rendered borders or coordinate grid | CIRA SLIDER |
| High-resolution local imagery | Copernicus Sentinel-2 L2A |
| Radar through clouds or at night | Copernicus Sentinel-1 |
| Global daily environmental imagery | NASA Worldview |
| European weather | EUMETSAT MTG |
| Weather over the Americas | GOES-East or GOES-West |
| Asia-Pacific weather | Himawari |
| Polar regions | EUMETSAT Metop/Sentinel-3 or NASA Worldview |
| A fixed historical date | Copernicus Browser, NASA Worldview, or a compatible EUMETSAT layer |
| The Sun | Solar / Sun SUVI |

### Source and use-case matrix

The following tables describe the sources and selections currently supported by
MarbleScape. Live catalogues remain authoritative when a provider changes its
satellites, sectors, products, or layer names.

| Image source | Satellites or missions | Suitable layers or products | Coverage | Projection in MarbleScape | Best suited to |
| --- | --- | --- | --- | --- | --- |
| **EUMETSAT** | MTG, MSG, Metop, Sentinel-3, and multi-mission products | GeoColour, True Color, infrared, water vapour, Air Mass, Dust, sea-surface temperature, and OLCI products | Europe, Africa, the Atlantic, and Indian Ocean; LEO products also provide global overpass strips | Selectable: Geographic, three GEOS views, Mercator, North Polar, and South Polar | European weather, geostationary Earth disks, marine and atmospheric products, and polar views |
| **GOES-East** | GOES-19 | GeoColor, infrared, water vapour, Air Mass, Dust, fire, and cloud products | North and South America and the Atlantic | Fixed NOAA view | Weather over the Americas and Atlantic, including hurricanes |
| **GOES-West** | GOES-18 | GeoColor, infrared, water vapour, Air Mass, fire, and cloud products | Western America and the eastern and central Pacific | Fixed NOAA view | The Pacific, Hawaii, and the western United States |
| **Solar / Sun** | GOES SUVI | Fe094, Fe131, Fe171, Fe195, Fe284, and Fe304 | The Sun | Fixed solar view | The solar corona, active regions, and solar events |
| **Himawari** | Himawari-9 | True Color, enhanced True Color, B13 infrared, water vapour, Dust, Ash, Air Mass, and Night Microphysics | East Asia, Australia, and the western Pacific | Native geostationary view | Asia-Pacific weather, typhoons, and volcanic ash |
| **CIRA SLIDER** | GOES-18/19, Himawari-9, GEO-KOMPSAT-2A, Meteosat/MTG, JPSS, and future catalogue entries | GeoColor and the products published for each sector | Depends on the selected satellite and sector | Fixed SLIDER sector projection | Clean product imagery without SLIDER borders, maps, or latitude/longitude lines |
| **NASA Worldview** | VIIRS NOAA-20/21, Suomi NPP, MODIS Terra/Aqua, and other GIBS missions | True Color, aerosols, fire, snow and ice, sea-surface temperature, vegetation, and atmospheric products | Global | Geographic, EPSG:4326 | Global daily imagery and thematic environmental observation |
| **Copernicus Browser** | Sentinel-1/2/3/5P, Copernicus DEM, and Landsat 8/9 | Radar, True Color, NDVI, SWIR, Moisture, atmospheric gases, sea-surface temperature, and terrain | Local or regional satellite overpasses | Web Mercator, EPSG:3857, framed with latitude, longitude, and map zoom | High-resolution land observation, radar, vegetation, terrain, and atmospheric products |

### Recommended source and layer by purpose

| Purpose | Recommended source | Mission or satellite | Recommended layer or product |
| --- | --- | --- | --- |
| Current natural-looking Earth view over Europe and Africa | EUMETSAT | MTG | **GeoColour** |
| Europe without separate border overlays | CIRA SLIDER | Meteosat-12 or MTG | **GeoColor**, Full Disk |
| Americas and Atlantic | GOES-East | GOES-19 | **GeoColor** |
| Western America, Pacific, or Hawaii | GOES-West | GOES-18 | **GeoColor** |
| East Asia, Australia, or western Pacific | Himawari | Himawari-9 | **NICT True Color Full Disk** |
| Clouds at night | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | **Infrared**, **B13**, or **Clean Longwave IR** |
| Water vapour and upper-level flow | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | **Water Vapor** or **Water Vapour** |
| Jet-stream structure and dry stratospheric air | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | **Air Mass RGB** |
| Fog and low cloud at night | Himawari or EUMETSAT | Himawari, MTG, or MSG | **Night Microphysics RGB** |
| Desert dust over land and sea | Himawari, GOES, or EUMETSAT | A suitable geostationary satellite | **Dust RGB** |
| Volcanic ash | Himawari or EUMETSAT | Himawari, MTG, or MSG | **Ash RGB** |
| Developing thunderstorms | GOES or Himawari | A suitable geostationary satellite | **Sandwich** or **Day Convective Storm RGB** |
| Current wildfires and thermal anomalies | GOES or NASA Worldview | GOES, VIIRS, or MODIS | A fire, hotspot, or short-wave infrared product |
| High-resolution land surface | Copernicus Browser | Sentinel-2 | **Sentinel-2 L2A · True color** |
| Local vegetation condition | Copernicus Browser | Sentinel-2 | **NDVI** |
| Soil and vegetation moisture | Copernicus Browser | Sentinel-2 | **Moisture index** or **SWIR** |
| Burn scars or smoke-penetrating views | Copernicus Browser | Sentinel-2 or Landsat | **Wildfires** or **SWIR** |
| Urban and built-up structures | Copernicus Browser | Sentinel-2 or Sentinel-1 | **False color (urban)** or **SAR urban** |
| Observation through cloud or darkness | Copernicus Browser | Sentinel-1 | **IW VV+VH · Enhanced visualization** or **RGB ratio** |
| Flood mapping | Copernicus Browser | Sentinel-1 IW | A **VV/VH Enhanced visualization** |
| Sea-surface temperature | EUMETSAT or Copernicus Browser | Sentinel-3 SLSTR | A sea-surface-temperature or SLSTR L2 layer |
| Ocean colour, algae, or suspended material | EUMETSAT or Copernicus Browser | Sentinel-3 OLCI | A chlorophyll, algal-pigment, or suspended-matter layer |
| Air pollution and atmospheric composition | Copernicus Browser | Sentinel-5P | **NO2**, **SO2**, **CO**, **CH4**, **O3**, or an aerosol layer |
| Terrain without current satellite imagery | Copernicus Browser | Copernicus DEM | **Topographic**, **Color**, or **Grayscale** |
| Global environmental overview | NASA Worldview | VIIRS, MODIS, or another GIBS mission | The matching thematic GIBS layer |
| Solar observation | Solar / Sun | GOES SUVI | **Fe171** as a general-purpose default |

Product names can differ slightly between providers. NOAA products may already
contain borders or grids in their published image pixels. Select CIRA SLIDER
when a comparable clean product without SLIDER's optional overlays is required.

### Copernicus mission guide

| Mission | Observation type | Strengths | Main limitations |
| --- | --- | --- | --- |
| **Sentinel-1** | Synthetic-aperture radar | Day and night operation, largely independent of weather, useful for water and surface structure | Does not produce a natural-colour photograph |
| **Sentinel-2 L2A** | Atmospherically corrected optical imagery | True Color, vegetation, moisture, fires, and detailed land observation | Clouds and local overpass strips; wide low-zoom views can contain large no-data areas |
| **Sentinel-2 L1C** | Top-of-atmosphere optical imagery | Less-processed optical measurements | L2A is normally the better wallpaper and land-analysis starting point |
| **Sentinel-3 OLCI** | Medium-resolution optical imagery | Ocean colour, vegetation, and wider areas | Less spatial detail than Sentinel-2 |
| **Sentinel-3 SLSTR** | Thermal and optical imagery | Land- and sea-surface temperature | Primarily thematic imagery rather than a natural photograph |
| **Sentinel-5P** | Atmospheric spectrometry | Trace gases, aerosols, and air-quality products | Coarse spatial resolution |
| **Copernicus DEM** | Digital elevation model | Terrain and relief with broad coverage | Timeless terrain data rather than a current satellite image |
| **Landsat 8/9** | Optical and thermal imagery | Land and water analysis and long historical time series | Longer revisit intervals than geostationary weather sources |

For a general Copernicus image, start with **Sentinel-2 L2A · True color**.
Map zoom describes the requested geographic extent, not the optical zoom of a
continuous global photograph. At a small map zoom, a Sentinel-2 overpass can
occupy only a tiny part of the output. This is especially noticeable with
**Fill areas without image data with black**.

### EUMETSAT projection guide

| Projection | Best suited to | Recommended missions | Notes |
| --- | --- | --- | --- |
| **Geographic — EPSG:4326** | Rectangular world and regional views | All compatible layers | The simplest choice for Europe and custom longitude/latitude extents; polar regions are distorted |
| **GEOS: MSG FES, MTG FD** | A round full-disk view centred at 0 degrees | MTG and MSG Full Earth Scan | The most natural Earth-disk view for Europe and Africa |
| **GEOS: MSG RSS** | Rapid-scan views of Europe | MSG Rapid Scanning Service | Intended for the rapid-scan sector rather than a global view |
| **GEOS: MSG IODC** | The Indian Ocean region | MSG IODC | Less suitable for a Europe-centred wallpaper |
| **Spherical Mercator — EPSG:3857** | Familiar web-map and regional views | Reprojectable layers | The poles are cropped and strongly distorted |
| **North Polar — EPSG:3995** | The Arctic | Metop, Sentinel-3, and other polar orbiters | MTG and MSG do not observe the pole |
| **South Polar — EPSG:3976** | Antarctica | Metop, Sentinel-3, and other polar orbiters | Geostationary imagery has large coverage gaps there |

The Projection control applies only to EUMETSAT. NOAA, Himawari, CIRA SLIDER,
NASA Worldview, and Copernicus use their provider-native or internally defined
projection. Reprojection changes presentation but cannot create imagery outside
a satellite's observed area.

### NOAA GOES and Solar imagery

The [official NOAA STAR GOES Image Viewer](https://www.star.nesdis.noaa.gov/goes/index.php)
provides GOES-East and GOES-West full disks, continental views, regional
sectors, Weather Forecast Office areas, mesoscale sectors, and available
storm views. Area and product availability follows the published catalog;
mesoscale and storm views can change over time. Solar/Sun is a separate
SUVI source, with wavelength products provided by NOAA.

Only JPEG and PNG still images are used. Some large NOAA products are
offered as a ZIP containing a still image; these are decoded as images.
GIF animations and videos are excluded. Size choices come from the
selected product instead of an assumed common size list.

New selections default to **Largest available**, resolved again when an image
is requested. Explicitly saved sizes remain available. A separate WMS render
quality factor is not used for NOAA: Source resolution determines available
detail, and the image is resized with Lanczos filtering to the output dimensions.
**Filter areas** (previously Find area) narrows the list within the chosen
category; it does not change the selected location. Selecting Active storms
chooses the first listed storm area. Apply saves that active selection.

**Refresh all catalogues**, to the left of Refresh catalogue, refreshes all
NOAA, Himawari, CIRA SLIDER, and NASA Worldview metadata with progress and error reporting.
It can take a while. Startup and Settings share the same in-memory cache;
opening Settings does not reload everything. NOAA area metadata expires after
five minutes; product lists and Himawari metadata are cached for 24 hours.
Each image update still
checks the latest published image independently of the catalog cache.

Some STAR products already contain boundaries, coastlines or grid lines in
their JPEG pixels. MarbleScape cannot switch those embedded lines off.
EUMETSAT supports separate optional overlay layers in the configuration. Select
CIRA SLIDER when clean source tiles without its separate border or coordinate
overlays are preferred.

NOAA selections use the latest published image available at each update
check. Source publication delays and outages can prevent a newer image
from being available. A failed download keeps the last valid wallpaper;
it does not make an older image a new observation. The update interval
sets how often MarbleScape checks, not how often NOAA produces imagery.
Large source sizes take more download time and memory; increasing the
wallpaper output size cannot add detail absent from the source.

NOAA can occasionally publish a valid but almost entirely black JPEG for one
resolution while another size contains normal imagery. The listed size remains
selectable, so it can be used again when NOAA restores its content. If the
original image on NOAA is black, select a smaller source resolution temporarily.

### Himawari imagery

The [official NICT Himawari viewer](https://himawari8.nict.go.jp/) supplies
timestamped PNG tiles for True Color Full Disk, True Color Japan, and all 16 AHI
bands. Full Disk True Color offers source sizes from 550 × 550 through
11000 × 11000, Japan through 3000 × 2400, and the band view through
5500 × 5500. AHI bands are composited over the same Blue Marble base used by
the NICT viewer. The NICT coastline overlay is a separate viewer control and is
not added to these source pixels.

The [official JMA Himawari real-time catalogue](https://ds.data.jma.go.jp/mscweb/data/himawari/index.html)
supplies Full Disk, Australia, New Zealand, Japan, Central/South/Southeast Asia,
Pacific Islands, high-resolution regional, heavy-rainfall, high-resolution
heavy-rainfall views for ten Pacific island locations, and target-area still
images. MarbleScape lists the products published for each JMA view and
validates the downloaded JPEG against that view's native dimensions. JMA
annotations already present in a published JPEG remain part of the image.

Every update resolves the provider's newest timestamp first. Only PNG and JPEG
stills are accepted; the animation and movie controls on the provider sites are
excluded. **Largest available** remains dynamic. For NICT Full Disk it currently
means a 20 × 20 grid (400 source tiles), so the highest setting can take
noticeably longer and use more network traffic. Tiles are resized directly into
the wallpaper canvas to avoid allocating an 11000 × 11000 intermediate image.
Fit mode, zoom, background color, and output size work like the NOAA sources.

**Refresh catalogue** refreshes Himawari metadata for the selected source.
**Refresh all catalogues** refreshes NOAA, Himawari, CIRA SLIDER, and NASA Worldview metadata together; it
does not download wallpaper images or query the location-specific Copernicus
acquisition list.

### CIRA SLIDER imagery

The [CIRA SLIDER viewer](https://slider.cira.colostate.edu/) provides a live
catalogue of satellites, sectors, products, product-specific tile-pyramid
levels, and latest acquisition times. MarbleScape exposes those dependent
choices as **Satellite**, **Sector**, **Product / layer**, and **Source
resolution**. This includes the currently published GOES-East, GOES-West,
Himawari-9, GEO-KOMPSAT-2A, Meteosat, MTG, and JPSS entries and follows future
catalogue changes without hard-coding the visible list.

Only the newest timestamped PNG still is used. Animations and archived playback
are excluded. Each wallpaper is assembled directly from the selected product's
PNG tiles and then processed with the common fit/crop, zoom, background, and
output-size settings. SLIDER's **Default Borders**, other maps, and **Lat/Lon**
grid are separate viewer layers. MarbleScape intentionally does not fetch them,
so both borders and coordinate lines are off for this source.

The highest available level can contain hundreds of tiles. MarbleScape caps a
full tile grid at 1,024 responses and renders tiles directly into the output
canvas to limit memory use. A high source resolution therefore retains detail
but can take considerably longer and transfer more data. Selecting a smaller
source resolution reduces the request count. **Refresh catalogue** reloads the
live SLIDER definition; the bundled fallback retains core full-disk GeoColor
choices when the catalogue endpoint is temporarily unavailable.

### NASA Worldview imagery

[NASA Worldview](https://worldview.earthdata.nasa.gov/) is powered by
[NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/access-basics/).
MarbleScape reads the official EPSG:4326 `best` WMTS capabilities document and
lists every still-image visualization with usable geographic tile-matrix
metadata. The dependent controls are **Layer category**, **Imagery layer**,
**Date / time**, and **Render resolution**. **Filter layers** searches the
current category by title or GIBS layer ID.

Time-dependent layers default to **Latest available**. Before an image check,
MarbleScape queries the layer's GIBS time domain and uses its newest available
acquisition; it falls back to the capabilities default if the small time-domain
request is temporarily unavailable. The date dropdown also exposes up to 100
recent fixed values described by the layer metadata. Layers without a time
dimension are marked **Timeless**.

The selected layer is requested as one PNG from the GIBS WMS 1.3 service over
the full geographic world extent. MarbleScape does not scrape the interactive
Worldview application, request animations, or download Worldview's separate
map labels, borders, and latitude/longitude overlays. An annotation embedded in
a provider visualization remains part of that layer's pixels.

Render sizes are derived from the layer's published tile-matrix levels and are
limited to safely processable choices up to 8,192 pixels on one axis and about
33.5 million source pixels. **Largest available** is resolved from the current
catalogue each time. The wallpaper Output size remains independent; fit/crop,
zoom, and background color are applied locally with Lanczos resampling. If a
large WMS response times out or is rejected upstream, select a smaller Render
resolution. A failed request keeps the previous wallpaper.

**Refresh catalogue** reloads the NASA GIBS capabilities for this source.
The startup and **Refresh all catalogues** jobs also warm this metadata without
downloading imagery pixels. The capabilities catalogue is cached for one hour;
the newest time for the selected layer is checked independently during normal
image checks. A small bundled true-color fallback keeps a saved
selection visible during a catalogue outage, but a live GIBS connection is
still required to download its image.

### Copernicus Browser imagery

Copernicus selections use dependent dropdowns for mission/dataset,
configuration, product, visualization layer, Browser highlight, and acquisition
date. The configuration, product, layer, and highlight choices come from a
bundled snapshot of the official Copernicus Browser catalogue. **Refresh
Copernicus catalogue** queries the live STAC Catalog API for every acquisition
date available at the current latitude/longitude and selection. **Latest
available** is the default and is resolved again before each image download.
The initial Browser selection is Sentinel-2 with Sentinel-2 L2A and True color,
matching the official Browser default.

Sentinel-1 is the radar mission. Sentinel-2, Sentinel-3, Sentinel-5P,
Copernicus DEM, and Landsat are included because the Copernicus Browser also
offers them as visual products. Enter a custom latitude and longitude and use
Map zoom to frame the output. Browser highlights load their saved product,
layer, position, zoom, and acquisition date and can then be edited.
The zoom dropdown follows the official limits of the selected dataset: 7-18
for Sentinel-1 and Sentinel-2 L2A, 10-18 for Sentinel-2 L1C, 5/6-18 for
Sentinel-3 products, 3-19 for Sentinel-5P, 7-18 for Landsat, and 7-25 for DEM.
The provider currently restricts COPERNICUS_30 DEM access to authorized CCM
users; COPERNICUS_90 remains the unrestricted DEM default on the service.

The Process API renders the selected official evalscript. **Coverage mode**
offers a single latest acquisition, black no-data areas, or a gap-filling
composite. Gap filling uses the most recent valid pixel from the selected 3,
7, 14, 30, 45, 60, or 90-day lookback; 14 days is the default for new profiles. Any area
still without imagery uses the map background. Outputs above the
Process API's 2500 × 2500 pixel request limit are split into tiles and joined
without reducing the configured wallpaper resolution. **Map labels** adds the
GISCO/OpenStreetMap place, road, POI, and boundary overlay. The black coverage
mode keeps transparent no-data pixels black. Attribution is written into
generated images whenever map tiles are used.
Temporary connection failures and retryable service responses are attempted up
to three times. Copernicus and GISCO HTTPS connections use a current Mozilla CA
bundle in addition to the operating-system certificate store. If the optional
GISCO map background or label service remains unavailable, MarbleScape keeps
the successfully rendered satellite imagery and uses black for uncovered
pixels; the map overlays are omitted and the reason is logged. A Copernicus
image or catalogue failure keeps the previous wallpaper and reports the source
as unavailable. Invalid requests are reported immediately with the service
detail so their settings can be corrected.

## Settings backups

Use the Backup tab in `Settings...` to create a JSON backup.
The backup contains the complete active TOML configuration, `profiles.toml`,
readable snapshots and integrity checksums for both files, and the current
per-user Windows startup state.

Import validates the JSON structure, checksum, and embedded TOML before
replacing and applying the active configuration. A confirmation is required.
Absolute output paths stored in a backup may need adjustment when restoring it
on another computer. A saved Copernicus Client secret is protected for the
Windows user that created it; enter that secret again after restoring on a
different computer or Windows account.

## Known imagery artifacts

MarbleScape displays provider satellite observations rather than a seamless
photographic map. Depending on the source and acquisition, an image can contain
scan seams, missing or partial coverage, day/night transitions, compression
artifacts, provider annotations, clouds, or temporarily inconsistent segments.
The application repeats this notice in Image and About so a visible source
artifact is not mistaken for a wallpaper-placement failure.

During twilight or nighttime, `MTG GeoColor` may occasionally contain hard
rectangular seams, missing tiles, or image segments that appear to overlap.
GeoColor combines daytime TrueColor imagery with a nighttime infrared cloud
visualisation over the static NASA Black Marble background. The transition and
upstream WMS mosaicking can make a temporarily inconsistent source segment
particularly noticeable.

MarbleScape does not divide the requested area into local download tiles.
It either requests a combined WMS image or downloads full-area images for
local composition, including a separate mask for the TrueColor night option.
Seams can originate in upstream imagery, but a screenshot alone does not rule
out a composition or display issue. Compare the generated PNG with the desktop
and, if needed, the individual URLs from `--print-urls`. If an artifact persists,
keep the generated image and its
timestamp, try an earlier available timestamp or another satellite layer, and
report the example to the EUMETSAT User Service Helpdesk.

For background information, see the official
[MTG GeoColor product description](https://user.eumetsat.int/catalogue/EO%3AEUM%3ADAT%3A0913)
and the [EUMETView release changelog](https://user.eumetsat.int/resources/user-guides/eumet-view-release-changelog).

## Configuration

MarbleScape was previously named EUMETView Wallpaper. Existing settings backups
and history images remain supported. On its first normal Windows launch,
MarbleScape updates a recognized legacy autostart entry for this installation
when it points into the same application folder. If you move the folder,
disable the old Windows startup entry and enable `Start with Windows` again
from the new location. Existing shortcuts must also point to the new EXE.
EUMETView is the name of EUMETSAT's data service; MarbleScape is an independent
application and is not an official EUMETSAT product.

In Settings, use `Custom latest folder` under Output or `Custom history folder`
under History to type a path or select one with `Choose...`. Empty fields use
`content/latest` and `content/history` under the configured output root. Relative
paths use the application folder. Apply or OK applies the paths; missing
folders are created when needed. On first normal use, PNG files from the
previous default `latest` and `history`
directories are moved into `content`; unrelated files and configured custom
folders are not moved. Latest and history must use different directories and
cannot point at the profile cache.

These paths are saved as `output.latest_folder` and `history.folder` and are
included in settings backups. `Open image folder` opens `content/latest` during
normal operation and the shared profile-cache folder while rotation is active.

General, source, image, download, history and storage settings are stored in
`marblescape_config.toml`. Named image profiles and rotation settings are stored
separately in `profiles.toml`. Both local files are ignored by the repository so
personal preferences are not accidentally published. The tracked
`marblescape_config.example.toml` contains neutral defaults.

Advanced options still require editing TOML: the EUMETSAT service endpoint,
archive timestamps, arbitrary WMS layer stacks/styles/opacities, custom
bounding boxes, plus the shared timeout and output-root paths. Restart after
manual file edits. Backup import applies a complete configuration, including
these advanced options.

`source.provider` selects `eumetsat`, `goes_east`, `goes_west`, `solar`,
`himawari`, `slider`, `worldview`, or `copernicus`.
The `sources.goes_east`, `sources.goes_west`, `sources.solar`, and
`sources.himawari`, `sources.slider`, and `sources.worldview` tables store each still-image source's `area`, `product`,
and `resolution`.
For Worldview, `area` is the GIBS layer ID and `product` is `latest` or a
fixed date offered by that layer.
`sources.copernicus` stores its catalogue selection, date, location, zoom, and
map options; `[copernicus]` stores its OAuth Client ID and protected secret. Existing
configurations and backups without a source selection continue to use
EUMETSAT. Existing source settings are retained when another provider is selected.
The `[download]` table stores the optional speed, size, percentage,
progress-bar, and completed-status retention preferences.

Relative output paths are resolved from the application directory, regardless
of the current working directory:

```toml
[output]
windows_root = "."                         # Application directory
# Alternatives (choose one value for windows_root):
# windows_root = "output"                  # An output subdirectory
# windows_root = 'D:\Images\MarbleScape'     # A custom absolute path
linux_root = "."
```

For a container, set `linux_root = "/output"` and mount that directory as a
volume.

When `height = 0`, the output height is calculated from `width` and
`aspect_ratio`.

### Render quality

For EUMETSAT, `Output > Render quality` provides:

- `Auto (max useful)` (default)
- `Standard (1.0×)`
- `High (1.25×)`
- `Very high (1.5×)`
- `Ultra (2.0×)`
- a custom factor of at least `1.0`

The final output resolution does not change. Higher settings request a larger
intermediate WMS image and resize it with Lanczos resampling. WMS requests are
capped at approximately 4000 pixels per axis. Outputs above that limit are
rendered proportionally within the service limit and then enlarged to the
selected size, so a quality factor above `1.0` adds no detail at 5K or 8K.
`Auto (max useful)` persists as an automatic mode and recalculates the largest
useful factor whenever the output resolution changes.
Pillow is required whenever the WMS render size differs from the final output.

### TrueColor day/night option

`Black TrueColor night side` applies only to `MTG TrueColor`. It displays
the sunlit portion of the satellite image and fills the unlit part of the Earth
disk with black. The area outside the disk retains the configured background
color.

### EUMETSAT catalogue, themes, missions, and layers

EUMETSAT uses dependent dropdowns in Image > Source for **Data theme**,
**Satellite / service**, **Mission**, **Product type**, and **Product / layer**.
Each choice restricts the following choices to combinations published together
in the official catalogue, so a mission, product type, or layer from another
service cannot remain selected accidentally. The public
EUMETView catalogue currently exposes MTG, MSG, Metop, multi-mission, and
Sentinel-3 choices. The theme filters are **Atmospheric composition**,
**Climate**, **Emergency**, **Marine**, and **Weather monitoring**; a product
may appear in more than one filter. The selectable channels, visualized
products, and RGB composites are derived from current official product metadata.
GeoColour is preferred when available. **Refresh EUMETSAT catalogue** reloads
this metadata, while **Refresh all catalogues** includes EUMETSAT together with
NOAA, Himawari, CIRA SLIDER, and NASA Worldview. Copernicus remains separate
because its acquisition catalogue depends on OAuth access and the selected
location.

For suitable LEO single-overpass layers, **Fill gaps with earlier imagery** can
place older passes underneath the newest image. The available maximum lookback
is **12 hours** or **24 hours**. MarbleScape downloads the newest image first and
only replaces its transparent No Data pixels; valid newest pixels always remain
on top. A failed optional older pass is skipped, while failure of the newest
image still fails the update normally. The resulting wallpaper can contain
several acquisition times.

This option is intentionally unavailable for GEO imagery and for products that
are already accumulated, daily, blended, climatological, or orbital-track
layers. Current eligible catalogue entries include Metop-A/B/C ASCAT and the
individual Sentinel-3A/B OLCI and SLSTR sea-surface-temperature products. Sparse
fire-detection layers are excluded because transparent pixels do not reliably
mean missing coverage there. Regional views are recommended: large global
reprojections require several WMS requests and can time out on the EUMETView
server even at a small output size.

Local composition and the Full Earth server path use the configured layer
order from bottom to top. The current regional server path reverses this
order for compatibility with its existing rendering behavior; therefore
arbitrary stacks can look different when changing render mode or preset.
Friendly basemap and overlay names are
resolved by the application. Exact WMS product names can be discovered from the
live capabilities document:

```powershell
python marblescape_download.py --list-layers
python marblescape_download.py --export-layers marblescape_layers.json
```

Each `[[layers]]` entry supports `enabled`, `opacity`, `style`, and an
optional `time`. In `render_mode = "auto"`, a single server request is used
unless per-layer opacity or different timestamps require local composition.
The TrueColor black-night option also uses local composition with a separate
Earth mask, regardless of the selected render mode. EUMETSAT LEO gap filling
also forces local composition because each acquisition needs an independent
transparent WMS response.

### EUMETSAT projections

Select a projection in the **View** section of **Settings**, or using
`view.projection` in the configuration. Settings exposes every projection
published in EUMETView:

- `Geographic` (EPSG:4326)
- `GEOS: MSG FES, MTG FD` (geostationary, centered at 0 degrees)
- `GEOS: MSG RSS` (geostationary, centered at 9.5 degrees east)
- `GEOS: MSG IODC` (geostationary, centered at 41.5 degrees east)
- `Spherical Mercator` (EPSG:3857)
- `North Polar` (EPSG:3995)
- `South Polar` (EPSG:3976)

Projection is not part of the catalogue dependency chain. Current EUMETView
layers advertise the standard geographic, Mercator, and polar CRSs, while the
three geostationary views are generated by GeoServer. EUMETView can therefore
reproject a valid selected layer into every listed MarbleScape projection.
Projection changes the view and processing cost; it does not change the mission
or create coverage outside the source observations.

Areas outside the selected layer's image coverage may display the
basemap instead of satellite imagery. The short Settings hint reads:
"Coverage depends on the selected satellite layer."

Definitions follow the [EUMETSAT viewer configuration](https://view.eumetsat.int/assets/data/config.json).
The former `view.show_extended_projections` and
`view.unlock_experimental_projections` keys remain accepted for configuration
compatibility, but the UI no longer hides official projections behind a toggle.

Changing projection in the UI selects `full_earth`, resets zoom to `1.1`, and
uses `fit` (`crop` for Geographic, to stay within valid latitude/longitude
bounds). Here, Full Earth means the projection's default overview, not a
guarantee of global satellite coverage. The selected satellite layer is kept.
Settings are saved in the active TOML file and included in JSON backups.

Existing regional presets still use Geographic; projected regional presets
are not included yet. For a custom projected view, specify `bbox` in meters
in the selected CRS. Geographic uses degrees. A projection cannot add missing
satellite coverage: some regions, especially the poles, may be blank with MTG.
Choose a layer that covers the area you want to display.
South Polar uses the Antarctic CRS EPSG:3976, not the Arctic EPSG:3995.
With MTG imagery, a black center and imagery only towards the edge can be
expected in polar views: the satellite does not observe the poles. In
particular, black-night masking also hides the underlying basemap. Selecting
a projection does not generate imagery for these missing areas.

### View presets

Available presets are:

- `full_earth`
- `europe`
- `mediterranean`
- `central_europe`
- `custom`

For `custom`, define `bbox` in logical x/y order. With the geographic
projection, use `[west, south, east, north]`. WMS 1.3.0 axis order is
handled automatically.

The default zoom is `1.1`, including all named preset profiles and projection
changes. An explicitly saved custom zoom is still respected.

## Planned features

- Support for additional imagery providers.
- More customization options for image composition.

These ideas are planned, but scope and release dates may change.

## Building release archives

This step is intended for maintainers, not for users of the prebuilt package.
On 64-bit Windows with 64-bit x86 Python and PowerShell 7, install the tested
build dependencies and run the release script:

```powershell
python -m pip install -r requirements-build.txt
pwsh -NoProfile -File .\build_release.ps1
```

The script creates separate source and Windows release-candidate archives in
`release`, plus a SHA-256 checksum file. Creating these local archives does not
clear them for publication; complete the distribution review described below
before publishing a new binary. Local configuration, shortcuts, downloaded
images, caches, and unused local assets are never copied.

The build regenerates `assets/icons/marblescape.ico` from the supplied PNGs
using `build_icon.py`. The ICO contains the original 16, 32, 48, 64, 128 and
256 px images; the tray can additionally use the 512 px PNG. Keep these
assets alongside the source when building. The EXE embeds its icon and tray
assets, so no separate icon installation is needed.

`app_version.py` defines the application version used by `--version`, the
HTTP User-Agent, and the EXE's Windows file/product version metadata.
The build generates that metadata with `build_windows_version.py`.

The executable is inside `MarbleScape-windows-x64.zip`; extract that archive
to use the new build. Building does not replace an already running installation.

The updated build script includes the matching application source ZIP under
`source/` inside the Windows package and the verified upstream pystray source
under `third_party/` in both packages. See
[Third-Party Notices](THIRD_PARTY_NOTICES.md#rebuilding-with-a-modified-pystray)
for rebuilding with a modified tray library. Existing archives must be rebuilt
to contain these materials; previous releases are not updated retroactively.

The Windows executable is not code-signed. Windows may therefore display an
unrecognized-publisher warning.

## EUMETSAT data and branding

The README includes example satellite images under `assets/examples`.
Imagery credit: EUMETSAT (MTG GeoColor/TrueColor), processed by MarbleScape
for framing, composition, resizing and WebP display. Additional contributors
and layer-specific terms may apply. The application license does not relicense
these images.
Live downloaded imagery is not included in releases. Users
must verify the licence and attribution requirements for every selected layer.
See the official EUMETSAT data registration and licensing guide and terms of
use:

- https://user.eumetsat.int/resources/user-guides/data-registration-and-licensing
- https://www.eumetsat.int/about-us/terms-use

Do not package or reuse the EUMETSAT logo or corporate visual identity without
the required permission. This project intentionally ships without an EUMETSAT
logo.

## Credits and attribution

The application and tray icon are based on
[Shape sphere](https://icon-icons.com/icon/shape-sphere/120489) by
[Zwoelf](https://icon-icons.com/authors/728-zwoelf)
([www.zwoelf.hu](https://www.zwoelf.hu/)), from the Zwicon set.
Icon-icons lists the [pack](https://icon-icons.com/pack/zwicon/1875) as
"Free for commercial use". The creator's
[usage instructions](https://www.zwicon.com/how-to-use.html) specify
[CC BY-ND 4.0](https://creativecommons.org/licenses/by-nd/4.0/): commercial use
with attribution is allowed, but distributing adapted artwork is not.
This corrects the earlier, overly permissive CC BY 4.0 attribution.

For MarbleScape, the artwork is distributed as size-specific PNG assets and
packaged into a multi-resolution Windows ICO. Credit does not imply the
creator's endorsement. The icon retains its separate CC BY-ND 4.0 terms; the
application's noncommercial restrictions do not apply to this third-party icon.
The maintainer recolored the icon turquoise using the built-in SVG editor
opened through **Edit icon** on icon-icons.com, for noncommercial use in
MarbleScape. This records the modification and its origin, not a separate
permission from the creator. Technical format conversion is allowed by
license section 2(a)(4); permission to redistribute the recolored variant
has not been established. Noncommercial use does not waive the NoDerivatives
condition, including when sharing the icon in a public repository or executable.
See [Third-Party Notices](THIRD_PARTY_NOTICES.md) and the bundled
[`licenses`](licenses) directory for dependency notices.

## License

MarbleScape is source-available under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).
The complete terms are included in [`LICENSE`](LICENSE).

Required Notice: Copyright 2026 Gittegatt

Noncommercial uses and the other permitted purposes defined in `LICENSE` are
allowed subject to its terms. Uses outside those permissions require separate
authorization from the copyright holder, except where statutory rights apply.
This is a general noncommercial license, not merely an AI-training restriction,
and it is not an OSI-approved open-source license. Third-party components
remain subject to their respective licenses.

### Outstanding distribution review

Before publishing a new binary release, clarify permission for any artistic
changes to the icon and review the example images' layer terms. Also resolve
whether the application's noncommercial terms preserve the library modification
and recombination rights required by pystray's LGPL. Source and rebuild
materials alone do not settle that licensing question. See
[Third-Party Notices](THIRD_PARTY_NOTICES.md#outstanding-licensing-review).
No additional permission or licensing exception is granted by this review.

## AI Notice

AI-assisted coding tools were used during the development of this project.

AI or machine-learning training, fine-tuning, dataset creation, and model
improvement are subject to the same PolyForm Noncommercial terms as other
uses of this source code. Commercial purposes outside the license's permitted
purposes require separate authorization. This notice adds no restrictions to
third-party material and does not override statutory exceptions.

## ☕ Support the project

If you enjoy the project and would like to support its development, a small contribution is always appreciated.

[★ Star MarbleScape on GitHub](https://github.com/Gittegatt/MarbleScape)

[![Support me on Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?style=for-the-badge&logo=ko-fi&logoColor=white)](https://ko-fi.com/gittegatt)

[Buy me a coffee](https://buymeacoffee.com/gittegatt)

## ✉️ Contact

For project information and commercial licensing inquiries, visit the
[MarbleScape repository](https://github.com/Gittegatt/MarbleScape).
