# Third-Party Notices

MarbleScape Wallpaper uses third-party software. Each component remains subject
to its own licence. The complete licence texts collected for the tested build
are included in the `licenses` directory.

## Runtime dependencies

| Component | Tested version | Licence | Project |
| --- | ---: | --- | --- |
| certifi | 2026.7.22 | MPL-2.0 | https://github.com/certifi/python-certifi |
| Pillow | 12.3.0 | MIT-CMU | https://python-pillow.github.io/ |
| pystray | 0.19.5 | LGPL-3.0-or-later | https://github.com/moses-palmer/pystray |
| six | 1.17.0 | MIT | https://github.com/benjaminp/six |

## Windows build and bundled runtime

The tested Windows executable is built with:

| Component | Tested version | Licence or terms | Project |
| --- | ---: | --- | --- |
| Python | 3.14.7 | Python Software Foundation Licence | https://www.python.org/psf/license/ |
| PyInstaller | 6.22.2 | GPL with bootloader exception; certain files Apache-2.0 (see bundled COPYING) | https://pyinstaller.org/ |
| pyinstaller-hooks-contrib | 2026.7 | Apache-2.0 and GPL-2.0-or-later | https://github.com/pyinstaller/pyinstaller-hooks-contrib |
| altgraph | 0.17.5 | MIT | https://github.com/ronaldoussoren/altgraph |
| pefile | 2024.8.26 | MIT | https://github.com/erocarrera/pefile |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | https://github.com/enthought/pywin32-ctypes |

A frozen executable may also contain standard-library and platform components
distributed with Python, including Tcl/Tk. Their notices are covered by the
included Python and Tcl/Tk licence files. Distributors remain responsible for
reviewing the exact contents of builds made in a different environment.

Example satellite images appear under `assets/examples`. Live downloads and
WMS layers remain subject to the applicable provider terms and data policies.

## Himawari imagery

Live Himawari imagery is requested from the public NICT Himawari viewer and the
JMA Meteorological Satellite Center real-time image service. The image data is
not bundled with MarbleScape and remains subject to each provider's terms:

- NICT Himawari viewer: https://himawari8.nict.go.jp/
- JMA Himawari real-time images: https://ds.data.jma.go.jp/mscweb/data/himawari/
- JMA Meteorological Satellite Center terms: https://www.data.jma.go.jp/mscweb/en/general/note.html

JMA specifies additional acknowledgement for True Color Reproduction imagery.
Where the complete acknowledgement shown on the JMA Himawari page cannot be
included, its stated minimum is: JMA, NOAA/NESDIS, CSU/CIRA. Users who publish
or redistribute downloaded imagery remain responsible for applying the current
provider terms and attribution requirements.

## Copernicus catalogue metadata and map tiles

The bundled Copernicus configuration, product, layer, evalscript, and highlight
catalogue is derived from the official `eu-cdse/copernicus-browser` source at
revision `1a1724c42b04e8a0953a410016676daea8ee5d33`. That project is copyright
2023 eu-cdse and licensed under MIT; its licence is included as
`licenses/COPERNICUS-BROWSER-MIT.txt`.

When Copernicus map background or labels are enabled, MarbleScape downloads
GISCO tiles based on OpenStreetMap data. Generated images carry the required
attribution: © OpenStreetMap contributors; tiles © European Union, GISCO.
OpenStreetMap data is available under the Open Database License (ODbL):
https://www.openstreetmap.org/copyright

## CIRA SLIDER imagery

Live SLIDER catalogue metadata and PNG imagery are requested from the public
Cooperative Institute for Research in the Atmosphere (CIRA) service at Colorado
State University. They are not bundled with MarbleScape and remain subject to
the provider's current notices and source-specific data terms:

- CIRA SLIDER: https://slider.cira.colostate.edu/
- SLIDER experimental products disclaimer: https://rammb2.cira.colostate.edu/resources/experimental_products_disclaimer/

Users who publish or redistribute downloaded imagery remain responsible for
the applicable satellite/product attribution and provider terms.

## NASA Worldview and GIBS imagery

Live NASA Worldview catalogue metadata and imagery are requested from NASA's
Global Imagery Browse Services (GIBS). No NASA imagery or GIBS software is
bundled with MarbleScape. Each visualization can combine data from different
missions or providers and remains subject to its applicable data policy,
attribution, and acknowledgement requirements:

