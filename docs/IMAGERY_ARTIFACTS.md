# Satellite imagery artifacts

Expected seams, missing coverage, partial acquisitions, and ways to distinguish source artifacts from display issues.

[Back to the main README](../README.md)

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
and the [EUMETSAT viewer release changelog](https://user.eumetsat.int/resources/user-guides/eumet-view-release-changelog).
