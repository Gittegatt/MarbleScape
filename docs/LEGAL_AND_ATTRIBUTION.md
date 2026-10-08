# Data terms, attribution, and license

EUMETSAT data guidance, icon attribution, application licensing, and the outstanding distribution review.

[Back to the main README](../README.md)

## EUMETSAT data and branding

Live downloaded imagery is not included in releases. Users
must verify the licence and attribution requirements for every selected layer.
See the official EUMETSAT data registration and licensing guide and terms of
use:

- https://user.eumetsat.int/resources/user-guides/data-registration-and-licensing
- https://www.eumetsat.int/about-us/terms-use

Do not package or reuse the EUMETSAT logo or corporate visual identity without
the required permission. This project intentionally ships without an EUMETSAT
logo.

## JMA tropical cyclone information

For Himawari's **Active storms**, MarbleScape reads the Japan Meteorological
Agency's public tropical cyclone list, names and positions
(`https://www.jma.go.jp/bosai/typhoon/`) to name a storm and centre the picture
on it; the picture itself comes from NICT. JMA permits reuse of its website
content with the source credited, for example "Source: Japan Meteorological
Agency website". Verify the current terms before redistributing:

- https://www.jma.go.jp/jma/en/copyright.html

## Example images

The README shows one picture assembled from example pictures made with
MarbleScape, `assets/examples/marblescape-examples.webp`; the single pictures are
next to it in `assets/examples` at 1600 × 900. The pictures were resized (and, in the
README picture, placed side by side) for display. The application license does
not relicense them.

- **Copernicus Browser** (Bora-Bora, Betsiboka Estuary, Guelb er Richat, Aletsch
  Glacier, Great Exuma, Tokyo, Western Greenland, Ari Atoll): contains modified
  Copernicus Sentinel data 2026, processed with the Copernicus Data Space
  Ecosystem.
- **CIRA SLIDER** (Hurricane Rachel, GOES-18 West CONUS and mesoscale): GOES-18
  imagery of NOAA, through RAMMB/CIRA SLIDER at Colorado State University.
- **NOAA SUVI** (the Sun, Fe195): GOES SUVI imagery of NOAA.
- **Himawari** (full disk in the centre, and Typhoon Koguma): Himawari-9 imagery of the
  Japan Meteorological Agency (JMA), through the NICT Himawari Real-Time Web.

Additional contributors and layer-specific terms may apply.

## Credits and attribution

The application and tray icon, a turquoise sphere outline with an equator, is
MarbleScape's own drawing: `python build_icon.py --draw` draws it from a circle
and an ellipse, and it is covered by the application license. It replaces an
earlier icon based on third-party artwork, which is no longer used or distributed.
See [Third-Party Notices](../THIRD_PARTY_NOTICES.md) and the bundled
[`licenses`](../licenses) directory for dependency notices.

## License

MarbleScape is source-available under the
[PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).
The complete terms are included in [`LICENSE`](../LICENSE).

Required Notice: Copyright 2026 Gittegatt

Noncommercial uses and the other permitted purposes defined in `LICENSE` are
allowed subject to its terms. Uses outside those permissions require separate
authorization from the copyright holder, except where statutory rights apply.
This is a general noncommercial license and is not an OSI-approved open-source
license. Third-party components
remain subject to their respective licenses.

### Outstanding distribution review

Before publishing a new binary release, review the example images' layer terms.
Also resolve whether the application's noncommercial terms
preserve the library modification
and recombination rights required by pystray's LGPL. Source and rebuild
materials alone do not settle that licensing question. See
[Third-Party Notices](../THIRD_PARTY_NOTICES.md#outstanding-licensing-review).
No additional permission or licensing exception is granted by this review.