- NASA Worldview: https://worldview.earthdata.nasa.gov/
- NASA GIBS API documentation: https://nasa-gibs.github.io/gibs-api-docs/
- NASA Earthdata data-use guidance: https://www.earthdata.nasa.gov/engage/open-data-services-software/data-use-policy

Users who publish or redistribute downloaded imagery must identify the selected
GIBS layer and follow the current terms for its underlying dataset. NASA and
Earthdata references do not imply endorsement of MarbleScape.

## pystray source and distribution

pystray is copyright 2016-2022 Moses Palmér. Its
[source headers](https://github.com/moses-palmer/pystray/blob/v0.19.5/lib/pystray/__init__.py)
specify LGPL version 3 or later. Copies of the LGPL and GPL are included in
`licenses/PYSTRAY-0.19.5-LGPL.txt` and `licenses/PYSTRAY-0.19.5-GPL.txt`.

The updated release script includes `third_party/pystray-0.19.5-source.zip`, with
provenance and a SHA-256 digest in `third_party/README.md`. It verifies the
installed Python sources against that archive. The Windows ZIP also contains
the matching `source/MarbleScape-source.zip`, including application code,
configuration template, icons and build scripts. Older archives lack these
additions until rebuilt. Publish matching source and binaries together.

### Rebuilding with a modified pystray

Extract the matching MarbleScape source package into a separate working folder.
On 64-bit Windows, install Python and use that folder as the working directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
Expand-Archive .\third_party\pystray-0.19.5-source.zip -DestinationPath .\third_party
```

Edit the library under `third_party/pystray-0.19.5/lib/pystray`, then install
that local source after installing the other dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation --force-reinstall .\third_party\pystray-0.19.5
.\.venv\Scripts\python.exe build_icon.py
.\.venv\Scripts\python.exe build_windows_version.py windows-version.txt
```

Rebuild directly with the modified library (the normal release script
deliberately rejects a mismatch with the bundled upstream source):

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed --name marblescape --icon assets/icons/marblescape.ico --version-file windows-version.txt --hidden-import pystray._win32 --add-data "assets/icons;assets/icons" marblescape_download.py
```

The resulting executable is `dist/marblescape.exe`. This creates a local build,
not a new release package. Before redistributing a modified build, supply its
actual modified library source, matching application source, build instructions
and notices, rather than reusing the unchanged upstream archive. These steps
do not promise byte-identical executables or independently grant distribution
rights.

### Outstanding licensing review

[LGPL section 4](https://github.com/moses-palmer/pystray/blob/v0.19.5/COPYING.LGPL)
requires more than notices: a combined work must preserve the specified library
modification and debugging rights and provide a suitable replacement or
recombination route. Source packaging addresses the technical requirements;
compatibility with MarbleScape's noncommercial terms still needs review by the
copyright holder or qualified legal counsel. No exception has been added to
`LICENSE`. Resolve this before publishing a new binary.

The [PyInstaller bootloader exception](https://pyinstaller.org/en/stable/license.html)
does not remove obligations imposed by bundled dependencies.

## Application icon

The application and tray use the supplied MarbleScape PNG assets in
`assets/icons`. The build packages their original sizes into `marblescape.ico`.

Attribution for the supplied artwork:

- Creator: Zwoelf
- Creator profile: https://icon-icons.com/authors/728-zwoelf
- Creator website: https://www.zwoelf.hu/
- Title: Shape sphere
- Source: https://icon-icons.com/icon/shape-sphere/120489
- Pack: https://icon-icons.com/pack/zwicon/1875 ("Free for commercial use")
- Creator's usage terms: https://www.zwicon.com/how-to-use.html
- License: Creative Commons Attribution-NoDerivatives 4.0 International
- License URL: https://creativecommons.org/licenses/by-nd/4.0/

The creator's usage page specifies CC BY-ND 4.0. The commercial-use label on
icon-icons is consistent with commercial use, but does not replace those terms.
The previous CC BY 4.0 attribution was too permissive and has been corrected.

The artwork is distributed as size-specific PNG assets and packaged into a
multi-resolution Windows ICO for MarbleScape. Attribution does not imply
endorsement by the creator. The icon remains under its own CC BY-ND 4.0 terms;
MarbleScape's PolyForm Noncommercial restrictions do not apply to this icon.

Modification history: the MarbleScape maintainer recolored the icon turquoise
using the built-in SVG editor accessed through **Edit icon** on icon-icons.com.
The maintainer's intended use is noncommercial. The PNG sizes and ICO package
are based on that recolored version.
