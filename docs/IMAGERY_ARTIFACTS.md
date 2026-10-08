# Satellite imagery artifacts

Expected seams, missing coverage, partial acquisitions, and ways to distinguish source artifacts from display issues.

[Back to the main README](../README.md)

## Known imagery artifacts

MarbleScape displays provider satellite observations rather than a seamless
photographic map. Depending on the source and acquisition, an image can contain
scan seams, missing or partial coverage, day/night transitions, compression
artifacts, provider annotations, clouds, or temporarily inconsistent segments.
The application repeats this notice in **Image** and **About** so a visible source
artifact is not mistaken for a wallpaper-placement failure.

During twilight or nighttime, **MTG GeoColor** may occasionally contain hard
rectangular seams, missing tiles, or image segments that appear to overlap.
GeoColor combines daytime TrueColor imagery with a nighttime infrared cloud
visualisation over the static NASA Black Marble background. The transition and
upstream WMS mosaicking can make a temporarily inconsistent source segment
particularly noticeable.

For EUMETSAT, MarbleScape does not divide the requested area into local download tiles.
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
and the [EUMETSAT viewer release changelog](https://user.eumetsat.int/resources/user-guides/eumet-view-release-changelog).

## A completely white or grey Copernicus image

A Copernicus image that is white or light grey all over, with no visible ground,
usually shows a closed cloud cover: the satellite looked down on the top of the
clouds. This is correct behavior, not a download or rendering error. Such an
image reports a data coverage of 100%, because clouds are valid image data.

It happens when **Maximum cloud cover** allows the newest acquisition although it
is almost completely cloudy. With 100%, MarbleScape takes the newest acquisition
whatever its cloud cover. Example: at Geldingadalir, Iceland, the newest
Sentinel-2 L2A acquisition on 2026-09-30 had an estimated 99.5-100% cloud cover,
so a 100% limit produced a white image, while 30% selected 2026-09-15 with 2.8%.

**Gap fill** does not help here: it fills only areas without image data (no-data
pixels), not clouds. With **Fill gaps with earlier imagery**, each pixel still
comes from the newest acquisition that has data there, which is the cloud cover.

What to do:

- Lower **Maximum cloud cover**, for example to 30%, so the newest acquisition
  with fewer clouds is chosen.
- Or choose a fixed **Date / time** with a clear view; compare the dates in the
  [Copernicus Browser](https://browser.dataspace.copernicus.eu/).
- For a cloud-free picture regardless of the weather, use a precomputed cloudless
  mosaic such as **Sentinel-2 Quarterly Mosaics**.

In regions with long cloudy seasons, such as Iceland in autumn, a high cloud limit
often yields only clouds. The cloud estimate also covers whole satellite tiles, so
a limit can still admit an acquisition that is cloudy over the requested view.

## A completely black Copernicus image

Optical layers (True color, False color, indices and similar) need daylight. A
completely black image usually has one of these causes:

- **A night acquisition.** Landsat 8/9 also passes over at night. Copernicus lists
  these passes with an unknown cloud cover of -1 but returns no pixels for them,
  not even in the thermal band; a test of 25 night passes over 7 active volcanoes
  (including Kilauea during its September 2026 eruption) found none with data.
  MarbleScape therefore skips them when it picks **Latest available** or lists
  dates. Example: at 19.6 / -155.48 on Hawaii, the newest Landsat pass was a night
  pass at 22:25 local time on 2026-09-26; MarbleScape now selects the day pass of
  2026-09-16 instead. Lava is visible in the day passes: the **Wildfires** and
  **Thermal** layers show active vents as hot spots.
- **No image data at all.** If the view has 0% data coverage (for example a date
  without an acquisition over this area, or a fixed date whose tiles all exceed
  **Maximum cloud cover**), there is nothing to show. MarbleScape does not store or
  show such a picture: the previous wallpaper stays, and the **Image** header reads
  "Update unavailable; previous image kept. No image data for this selection: ...".
  The date list itself follows the place, map zoom, picture size and cloud limit:
  after a change it reloads, and a fixed date it no longer offers becomes **Latest
  available** with a note.
- **Darkness near the poles.** In polar winter the sun stays low or below the
  horizon, so optical acquisitions there are very dark or missing.

What to do: use **Latest available** or a date from the refreshed date list,
check the data coverage, choose another date or location, or use a layer that
does not need daylight, such as Sentinel-1 radar.

## Copernicus mosaics and other tiled sources

The EUMETSAT description above does not apply to every source: Copernicus,
NICT Himawari and CIRA SLIDER can assemble an output from multiple requests or
tiles. A seam alone does not identify whether the cause is the provider's
observations, a composite or local rendering.

Precomputed cloudless mosaics may still contain residual clouds, bright terrain,
snow, source seams or transparent No Data. Gaps use the profile's
**No-data color** (default **Blur**; mosaics and regular layers each have their own);
for a regular layer **Transparent** reveals the background map.
The color picker affects only source-masked missing pixels, not valid dark pixels.
**Transparent** preserves those gaps as PNG alpha instead of filling them. A viewer
may display a checkerboard, black or white behind transparent pixels; that background
is not satellite imagery. Windows wallpaper copies use the chosen background color.
**Labels** and **Country borders** may overlay the fill; coverage is measured before both.
Dark pixels can also belong to the imagery itself; their colour alone does not
prove missing data. Compare the same source, product, layer, location and resolved
period in the original viewer before classifying an apparent gap.

For an issue report, keep the original PNG rather than only a screenshot. Its
metadata records the source selection, applicable location, period and settings.
Use [PNG metadata and ExifTool](PNG_METADATA.md) to inspect these values and
review private names/coordinates before sharing. A relative profile's current
target may differ from the period used when an older image was generated.
