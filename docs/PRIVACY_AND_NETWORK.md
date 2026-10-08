# Privacy and network access

Services contacted by MarbleScape, credential handling, quotas, and local startup registration.

[Back to the main README](../README.md)

## Contents

- [Privacy and network access](#privacy-and-network-access)
- [Image metadata and filenames](#image-metadata-and-filenames)
- [Profiles, backups and exports](#profiles-backups-and-exports)
- [Update check](#update-check)
- [Network access by image source](#network-access-by-image-source)
- [Copernicus account, credentials and quotas](#copernicus-account-credentials-and-quotas)
- [Logs and Windows startup](#logs-and-windows-startup)

## Privacy and network access

- The application contains no analytics, telemetry, advertising or built-in API
  keys.
- Configuration and downloaded images are stored locally.
- With the appearance **System**, MarbleScape reads the Windows app mode
  (`AppsUseLightTheme` under `HKCU\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize`)
  locally; nothing is sent.
- User-provided Copernicus OAuth credentials are used only for direct Copernicus
  Data Space API requests, as described [below](#copernicus-account-credentials-and-quotas).
- Network requests go only to the selected imagery services, their catalogues and
  the public GitHub release endpoint, and to the OpenStreetMap place search when you
  search in **Image** > [**Find location**](#find-location).

## Image metadata and filenames

### Embedded metadata

New PNG images carry matching `MarbleScape` text and EXIF metadata: source,
selection, applicable coordinates, profile name and UUID, image time or mosaic
period, and portable rendering settings for profile import. Profile JSON exports
use the same sanitized settings.

- **Never included:** OAuth credentials, tokens, local file paths and unrelated
  provider selections.
- **Possibly sensitive:** profile names and coordinates stay with every copy of
  the original PNG; review them before sharing.
- **Data coverage** adds pixel counts, method and a percentage, but no further
  location or account information. Measuring the Copernicus mask needs no extra
  image request.
- **Latest snapshot:** the protected local **Latest snapshot (no profile)** lives
  in `marblescape_config.toml`. Its portable settings and random snapshot/export
  identities are embedded in new PNGs, without credentials or local paths.

Use the read-only commands in [PNG metadata and ExifTool](PNG_METADATA.md) to
inspect the record before sharing. General ExifTool reports can also include local
file paths and filesystem timestamps that are not part of MarbleScape's record;
keep such reports private or redact them.

### Filenames

Readable image filenames contain the UTC creation time, a shortened profile name
or location (without a named profile, possibly map-centre coordinates) and a short
image hash. Renaming a PNG does not remove its embedded metadata; check both the
filename and the metadata before sharing. Existing images are not mass-renamed,
and cache filenames remain hashes.

## Profiles, backups and exports

- **Settings backups** contain complete settings and optionally all profiles and
  rotation; keep both variants private. Both scopes include the **Latest snapshot**
  settings (not the image).
- **Profile UUIDs** allow exported profiles and images to be correlated. Imports
  and full backup restores keep stored UUIDs; only an explicit independent copy or
  a legacy record without an ID gets a new one. Duplicate UUIDs require a conflict
  decision; a used name with a different UUID is imported as a separate profile
  named with " (Imported)". Profile import never replaces the protected snapshot row.
- **Checksums** detect corruption, not who created a file.
- **Export folders:** exports default to the local `export/settings` and
  `export/profiles` folders. The last successful destination of each is stored in
  `export/export_locations.json`, which can contain private absolute folder names.
  It is excluded from version control and release packages and never embedded in
  profiles, PNGs or settings backups. Unavailable destinations reset to the
  defaults.

## Update check

The Windows tray checks `api.github.com` for the latest public MarbleScape release
once after each manual start; silent Windows autostart opens no update notice or
Settings. **About** > **Check for updates** repeats the check manually. Only release metadata is
requested; no account login or OAuth credentials are sent. A skipped release
version is stored in `marblescape_config.toml`.

## Network access by image source

Image pixels are downloaded only for the selected source or an active rotation
profile that uses it. The shared daily catalogue job, at the saved local system
time, checks catalogue metadata of all public sources, also when another source is
selected. It uses the local clock regardless of the display time-zone preference,
stores the last successful slot in `content/catalogue_refresh_schedule.json`,
catches up a missed run at the next start and retries failed runs after five
minutes. **Downloads & Updates** > **Catalogue refresh** > **Refresh all catalogues** forces the same
checks; saving a new time does not start one. These checks download metadata, not
image pixels. None of the public sources needs an account or API key.

### EUMETSAT

The WMS endpoint configured in `marblescape_config.toml`, by default:

```text
https://view.eumetsat.int/geoserver/wms
```

Theme, mission, product-type and layer lists come from the public product metadata
at `view.eumetsat.int` and the EUMETSAT Product Navigator API at
`api.eumetsat.int`.

### NOAA GOES and Solar

GOES-East, GOES-West and Solar/Sun use the official NOAA STAR catalogue and image
hosts, which supply areas, products, image sizes and latest image links:

```text
https://www.star.nesdis.noaa.gov
https://cdn.star.nesdis.noaa.gov
```

### Himawari

The official NICT and JMA public image services:

```text
https://himawari8.nict.go.jp
https://jh190005-4.kudpc.kyoto-u.ac.jp/himawari
https://ds.data.jma.go.jp/mscweb/data/himawari
```

### CIRA SLIDER

The public catalogue, latest-time metadata and PNG tile host:

```text
https://slider.cira.colostate.edu
```

The daily job checks satellite, sector, product and source-size metadata.
SLIDER's separate map and latitude/longitude overlays are not requested.

### NASA Worldview

The public Global Imagery Browse Services (GIBS) WMTS catalogue, time-domain and
WMS endpoints:

```text
https://gibs.earthdata.nasa.gov/wmts/epsg4326/best
https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi
```

The daily job checks the capabilities metadata (several megabytes), so layers,
dates and render sizes are available in Settings; cached catalogues remain
available between refreshes and during outages. Worldview's separate labels,
borders and coordinate overlays are not requested.

### Copernicus

The official identity, STAC Catalog and Process endpoints, plus GISCO map tiles:

```text
https://identity.dataspace.copernicus.eu
https://sh.dataspace.copernicus.eu/catalog/v1/search
https://sh.dataspace.copernicus.eu/process/v1
https://gisco-services.ec.europa.eu
```

- When credentials are configured, the daily refresh also checks acquisition dates
  for saved Copernicus locations and profiles. This makes metadata and
  authentication requests, not image downloads.
- GISCO map tiles are requested only when the Copernicus background, **Labels** or
  **Country borders** need them.

- **Recommendation** > **Compare variants...** searches the same STAC Catalog for the tiles over the view of the
  last 150 days (for mosaics their newest periods), only when its window checks a place. It
  downloads metadata, not image pixels, and uses no processing units. **Precise check** and
  **Load preview** send small Process API requests for the same view and use processing units.
- **Recommendation** > **Use auto recommendation** runs that catalogue search at every image
  check of a profile that has it on; a mosaic's **Data coverage** priority also measures each new period
  once with a small Process API request, and **Always use precise check** measures a regular
  layer's view at every check the same way.

### Find location

Only when you press **Search** (or **Enter**) in **Image** > **Find location**, MarbleScape
sends the search text to the OpenStreetMap Nominatim search:

```text
https://nominatim.openstreetmap.org/search
```

- One request per search and at most one per second, identified by MarbleScape's
  User-Agent, as the
  [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/)
  asks. A search repeated while MarbleScape runs is answered from memory.
- Coordinates typed into the search field are recognized locally and not sent.
- Results are not saved; **Transfer** only fills fields in Settings. Results are
  © OpenStreetMap contributors (ODbL).
- **Preview** opens the Copernicus Browser with the place and map zoom in your web
  browser; MarbleScape itself sends nothing there.
- `[service] location_search_endpoint` in the settings file changes the service
  address (https only).

## Copernicus account, credentials and quotas

### OAuth client

Copernicus rendering needs a free Sentinel Hub OAuth client, created under
**User Settings** > **OAuth clients** in the
[Copernicus Data Space Sentinel Hub portal](https://shapps.dataspace.copernicus.eu/dashboard/#/account/settings).
MarbleScape uses the CDSE OAuth, Catalog and Process APIs directly. It does not use
Planet Insights or require a Planet subscription or a Configuration Instance
(those are needed for OGC services such as WMS/WCS); the selected visualization is
sent as an inline Process API evalscript.

- The **Client ID** is stored in the configuration.
- On Windows the **Client secret** is encrypted for the current Windows user with
  DPAPI.
- Both can instead be supplied through `MARBLESCAPE_COPERNICUS_CLIENT_ID` and
  `MARBLESCAPE_COPERNICUS_CLIENT_SECRET`.

### Quotas

The OAuth client provides authentication only. API access, rate limits, the monthly
request allowance and the Processing Unit (PU) allowance come from the associated
CDSE user account and account type; another OAuth client does not add quota.

- The portal's **Credits** view shows the effective quota of the account and is
  authoritative. Assignments can differ from the public
  [CDSE quota table](https://documentation.dataspace.copernicus.eu/Quotas.html),
  which describes the general account categories. For example, the account used
  during development showed 30,000 monthly requests and 30,000 monthly Processing
  Units in September 2026; this is not a guaranteed allowance for every user.
- Catalogue searches and image processing are counted separately. Processing cost
  depends on requested pixels, input bands, temporal samples and processing
  options. A wallpaper larger than one Process API request is assembled from several
  requests, so higher resolutions generally use more requests and PUs.

### Availability

Availability in the wider Copernicus Data Space catalogue does not guarantee that a
mission or product can be processed through Sentinel Hub; the available
collections, their options and the user's permissions decide what MarbleScape can
render. Some services, including Batch Processing V2, are unavailable to
Copernicus General Users. When access is denied, a rate or monthly quota is
exhausted or a collection is unavailable, MarbleScape reports the source error and
keeps the previous wallpaper instead of an incomplete result.

## Logs and Windows startup

MarbleScape writes what it does to `content/marblescape.log` (about 1 MB; the two
files before are kept as `.1` and `.2`). **General** > **Actions** > **Show log**
opens it. The log stays on this computer and is never sent anywhere. Copernicus
credentials and access tokens are masked in it, but it contains local configuration
and output paths, profile names and coordinates: remove or redact them before
sharing a log. The console shows the same lines when MarbleScape runs from a
terminal.

The tray item **Start minimized with Windows** stores the local launch command in:

```text
HKCU\Software\Microsoft\Windows\CurrentVersion\Run
```

- The command includes the current installation and the active `--config` path,
  with Windows path quoting; temporary flags such as `--once` are excluded.
- Settings reports startup as enabled only when the registration matches this
  launch target and configuration.
- Registry changes are rolled back if saving the accompanying configuration fails.
