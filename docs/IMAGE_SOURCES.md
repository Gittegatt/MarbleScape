# Image source guide

Provider selection, missions, satellites, layers, projections, cloud cover, and source-specific behavior.

[Back to the main README](../README.md)

## Contents

- [What the image sources show](#what-the-image-sources-show)
- [Choosing a source](#choosing-a-source)
- [Catalogues and refresh](#catalogues-and-refresh)
- [NOAA GOES and Solar imagery](#noaa-goes-and-solar-imagery)
- [Himawari imagery](#himawari-imagery)
- [CIRA SLIDER imagery](#cira-slider-imagery)
- [NASA Worldview imagery](#nasa-worldview-imagery)
- [Copernicus Browser imagery](#copernicus-browser-imagery)

## What the image sources show

MarbleScape shows satellite pictures, not a map service such as Google Maps.
Google Maps assembles aerial photographs and commercial satellite images down to
a few decimetres per pixel from acquisitions that are often months to years
old. The sources here are weather and Earth observation satellites: weather
satellites see whole continents at about 0.5-3 km per pixel, and Copernicus
Sentinel-2 shows landscapes, fields, towns and coasts at about 10 m per pixel,
not single houses or cars. A higher **Map zoom** than the source resolves only
enlarges the picture.

| Image source | Satellites | New picture | Pixel size (about) | Shows |
| --- | --- | --- | --- | --- |
| **EUMETSAT** | MTG and MSG (geostationary); Metop and Sentinel-3 (polar orbit) | every 10-15 minutes (geostationary) | 0.5-3 km; Sentinel-3 OLCI 300 m | Europe, Africa, the Atlantic and Indian Ocean: weather and clouds |
| **NOAA GOES** | GOES-19 (East), GOES-18 (West) | Full Disk every 10 minutes, CONUS 5 minutes, mesoscale sectors every minute | 0.5-2 km | The Americas, the Atlantic and the Pacific |
| **Solar / Sun** | GOES SUVI | every few minutes | - | The Sun in extreme ultraviolet |
| **Himawari** | Himawari-9 | every 10 minutes | 0.5-2 km | Asia, Australia and the western Pacific |
| **CIRA SLIDER** | GOES-18/19, Himawari-9, GEO-KOMPSAT-2A, Meteosat/MTG, JPSS | every 5-15 minutes (geostationary), per overpass (JPSS) | 0.5-2 km (geostationary) | Each satellite's view in full resolution |
| **NASA Worldview** | MODIS Terra/Aqua, VIIRS and further GIBS missions | mostly daily | 250 m-1 km for true color | The whole Earth, one mosaic per day |
| **Copernicus Browser** | Sentinel-2 | every 2-5 days | 10 m | Landscapes, fields, towns and coasts |
| | Landsat 8/9 | about every 8 days | 30 m | The same, since 2013 |
| | Sentinel-1 | every few days | 10 m (radar) | Surfaces through clouds and at night |
| | Sentinel-3 | daily | 300 m | Land, ocean colour and temperature |
| | Sentinel-5P | daily | about 5 km | Gases and aerosols in the atmosphere |
| | Quarterly and monthly mosaics | per quarter or month | 10 m (Sentinel-2) | Cloud-free composites of a period |

The values are rounded from the operators' specifications; the pixel size of
geostationary images grows towards the edge of the Earth disk.

Advantages over Google Maps:

- **Current:** minutes to days old instead of months to years.
- **Regular:** the same place again and again, so weather, seasons, floods, fires
  and snow become visible.
- **Consistent:** each picture comes from one satellite, with the same colors and
  light, instead of a patchwork of acquisitions from different years.
- **Worldwide:** including oceans and remote areas.
- **Beyond natural color:** infrared, night views and radar through clouds.
- **Freely available:** open data from public operators.

### How often each view updates

How often a new picture of a view appears at the provider. For NOAA, Himawari,
CIRA SLIDER and NASA Worldview MarbleScape uses these values to mark a late
picture "(delayed)" in the **Image** header.

| Source | View | New picture |
| --- | --- | --- |
| EUMETSAT | MTG layers | every 10 minutes |
| | MSG layers (0°, Indian Ocean) | every 15 minutes |
| | Metop, Sentinel-3 | per overpass, a few times a day |
| NOAA GOES | Full Disk, regional sectors, WFO areas, active storms | every 10 minutes |
| | CONUS / PACUS | every 5 minutes |
| | Mesoscale sectors | every minute |
| | GLM lightning products / derived motion winds | every 5 minutes / hourly |
| Solar / Sun | SUVI | about every 4 minutes |
| Himawari | Full Disk, AHI bands, active storms, JMA regions | every 10 minutes |
| | NICT Japan, JMA target area | every 2.5 minutes |
| CIRA SLIDER | Full Disk (GOES, Himawari, GEO-KOMPSAT-2A, MTG) | every 10 minutes |
| | CONUS / mesoscale, Japan, Korea | 5 / 1 / 2.5 / 2 minutes |
| | Meteosat-9/10, JPSS | 15 minutes, about every 50 minutes |
| NASA Worldview | most layers | daily |
| | other layers | monthly, 8-day or yearly; GOES/Himawari layers every 10 minutes |
| Copernicus Browser | Sentinel-2 / Landsat 8/9 | every 2-5 days / about 8 days |
| | Sentinel-3, Sentinel-5P | daily |
| | Quarterly and monthly mosaics | per quarter or month |

Weather satellites publish a picture about 10-30 minutes after it was taken,
Copernicus a scene a few hours to a day later. A view's own provider page can
differ for single products. MarbleScape checks for a new picture at the
**Update check interval** (**Downloads & Updates** > **Image update checks**).

## Choosing a source

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
| **NOAA GOES** | GOES-19 (East) or GOES-18 (West), selected in the Satellite dropdown | GeoColor, infrared, water vapour, Air Mass, Dust, fire, and cloud products | East: the Americas and Atlantic; West: western America and the eastern/central Pacific | Fixed NOAA view | Weather over the Americas or Pacific, including hurricanes |
| **Solar / Sun** | GOES SUVI | Fe094, Fe131, Fe171, Fe195, Fe284, and Fe304 | The Sun | Fixed solar view | The solar corona, active regions, and solar events |
| **Himawari** | Himawari-9 | True Color, enhanced True Color, B13 infrared, water vapour, Dust, Ash, Air Mass, and Night Microphysics | East Asia, Australia, and the western Pacific | Native geostationary view | Asia-Pacific weather, typhoons, and volcanic ash |
| **CIRA SLIDER** | GOES-18/19, Himawari-9, GEO-KOMPSAT-2A, Meteosat/MTG, JPSS, and future catalogue entries | GeoColor and the products published for each sector | Depends on the selected satellite and sector | Fixed SLIDER sector projection | Clean product imagery without SLIDER borders, maps, or latitude/longitude lines |
| **NASA Worldview** | VIIRS NOAA-20/21, Suomi NPP, MODIS Terra/Aqua, and other GIBS missions | True Color, aerosols, fire, snow and ice, sea-surface temperature, vegetation, and atmospheric products | Global | Geographic, EPSG:4326 | Global daily imagery and thematic environmental observation |
| **Copernicus Browser** | Sentinel-1/2/3/5P, Copernicus DEM, and Landsat 8/9 | Radar, True Color, NDVI, SWIR, Moisture, atmospheric gases, sea-surface temperature, and terrain | Local or regional satellite overpasses | Web Mercator, EPSG:3857, framed with latitude, longitude, and map zoom | High-resolution land observation, radar, vegetation, terrain, and atmospheric products |

### Recommended source and layer by purpose

| Purpose | Recommended source | Mission or satellite | Recommended layer or product |
| --- | --- | --- | --- |
| Current natural-looking Earth view over Europe and Africa | EUMETSAT | MTG | GeoColour |
| Europe without separate border overlays | CIRA SLIDER | Meteosat-12 or MTG | GeoColor, Full Disk |
| Americas and Atlantic | NOAA GOES → GOES-East | GOES-19 | GeoColor |
| Western America, Pacific, or Hawaii | NOAA GOES → GOES-West | GOES-18 | GeoColor |
| East Asia, Australia, or western Pacific | Himawari | Himawari-9 | NICT True Color Full Disk |
| Clouds at night | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | Infrared, B13, or Clean Longwave IR |
| Water vapour and upper-level flow | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | Water Vapor or Water Vapour |
| Jet-stream structure and dry stratospheric air | GOES, Himawari, or EUMETSAT | A suitable geostationary satellite | Air Mass RGB |
| Fog and low cloud at night | Himawari or EUMETSAT | Himawari, MTG, or MSG | Night Microphysics RGB |
| Desert dust over land and sea | Himawari, GOES, or EUMETSAT | A suitable geostationary satellite | Dust RGB |
| Volcanic ash | Himawari or EUMETSAT | Himawari, MTG, or MSG | Ash RGB |
| Developing thunderstorms | GOES or Himawari | A suitable geostationary satellite | Sandwich or Day Convective Storm RGB |
| Current wildfires and thermal anomalies | GOES or NASA Worldview | GOES, VIIRS, or MODIS | A fire, hotspot, or short-wave infrared product |
| Active lava and volcanic hot spots | Copernicus Browser | Landsat 8/9 | Wildfires or Thermal (day passes) |
| High-resolution land surface | Copernicus Browser | Sentinel-2 | Sentinel-2 L2A · True color |
| Local vegetation condition | Copernicus Browser | Sentinel-2 | NDVI |
| Soil and vegetation moisture | Copernicus Browser | Sentinel-2 | Moisture index or SWIR |
| Burn scars or smoke-penetrating views | Copernicus Browser | Sentinel-2 or Landsat | Wildfires or SWIR |
| Urban and built-up structures | Copernicus Browser | Sentinel-2 or Sentinel-1 | False color (urban) or SAR urban |
| Observation through cloud or darkness | Copernicus Browser | Sentinel-1 | IW VV+VH · Enhanced visualization or RGB ratio |
| Flood mapping | Copernicus Browser | Sentinel-1 IW | A VV/VH Enhanced visualization |
| Sea-surface temperature | EUMETSAT or Copernicus Browser | Sentinel-3 SLSTR | A sea-surface-temperature or SLSTR L2 layer |
| Ocean colour, algae, or suspended material | EUMETSAT or Copernicus Browser | Sentinel-3 OLCI | A chlorophyll, algal-pigment, or suspended-matter layer |
| Air pollution and atmospheric composition | Copernicus Browser | Sentinel-5P | NO2, SO2, CO, CH4, O3, or an aerosol layer |
| Terrain without current satellite imagery | Copernicus Browser | Copernicus DEM | Topographic, Color, or Grayscale |
| Global environmental overview | NASA Worldview | VIIRS, MODIS, or another GIBS mission | The matching thematic GIBS layer |
| Solar observation | Solar / Sun | GOES SUVI | Fe171 as a general-purpose default |

Product names can differ slightly between providers. NOAA products may already
contain borders or grids in their published image pixels; select CIRA SLIDER when
a comparable clean product without SLIDER's optional overlays is required.

### Copernicus mission guide

| Mission | Observation type | Strengths | Main limitations |
| --- | --- | --- | --- |
| **Sentinel-1** | Synthetic-aperture radar | Day and night operation, largely independent of weather, useful for water and surface structure | Does not produce a natural-colour photograph |
| **Sentinel-1 Mosaics** | Monthly radar composites | Broad-area radar views with fixed or rolling month selection | Not quarterly; availability and coverage depend on product and month |
| **Sentinel-2 Mosaics** | Precomputed optical composites, including quarterly mosaics | Broad landscape views with fixed or rolling quarter selection | Not a current single observation; cloudless does not guarantee artifact-free pixels |
| **Sentinel-2 L2A** | Atmospherically corrected optical imagery | True Color, vegetation, moisture, fires, and detailed land observation | Clouds and local overpass strips; wide low-zoom views can contain large no-data areas |
| **Sentinel-2 L1C** | Top-of-atmosphere optical imagery | Less-processed optical measurements | L2A is normally the better wallpaper and land-analysis starting point |
| **Sentinel-3 OLCI** | Medium-resolution optical imagery | Ocean colour, vegetation, and wider areas | Less spatial detail than Sentinel-2 |
| **Sentinel-3 SLSTR** | Thermal and optical imagery | Land- and sea-surface temperature | Primarily thematic imagery rather than a natural photograph |
| **Sentinel-5P** | Atmospheric spectrometry | Trace gases, aerosols, and air-quality products | Coarse spatial resolution |
| **Copernicus DEM** | Digital elevation model | Terrain and relief with broad coverage | Timeless terrain data rather than a current satellite image |
| **Landsat 8/9** | Optical and thermal imagery | Land and water analysis and long historical time series | Longer revisit intervals than geostationary weather sources; night passes carry no usable pixels |

### EUMETSAT projection guide

| Projection | Best suited to | Recommended missions | Notes |
| --- | --- | --- | --- |
| **Geographic - EPSG:4326** | Rectangular world and regional views | All compatible layers | The simplest choice for Europe and custom longitude/latitude extents; polar regions are distorted |
| **GEOS: MSG FES, MTG FD** | A round full-disk view centred at 0 degrees | MTG and MSG Full Earth Scan | The most natural Earth-disk view for Europe and Africa |
| **GEOS: MSG RSS** | Rapid-scan views of Europe | MSG Rapid Scanning Service | Intended for the rapid-scan sector rather than a global view |
| **GEOS: MSG IODC** | The Indian Ocean region | MSG IODC | Less suitable for a Europe-centred wallpaper |
| **Spherical Mercator - EPSG:3857** | Familiar web-map and regional views | Reprojectable layers | The poles are cropped and strongly distorted |
| **North Polar - EPSG:3995** | The Arctic | Metop, Sentinel-3, and other polar orbiters | MTG and MSG do not observe the pole |
| **South Polar - EPSG:3976** | Antarctica | Metop, Sentinel-3, and other polar orbiters | Geostationary imagery has large coverage gaps there |

The Projection control applies only to EUMETSAT; other sources use their
provider-native or internally defined projection. Reprojection changes the
presentation but cannot create imagery outside a satellite's observed area. For
your own region, choose the EUMETSAT preset **Custom area** (see the
[user guide](USER_GUIDE.md#custom-area)).

## Catalogues and refresh

The Image tab shows **Catalogue refresh - <image source>** below the Source
section. Its **Refresh catalogue** button reloads the selected source's catalogue;
the status line and an animated activity bar report the result: "Completed.",
or "Finished with issues." when the catalogue is unavailable or reports a notice
(the message above names it). Both appear on the left, below the message. The
section keeps its height while a refresh starts and ends: the result row and two
message lines are always reserved.

- **What a refresh loads:** for Copernicus, all available acquisition dates for
  the selected location, which can take a while; for EUMETSAT, the theme,
  service, mission, product type and layer combinations of the public EUMETSAT
  catalogue. The EUMETSAT projection is independent of the catalogue because
  the viewer reprojects the selected layer. Other sources load their areas,
  products and sizes.
- **On source selection**, saved catalogue entries appear immediately when
  available. **Refresh catalogue** and **Downloads & Updates** > **Refresh all catalogues** always
  check online at once; the daily refresh is described in the
  [user guide](USER_GUIDE.md#catalogue-refresh).
- **Disk cache:** the last successful metadata is stored in
  `content/catalogues.json`. If a provider is unavailable or returns an incomplete
  catalogue, MarbleScape reports it and uses the cache when it contains the
  source. Unchanged remote entries are kept while each source's check time is
  recorded. Cached selections stay editable where possible.
- **Memory caches:** within a running NOAA client, area metadata lives five minutes
  and product lists one day; Himawari keeps its memory cache between explicit
  refreshes; the NASA GIBS capabilities are cached for one hour. The daily refresh
  ignores these limits and checks online.
- **Copernicus:** the bundled Browser mission, product and layer catalogue needs no
  network refresh. Its acquisition dates depend on OAuth access and location:
  selecting Copernicus refreshes the date list for the current location, and the
  daily job refreshes the dates of saved Copernicus locations when credentials are
  configured.

Catalogue refreshes never download wallpaper images. Each image update checks the
latest published image independently of the catalogue cache.

## NOAA GOES and Solar imagery

### Areas and products

The [official NOAA STAR GOES Image Viewer](https://www.star.nesdis.noaa.gov/goes/index.php)
provides GOES-East and GOES-West full disks, continental views, regional sectors,
Weather Forecast Office areas, mesoscale sectors and available storm views. Area
and product availability follows the published catalogue; mesoscale and storm
views can change over time. Solar/Sun is a separate SUVI source with NOAA's
wavelength products.

- Only JPEG and PNG stills are used; some large products come as a ZIP containing
  a still image, which is decoded. GIF animations and videos are excluded.
- **Filter areas** narrows the list within the chosen category without changing
  the selected location; **Clear** beside it empties the filter (**Filter sectors** and
  **Filter imagery layers** for SLIDER and Worldview work the same way). Selecting **Active storms** chooses the first listed storm
  area and shows a note below **Area / location**. **Apply Image** saves the active selection.
- Active storms are temporary. Their list is checked at most hourly when shown,
  without reloading the whole catalogue. A storm keeps its ID when NOAA renames it
  (for example "Tropical Depression Nine" becoming "Tropical Storm Isaias"), so a
  profile keeps working. When NOAA ends the storm, the profile's **Status** shows
  "LOST"; it is still tried at its next update or rotation step.
- **Strongest active storm**, first in **Active storms** of each satellite, picks
  the strongest storm NOAA lists: hurricane or typhoon before tropical storm before
  depression before remnants and invests; of equal ones the newest. The choice is
  renewed hourly (NOAA reassesses storms every 3-6 hours) and at once when the
  chosen storm's pictures fail, for example because the storm ended; each update
  still checks the chosen storm for a newer picture. Its name shows in the
  **Image** header, e.g. "Strongest active storm (Hurricane Isaias)". Without any
  storm the profile shows "UNAVAIL" and takes the next storm once there is one.
- After a NOAA failure, cached GOES-East, GOES-West and Solar selections remain
  editable.

### Source resolution

Size choices come from the selected product. New selections default to
**Automatic (recommended)** for both GOES satellites, the smallest size suitable
for the output and zoom; **Largest available** and saved sizes remain available.
**Zoom** enlarges the centre of the picture; two lines below it in Settings say
whether the chosen zoom is sharp, up to which zoom the largest (or selected) size
stays sharp at the output size, and by how much a higher zoom enlarges it.
No WMS render-quality factor is used: the source resolution determines the
detail, and the image is resized with Lanczos filtering. Large sizes take more
download time and memory; a larger output cannot add detail absent from the
source.

### Update behavior and limitations

- Each update check uses the latest published image. Publication delays and
  outages can prevent a newer image; a failed download keeps the last wallpaper
  and never turns an older image into a new observation. The update interval sets
  how often MarbleScape checks, not how often NOAA produces imagery.
- Some STAR products contain boundaries, coastlines or grid lines in their JPEG
  pixels, which cannot be switched off. Choose CIRA SLIDER for clean tiles.
- NOAA occasionally publishes a valid but almost entirely black JPEG for one
  resolution while another size is normal. The size stays selectable; if the
  original on NOAA is black, choose a smaller resolution temporarily.

## Himawari imagery

### NICT

The [official NICT Himawari viewer](https://himawari8.nict.go.jp/) supplies
timestamped PNG tiles for True Color Full Disk, True Color Japan and all 16 AHI
bands. Source sizes: Full Disk True Color 550 × 550 to 11000 × 11000, Japan up to
3000 × 2400, bands up to 5500 × 5500. These are the zoom levels of NICT's viewer:
each **Source resolution** is one level. AHI bands are composited over the Blue
Marble base used by the viewer.

- **Plot shorelines** (**Rendering**) draws NICT's coastline overlay, the viewer's
  *Plot shorelines*, in any color (**Choose color…**, initially yellow). It is built
  like the Copernicus **Country borders** row, draws coastlines only (no country
  borders), and exists for every NICT view. The coastline tiles never change: each
  is downloaded once and kept in `content/himawari_shorelines`.
- **Center on coordinates** (**Source**, NICT Full Disk and AHI bands) puts a place
  in the middle of the full disk; **Latitude** and **Longitude** take decimal
  degrees, and [**Find location**](USER_GUIDE.md#find-location) fills them. It is the
  disk as Himawari sees it, not a flat map: the place is always exactly in the middle,
  and space beside the disk shows the background color. Only East Asia, Australia
  and the western Pacific are in view; near the edge of the disk the view is flat and
  less sharp.
- **Active storms** (an **Area category**) lists JMA's current tropical cyclones of
  the western North Pacific by name, for example *Severe Tropical Storm Koguma*
  (unnamed ones keep JMA's number, for example *Tropical Depression (TC2633)*), and
  *Himawari target area (rapid scan)*, the area Himawari scans every 2.5 minutes,
  which JMA usually places on a typhoon. **Strongest active storm**, first in the
  list, takes the strongest of JMA's cyclones (typhoon before severe tropical storm
  before tropical storm before depression; of equal ones the newest), chosen anew
  hourly and at once when JMA no longer lists it; without any it shows "UNAVAIL".
  The chosen storm is always in the middle,
  at its latest position at every update, so it stays in view while it moves. At
  **Zoom** 1 the view is about 3000 km across (measured below the satellite; a storm
  farther out looks a little smaller), Zoom 2 half of that; **Fit mode** does not
  apply. **Automatic** source resolution picks the NICT level that keeps this view
  sharp (for example 8800 × 8800 on a 1920 × 1080 picture). On 3840 × 2160 even the
  largest level, 11000 × 11000, is enlarged a little (about 1.3 times at Zoom 1); the
  lines below **Zoom** say by how much.
  The list is checked at most hourly when shown; when JMA no longer lists a storm,
  its profile shows "LOST" in the **Status** column and is still tried again later.
  NICT does not offer the rapid-scan pictures themselves; the full disk (every 10
  minutes) is used.

**Largest available** stays dynamic; for NICT Full Disk it currently means a
20 × 20 grid (400 tiles), so it can take noticeably longer and use more traffic.
Tiles are resized directly into the wallpaper canvas to avoid an 11000 × 11000
intermediate image.

### JMA

The [official JMA Himawari real-time catalogue](https://ds.data.jma.go.jp/mscweb/data/himawari/index.html)
supplies Full Disk, Australia, New Zealand, Japan, Central/South/Southeast Asia,
Pacific Islands, high-resolution regional, heavy-rainfall, high-resolution
heavy-rainfall views for ten Pacific island locations, and target-area stills.
MarbleScape lists the products published for each view and validates the JPEG
against the view's native dimensions. JMA annotations in a published JPEG remain
part of the image.

### Common behavior

Every update resolves the newest timestamp first. Only PNG and JPEG stills are
accepted; animation and movie controls are excluded. Fit mode, zoom, background
color and output size work like the NOAA sources.

## CIRA SLIDER imagery

The [CIRA SLIDER viewer](https://slider.cira.colostate.edu/) provides a live
catalogue of satellites, sectors, products, tile-pyramid levels and latest times.
MarbleScape shows them as **Satellite**, **Sector**, **Product / layer** and
**Source resolution**, including the GOES-East, GOES-West, Himawari-9,
GEO-KOMPSAT-2A, Meteosat, MTG and JPSS entries, and follows future catalogue
changes without a hard-coded list.

- Only the newest timestamped PNG still is used; animations and archived playback
  are excluded. Each wallpaper is assembled from the product's PNG tiles, then
  fit/crop, zoom, background and output size apply.
- SLIDER's **Default Borders**, other maps and **Lat/Lon** grid are separate viewer
  layers that MarbleScape does not fetch, so borders and coordinate lines are off.
- The highest level can contain hundreds of tiles. A full grid is capped at 1,024
  responses and rendered directly into the canvas; a smaller source resolution
  reduces the request count.
- **Non-square sectors:** SLIDER stores every sector as a square tile grid and pads
  wide sectors with black rows, for example GOES CONUS (5:3), Himawari Japan and
  JPSS CONUS/Alaska. MarbleScape measures this padding once per sector from the
  smallest tile and frames **fit**, **crop** and zoom on the visible sector, so a
  5:3 CONUS fills a 16:9 screen's height with **fit**. The padding counts only when
  it is exactly black, centred and at least 3% per side, so a dark night side or a
  full disk's space margin is never cut off. Measurements are kept in
  `content/slider_content_boxes.json`.
- **Visible size:** **Source resolution** and the profile table's **Resolution selection** and **Resolution** show
  the visible size, for example **10000 × 6032** instead of the 10000 × 10000 grid of
  GOES CONUS, and **Automatic** chooses its level by that size. Saved selections
  keep their grid value, so existing profiles stay valid. Images of padded sectors
  are rendered once more after this change; square sectors such as Full Disk keep
  their saved images.
- **Refresh catalogue** reloads the live definition; a bundled fallback keeps core
  full-disk GeoColor choices while the catalogue endpoint is unavailable.

## NASA Worldview imagery

[NASA Worldview](https://worldview.earthdata.nasa.gov/) is powered by
[NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/access-basics/).

### Layers and dates

MarbleScape reads the official EPSG:4326 `best` WMTS capabilities and lists every
still-image visualization with usable geographic tile-matrix metadata. The
controls are **Layer category**, **Imagery layer**, **Date / time** and **Render
resolution**. **Filter categories** above **Layer category** narrows the category list
by name; **Filter imagery layers** searches the chosen category by title or GIBS
layer ID. Both narrow only their list: the shown choice stays until you pick
another one.

Time-dependent layers default to **Latest available**: before an image check,
MarbleScape queries the layer's GIBS time domain and uses the newest acquisition,
falling back to the capabilities default if that small request is unavailable. The
date dropdown also offers up to 100 recent fixed values. Layers without a time
dimension are marked **Timeless**.

### Rendering

The layer is requested as one PNG from the GIBS WMS 1.3 service over the full
world extent. MarbleScape does not scrape the interactive application, request
animations or download Worldview's separate labels, borders and coordinate
overlays; annotations embedded in a visualization remain in its pixels.

Render sizes come from the layer's tile-matrix levels, limited to 8,192 pixels on
one axis and about 33.5 million pixels; **Largest available** is resolved from the
current catalogue each time. Fit/crop, zoom and background are applied locally
with Lanczos resampling. If a large request times out or is rejected, choose a
smaller **Render resolution**; a failed request keeps the previous wallpaper.

A small bundled true-color fallback keeps a saved selection visible during a
catalogue outage, but downloading still needs a live GIBS connection.

## Copernicus Browser imagery

### Selection

Copernicus selections use dependent dropdowns for **mission/dataset**,
**configuration**, **product**, **visualization layer**, **Example scene** and
**acquisition date**. The dropdowns share one stable width. Configuration,
product, layer and example scenes come from a bundled snapshot of the official
Copernicus Browser catalogue. Sentinel-1 is the radar mission; Sentinel-2,
Sentinel-3, Sentinel-5P, Copernicus DEM and Landsat are included because the
Browser offers them as visual products. The Process API renders the selected
official evalscript.

New installations start with **Sentinel-2 Mosaics · Sentinel-2 Quarterly Mosaics ·
True Color Cloudless**; saved selections remain unchanged. Select **Sentinel-2
L2A · True color** for a recent individual acquisition or finer local detail.

### Location and map zoom

Enter a custom **Latitude** and **Longitude** and use **Map zoom** to frame the output.
**Image** > **Find location** fills **Latitude** and **Longitude** from a place name
(OpenStreetMap search) or pasted coordinates; its **Preview** opens the Copernicus
Browser at that place and map zoom.
**Example scene** lists the Copernicus Browser's showcase scenes (its "highlights")
of the selected configuration, for example *Wildfires in Canada (SWIR)* under
*Wildfires*; the row is hidden for configurations without scenes, such as *Default*.
A scene loads its product, layer, position, zoom and its own acquisition date, which
can then be edited: choose "Latest available" under **Date / time** to keep the
picture current. "None" means the own **Latitude** and **Longitude**; typing
coordinates or using **Find location** switches back to it. Profiles, profile JSON
and PNG metadata keep the scene in the field `highlight`.

- The zoom dropdown follows the official limits of the dataset: 7-18 for
  Sentinel-1 and Sentinel-2 L2A, 10-18 for Sentinel-2 L1C, 5/6-18 for Sentinel-3,
  3-19 for Sentinel-5P, 7-18 for Landsat and 7-25 for DEM.
- Map zoom describes the requested geographic extent, not the optical zoom of a
  global photograph. At a small zoom, a Sentinel-2 overpass can fill only a small
  part of the output.
- The provider restricts COPERNICUS_30 DEM to authorized CCM users;
  COPERNICUS_90 remains the unrestricted DEM default.

### Acquisition date

**Latest available** is the default and is resolved again before each download.
**Refresh catalogue** queries the live STAC Catalog API for every acquisition date
at the current location and selection; the list then offers fixed dates.

- Changing the layer, product, mission or configuration keeps a chosen date when
  the new selection uses the same kind of date (day, month or quarter). If the
  refreshed list lacks it, the date switches to **Latest available** and the
  status line says so.
- While the list reloads, the dropdown shows "Loading dates…"; after a layer
  change it keeps the previous dates meanwhile, because a product's layers share
  their dates. An open dropdown updates as soon as the new list arrives.
- Acquisitions without a cloud estimate are always skipped, see
  [Maximum cloud cover](#maximum-cloud-cover).

### Quarterly and monthly mosaics

Quarterly mosaics offer **Quarter selection**:

- **Specific quarter** shows the **Quarter** list.
- **Relative to now** shows **Quarters back** (**Current quarter**, **1 quarter ago**, up to
  **40 quarters ago**) and **Resolved quarter**; in 2027 Q4, 1 quarter ago resolves to
  2027 Q3. The target is recalculated for each update in the display time zone.
- **Latest available** has no offset and queries the newest published period for
  the location.

An unavailable target is reported without substituting another period; the
current period may not be published yet. Sentinel-1 IW/DH products are official
monthly mosaics with the same behavior as **Month selection**, **Specific
month**, **Months back** (0-120) and **Resolved month**; no custom quarterly
aggregation is performed (see the
[Sentinel-1 documentation](https://documentation.dataspace.copernicus.eu/Data/Sentinel1.html)).
Ordinary acquisitions, annual products and timeless terrain have no rolling
period controls. New PNGs keep both the saved selection logic and the resolved
period ([PNG metadata](PNG_METADATA.md)).

The word "Cloudless" describes the mosaic processing, not a guarantee of clear
pixels: quarterly mosaics can contain No Data where no cloud-free observation
exists, residual clouds, snow, over-bright terrain on steep slopes or source-tile
seams. Compare the period in the
[Copernicus Browser](https://browser.dataspace.copernicus.eu/) when checking
whether an area belongs to the source mosaic. Sentinel-1 DH monthly mosaics mainly
cover polar regions; a region and month without valid imagery is reported rather
than presenting the map background as imagery.

### Gap fill

**Image** > **Recommendation** > **Compare variants...** compares cloud limits and Gap fill (or a
mosaic's periods) for the view from the catalogue before rendering and recommends one
setting for the fewest clouds (or the newest period) and one for full coverage;
**Use auto recommendation** takes that choice at every image check; see the
[user guide](USER_GUIDE.md#recommendation-copernicus).

**Gap fill** offers **Single latest acquisition** or **Fill gaps with earlier
imagery**:

- **Single latest acquisition** uses the latest qualifying date and does not fill
  uncovered areas from earlier dates.
- **Fill gaps** uses the most recent valid pixel within a lookback of 3, 7, 14, 21,
  30, 45, 60, 90, 120, 180, 270, 365, 550, 730, 920 or 1095 days (14 by default for
  new profiles; month and year labels are approximate, the day count is exact).
  The lookback counts back from the chosen acquisition date. Every contributing
  tile must meet the cloud limit.

Gap fill fills only areas without image data; clouds are image data and stay.
Areas still without imagery take the [No-data color](#no-data-color). The former
option **Fill areas without image data with black** became **Single latest
acquisition** with a black **No-data color**; saved profiles and exports convert
automatically and look as before.

Precomputed mosaics do not use the cloud filter or the day-based lookback. Saved
inactive settings can still appear in a profile's metadata without affecting the
mosaic.

**Large outputs:** requests above the Process API's 2500 × 2500 pixel limit are
split into tiles and joined without reducing the resolution. In gap-fill mode, a
large response with transparent areas is checked again in 512-pixel tiles, and
only tiles with missing pixels are requested again. This can recover coverage the
API omits from a broad request, but adds requests and cannot create imagery where
none exists.

### Maximum cloud cover

For optical layers that support it, **Maximum cloud cover** filters satellite tiles
by their published cloud-cover estimate, for date discovery and rendering, also
with **Single latest acquisition**. The slider runs from 0% to 100% in 5% steps;
new selections start at 100%, saved ones keep their value. It is disabled
for layers without this metadata, such as Sentinel-1 radar and DEM.
With a fixed date it stays available: it filters the dates offered under **Date /
time** (a low limit lists only clearer days) and leaves out that day's cloudier
tiles; the note below the slider says so. The date list reloads once the slider is
let go, and also after a change of **Latitude**, **Longitude**, **Map zoom** or the
picture size; a fixed date the new list no longer offers becomes **Latest
available** with a note, and a zoom that no longer fits the view becomes the
nearest one that does.

- **Inclusive upper limit:** 20% accepts tiles estimated at 20% or less; 0%
  requires exactly 0% and may return no image; 100% allows every published value.
- **Unknown estimates are skipped:** acquisitions without an estimate are always
  skipped when MarbleScape picks the latest date or lists dates. Landsat 8/9 night
  passes report -1 and would otherwise become a black "latest" image; see
  [a completely black Copernicus image](IMAGERY_ARTIFACTS.md#a-completely-black-copernicus-image).
- **Whole tiles:** the estimate covers whole satellite tiles, so the visible map can
  contain more clouds than the percentage.
- **Clouds are image data:** neither the No-data color nor Gap fill replaces them.
  With a high limit a completely overcast newest acquisition becomes a white
  image; lower the limit or choose a date, see
  [a completely white or grey Copernicus image](IMAGERY_ARTIFACTS.md#a-completely-white-or-grey-copernicus-image).

Trade-offs: a higher limit admits more cloudy tiles; this may give the latest
qualifying date better coverage and, with gap filling, need fewer older dates, so
the picture is more consistent in time. A lower limit excludes more tiles and may
leave gaps or, with gap filling, produce a patchwork of dates. Neither changes the
number of tiles needed for the map or guarantees a seamless result, and changing
the limit can change which date counts as latest. A fixed date stays fixed; a
stricter limit may leave part or all of it without imagery. See the
[Sentinel-2 L2A filtering and mosaicking documentation](https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/).

### No-data color

**No-data color** decides what fills areas without image data. It uses the
provider's alpha/data mask, not a black-pixel threshold, so valid dark terrain,
water, clouds and shadows are never changed. Mosaics (Sentinel-1/Sentinel-2
quarterly, monthly and annual) and regular layers keep separate choices; the row
shows the one for the selected layer after **Choose color…**: a hex color, **Transparent**,
**Blur** or **Edge Blur**, in the same place and width as the **Labels** and **Country borders**
colors. **Transparent**, **Blur** and **Edge Blur** are equally wide buttons.

- **Choose color…** fills the gaps with one color.
- **Transparent** shows the map background on regular layers. On mosaics it keeps
  the gaps transparent in the saved PNG (RGBA), also after resampling and metadata
  embedding; mosaics never show the map background.
- **Blur**, the default for new selections (mosaics and regular layers), spreads
  the nearby image into the gaps: close to real pixels the gap
  takes their colors, lightly blurred; farther away it fades into a smooth average
  of the whole image, so large areas stay calm. Small streaks and holes mostly
  disappear; large gaps become a soft, invented surface.
- **Edge Blur** is the same near real pixels, but far away it fades into the average of the pixels
  along the gap's edge only. Open sea beyond a Sentinel-2 coast then takes the
  color of the coastal water instead of the land's green or brown. Where a gap is
  surrounded by land, both look alike.

**Blur** and **Edge Blur** are not image data and can take a few seconds for a large
image. Profiles saved with **Edge Blur** or another choice keep it.

| Choice | Saved value | Mosaics | Regular layers | Far from real pixels |
|---|---|---|---|---|
| **Choose color…** | `#RRGGBB` | gaps in that color | gaps in that color | the same color |
| **Transparent** | `transparent` | transparent gaps in the saved PNG (RGBA) | the map background shows | - |
| **Blur** | `blur` | nearby image spread into the gaps | nearby image spread into the gaps | the average of the whole image |
| **Edge Blur** | `blur_edge` | nearby image spread into the gaps | nearby image spread into the gaps | the average of the gap's edge (e.g. coastal water for open sea) |

**Blur** is the default for new mosaic and regular-layer selections. Near real
pixels, **Blur** and **Edge Blur** look the same.

Selections saved before these choices existed keep their look: white mosaic gaps
(`#FFFFFF`) and the map background behind regular layers. Saved choices are never
changed by the default.

Notes:

- Sentinel-2 mosaics contain data only near land: a wide ocean view such as Hawaii
  at map zoom 7 has image data for only about 5% of its pixels, and earlier periods
  do not add the open sea. **Edge Blur**, which continues the coastal water, or a
  dark color then usually looks calmer than white.
- The Landsat 8/9 **Wildfires** layer returns color only. MarbleScape adds the data
  mask to its request, so its gaps are filled like those of every other layer; its
  first image after this change is downloaded again, other layers keep theirs.
- The choice is stored per profile, in the global source settings and in both PNG
  provenance copies (including EXIF): mosaics as `no_data_color`, regular layers as
  `scene_no_data_color`. It is also the **No-data color** profile column. Changing
  it requests a new render; existing images are not modified.
- Coverage is measured before filling, blurring or labels, so filling does not
  increase it. A completely empty mosaic is reported as unavailable rather than
  saved as a solid-color image.
- **Transparent mosaics on the desktop:** Windows wallpaper is not a transparent
  layer. With detected displays, MarbleScape flattens only the separate wallpaper
  copy against the background color; Latest, History and cached original PNGs stay
  transparent. **Labels**, **Country borders** and attribution can still be drawn over gaps.

### Labels and Country borders

Two map overlays can be added, as in Copernicus Browser, for regular layers and
all Sentinel-1/Sentinel-2 mosaics, on the same first render:

- **Labels:** the GISCO/OpenStreetMap label layer with place names, road names and
  POIs, which the map service provides as one image and cannot be split.
- **Country borders:** GISCO boundaries.

Each has its own checkbox and color (swatch, **Choose color…**, hex value); both
start off and white (`#FFFFFF`), and a switched-off overlay keeps its color.
Selections from before the two were separate keep one common switch and color;
one saved before the color existed keeps black labels. All four settings are
stored in profiles, profile JSON and PNG text/EXIF.

- The overlays are independent of the satellite pixels and drawn over the
  **No-data color** or **Blur**.
- Background, borders and labels are attempted independently; a failure produces
  a warning and keeps the satellite image.
- Labels depend on the map extent/zoom and the available GISCO tiles; above zoom 18
  the renderer enlarges zoom-18 tiles, so further zooming adds no label detail.
  Inspect the layers in the [GISCO map demo](https://gisco-services.ec.europa.eu/maps/demo/).
- Attribution is written into generated images whenever map tiles are used.

### Brightness and contrast correction

Both are saved per profile and apply only to Copernicus layers with a tone rule
(see [Tone rules](#tone-rules) below); other Copernicus layers render stock and
other image sources have no such controls. They sit in the Image tab's
**Rendering** section, below **Image resolution** and above the map overlays
(**Labels**, **Country borders**) and the **No-data color**.

- **Brightness correction:** 25-200% in 5% steps; 100% is the normal rendering.
  Mosaics take it in their request (the Sentinel-2 cloudless mosaics use the
  Browser's optical contrast curve, which rolls highlights off softly); regular
  layers get it as a midtone gamma after the download. With auto contrast on, the
  value acts on the midtones after the stretch.
- **Contrast correction:** 0-200% in 5% steps; neutral 100%. An S-curve around the
  image's own median luminance: black and white stay fixed, and shadows and
  highlights are compressed (at 200% to half their distance), never clipped.
- **auto (brightness / contrast):** an independent checkbox left of each slider,
  on for new selections of a layer with a tone rule (saved selections keep their
  choice). In the profile table and in PNG metadata they are called **Auto
  brightness** and **Auto contrast**. Without auto, a mosaic's brightness is applied by
  the Copernicus server; contrast, a regular layer's brightness and everything
  with auto are computed locally after the download (Landsat true colour with
  auto contrast asks the server for its soft highlight script, rule f). Enabling one disables its
  slider and keeps your own value for when auto is switched off again. Meanwhile
  the slider shows what auto chose for the picture on screen, as long as the
  selection still matches it: brightness as the equal manual percentage (e.g.
  125%), contrast as the stretch factor (e.g. ×1.32, at most ×1.50; the manual
  contrast is a different curve, so the slider stays at 100%). Without such a
  picture the value reads "auto" until the next one. Auto contrast stretches the range so that at most
  2% of the pixels reach black in their darkest channel or white in their
  brightest one, so saturated colors and dark water keep their detail. A few
  bright spots (scattered cloud, sand) roll off softly instead of turning flat
  white; large bright surfaces (salt flat, cloud deck, snowfield) keep the
  straight stretch, which shows more of their texture. The stretch is at most
  1.5-fold, so a scene of nothing but cloud keeps its faint texture and a dark
  mosaic (rendered at 100%, most of it sea) does not turn bright and flat, and it
  ends at 96.5% instead of pure white, so beaches, salt and cloud stay a little
  darker than white. Where the source already clips its brightest parts to
  white (cloud in some Landsat true colour scenes), stretching could only darken
  the land, so Auto contrast leaves such a picture as it is.
  Auto brightness alone moves the
  median luminance toward 42% with a midtone gamma: it brightens freely (gamma
  down to 0.5) but darkens only a little (up to 1.15). Together with Auto
  contrast (the recommended combination) it lifts a picture only while 65% of its
  pixels stay below 25% luminance, so a dark sea next to well-lit land is left as
  stretched while very dark land is lifted a little (gamma down to 0.8),
  and darkens bright pictures a little (up to 1.15) to keep detail in cloud,
  snow, salt and sand. Flat or empty statistics use a neutral adjustment.

#### Tone rules

Each Copernicus layer that offers **auto** uses one tone rule, chosen per
collection and layer after comparing example pictures. All rules share the same
base and differ only where the table shows it.

| Rule | a | b | c | d | e | f |
|---|---|---|---|---|---|---|
| **Contrast: measurement** | black point 2% quantile of the darkest channel, white point 98% quantile of the brightest | same | same | same | same | same |
| **Contrast: limits** | stretch at most 1.5-fold, target white 96.5% | same | same | same | same | same |
| **Clipped source** (top value 250 or more) | no stretch | same | same | same | same | same |
| **Highlight shoulder** | from 85%, fading out with a large bright surface (8-20% of the pixels above 90%) | as a | as a; with a bright area of 25% or more and a top value of 240 or more, the shoulder of e | as a | adaptive: from 65% (bright area up to 10%) to 80% (25% or more), white only at the brightest value | as e |
| **Auto brightness** | neutral (100%) | lifts dark pictures (gamma down to 0.8 while 65% of the pixels stay below 25%), darkens bright ones (up to 1.15 while the median is above 42%) | as b | as b | as b | as b |
| **Large bright area** | no adaptation | no adaptation | no adaptation | black point raised half as much, no darkening | no adaptation | as d |
| **Server script** | original | original | original | original | original | soft: as the original up to reflectance 0.28 (output 70%), then a soft roll-off reaching white only at reflectance 1.0 instead of clipping at 0.4 |

"Bright area" is the share of pixels brighter than 75% after the stretch; the
adaptations blend smoothly between 10% and 25%. Layers without a rule (-) get no
correction.

Rules per collection and layer:

| Source | Layer | Rule |
|---|---|---|
| **Sentinel-2 L2A** | **True color** | e |
| | **Highlight Optimized Natural Color** | b |
| | **False color**, **SWIR** | - |
| **Sentinel-2 L1C** | **True color** | b |
| | **Highlight Optimized Natural Color** | b |
| | **False color**, **SWIR** | - |
| **Sentinel-2 Quarterly Mosaics** | **True Color Cloudless** | c |
| | **False Color Cloudless** | - |
| **WorldCover Annual Cloudless Mosaics** | **True Color Cloudless** | c |
| | **False Color Cloudless** | - |
| **Landsat 8/9** | **True color**, **True color - pansharpened** | f |
| | **Highlight Optimized Natural Color** | a |
| | **False color** | - |
| **Sentinel-3 OLCI** | **True color**, **Enhanced Natural Color** | b |
| | **Highlight Optimized Natural Color** | d |
| **Sentinel-1**, **Sentinel-1 Mosaics** (radar) | all layers | - |
| Other Copernicus layers (indices, measurements, DEM) | - | - |
| EUMETSAT, NOAA, Himawari, CIRA SLIDER, NASA Worldview | - | no correction controls |

- **With a rule:** both sliders work, **auto** is on for new selections, and
  unchecking it allows manual values.
- **Without a rule (-):** both sliders stay at 100% and **auto** is unavailable;
  values saved earlier stay in the profile but are not applied.
- **Sources without correction controls** show neither sliders nor **auto**.

Statistics use fully opaque satellite pixels of the whole assembled image, before
No-data filling, labels and attribution, so there are no per-tile seams. These are
display enhancements, not radiometric corrections: snow, water and dark volcanic
terrain can justify manual settings, and clouds are not excluded. Chosen settings
and applied parameters are recorded in PNG/EXIF.

### Image resolution

**Auto (recommended)**, **1920 × 1080 (Full HD)**, **2560 × 1440 (QHD)**, **3840 × 2160 (4K UHD)**
or **7680 × 4320 (8K UHD)**, or in portrait **1080 × 1920**, **1440 × 2560**, **2160 × 3840** or
**4320 × 7680** (named "Full HD, portrait" and so on), for all
Copernicus selections.

**Auto** keeps the global output aspect ratio and minimum size and enlarges them
until the image covers every connected monitor. For each monitor it compares the
physical size and any per-monitor override with the baseline and takes the larger
of the width and height ratios; the largest ratio across all monitors wins
(rounded up). The deciding monitor is the one needing the most enlargement, not
necessarily the biggest; an override can only enlarge the result. Disconnected
monitor overrides and paused monitors (**Pause wallpaper updates**) are ignored.
The other sources size their saved picture by the same rule while Windows
wallpapers are on.

Example: baseline 1920×1080 (16:9), a 2560×1440 landscape and a 1050×1680
portrait monitor. The landscape monitor needs 2560 ÷ 1920 = 1440 ÷ 1080 ≈ 1.33×;
the portrait monitor needs max(1050 ÷ 1920, 1680 ÷ 1080) ≈ 1.56×, because its crop
of the shared landscape image must be 1680 pixels tall. Auto therefore downloads
2987×1680; without the portrait monitor it would be 2560×1440.

**Fixed sizes** take precedence: fixed 4K with an 8K monitor still produces a 4K
PNG; choose **Auto** or **8K** for an 8K download. **General** > **Monitor output** still controls
the wallpaper composition.

**Portrait sizes** render a vertical view of the place: more to the north and
south, less to the east and west, for example for a coastline or a river running
north-south. They are a profile setting, so other profiles stay landscape. On a
landscape monitor the wallpaper fits the picture with margins in the **Background
color**; on a monitor turned to portrait in Windows it fills the screen. Map zoom,
Compare variants (with a narrow, tall preview) and the cache follow the size.

- Larger requests use tiled processing. The cap is 33,554,432 pixels and 32,768 per
  side. **Auto** stops at the largest picture within it, keeping the aspect ratio:
  a 3840-wide 9:16 monitor override with a 16:9 baseline would need 12136×6827
  (83 megapixels) and gets 7723×4344, which Windows enlarges for that monitor.
  A fixed size above the cap shows an error rather than a silent fallback.
- Larger images need more memory, transfer and quota, and at unchanged map zoom
  cover a wider area; check the framing when changing size. More pixels do not add
  native detail.

### Data coverage

For evalscripts whose alpha carries the binary `dataMask`, MarbleScape measures
**Data coverage** after satellite gap filling and before map backgrounds and
labels. It appears after download, in profile details and in PNG/EXIF. It is not a
cloud-free percentage. Missing or non-binary masks produce "Not available", as do
other providers without a verified mask. See
[coverage fields and limitations](PNG_METADATA.md#data-coverage).

### Errors and retries

- Temporary image-transfer failures and retryable responses use the global
  **Download retries** setting.
- Copernicus and GISCO HTTPS connections use a current Mozilla CA bundle in
  addition to the operating-system certificate store.
- If the GISCO background or label service stays unavailable, the rendered
  satellite imagery is kept, uncovered pixels are black, the overlays are omitted
  and the reason is logged.
- A Copernicus image or catalogue failure keeps the previous wallpaper and reports
  the source as unavailable. Invalid requests are reported at once with the
  service detail so the settings can be corrected.
