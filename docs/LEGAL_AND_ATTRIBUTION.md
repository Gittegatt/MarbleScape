# Data terms, attribution, and license

EUMETSAT data guidance, icon attribution, application licensing, and the outstanding distribution review.

[Back to the main README](../README.md)

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

Before publishing a new binary release, clarify permission for any artistic
changes to the icon and review the example images' layer terms. Also resolve
whether the application's noncommercial terms preserve the library modification
and recombination rights required by pystray's LGPL. Source and rebuild
materials alone do not settle that licensing question. See
[Third-Party Notices](../THIRD_PARTY_NOTICES.md#outstanding-licensing-review).
No additional permission or licensing exception is granted by this review.
