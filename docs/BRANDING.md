# CamPresso branding

[README](../README.md) · [Developer guide](DEVELOPER_GUIDE.md)

**App:** CamPresso

**Tagline:** Action, packed lighter

<img src="../assets/brand/campresso-icon.png" width="192" alt="CamPresso application icon">

## Mark and palette

The primary identity follows the supplied reference: exactly three vertical motion lines above an orange T-shaped press, a pale-cyan video capsule and a navy play symbol. The center motion line is longer than the two outer lines. The motion lines, base and lettering are navy on light backgrounds and white on dark backgrounds. The wordmark uses heavy rounded lettering with a widely spaced tagline. The app icon has a deep-navy background matching the application.

Every mark, lockup and app-icon export includes the same three motion lines. The app icon places the dark-background logo on a navy rounded square, with balanced space above and below. A one-color navy lockup is provided for monochrome use. The `campresso-vertical` files remain compatible aliases of the light-background primary mark; there is no separate motionless logo.

| Color | Hex | Use |
| --- | --- | --- |
| Navy | `#071E2A` | App/icon background, play symbol; base, lettering and strokes on light backgrounds |
| Surface | `#122F3D` | Raised app panels/cards and the website's illustrated app panels |
| Orange | `#FF5B24` | Press and UI accent |
| Pale cyan | `#B0E8F0` | Compressed video capsule |
| White | `#FFFFFF` | Text, motion lines and base on dark backgrounds; monochrome play symbol |

The standalone SVG uses a 512 × 512 coordinate space. A 70-unit-wide stem meets a 348 × 66 press bar. The video capsule is 316 × 102 units at (98, 242), with fully rounded ends; the base is 376 × 50 units at (68, 354). The three 10-unit-wide strokes have round caps at x=224/256/288, starting at y=48/20/48 and ending at y=76. Preserve the rounded triangle, capsule proportions and separation between the three layers. The app icon uses a 488 × 488 navy square at (12, 12), corner radius 110, and translates the mark by (15, 59) at 94% scale to center its visible bounds.

## Application and website colors

The application uses a navy dark appearance for the gallery, Settings, Details and comparison windows, including native dark controls and dialogs. `Brand.swift` owns the application colors and shared window styling. The asset exporter uses the same sRGB values; website CSS declares matching `--navy`, `--surface`, `--orange` and `--cyan` variables. Keep these definitions aligned when changing the palette. Status colors retain their native warning/error/success meanings, and video pixels are never recolored by the theme.

Orange buttons use navy labels: this pair has about 5.5:1 contrast, while white on orange is only about 3.1:1. White and cyan are readable on navy. The landing page retains its light reading surface, uses the light-background mark, and illustrates the app with the same navy/surface colors. Its darker orange text shade is a contrast adjustment for light backgrounds, not a different primary brand orange.

## Assets

- [Editable mark SVG](../assets/brand/campresso-mark.svg) and [transparent 1024px PNG](../assets/brand/campresso-mark.png).
- [Dark-background mark SVG](../assets/brand/campresso-mark-dark.svg) and [transparent PNG](../assets/brand/campresso-mark-dark.png).
- [Editable name/tagline lockup SVG](../assets/brand/campresso-lockup.svg) and [transparent 2880px PNG](../assets/brand/campresso-lockup.png).
- [Dark-background lockup SVG](../assets/brand/campresso-lockup-dark.svg) and [transparent PNG](../assets/brand/campresso-lockup-dark.png).
- [Monochrome SVG](../assets/brand/campresso-monochrome.svg) and [PNG](../assets/brand/campresso-monochrome.png).
- [Compatible vertical-press SVG](../assets/brand/campresso-vertical.svg) and [PNG](../assets/brand/campresso-vertical.png), identical to the primary mark.
- [Editable application-icon SVG](../assets/brand/campresso-icon.svg), [1024px PNG](../assets/brand/campresso-icon.png), [32px preview](../assets/brand/campresso-icon-32.png) and [macOS ICNS](../assets/brand/CamPresso.icns).
- [Brand overview](../assets/brand/campresso-brand-board.png), showing the complete identity and variants.

The ICNS contains standard and Retina PNG representations from 16px through 1024px. The SVG and native raster paths share the documented geometry; exports use sRGB. The lockup SVG keeps editable text, so its exact font metrics depend on the viewer; the PNG freezes the native rendering.

## Regenerate

On macOS, from the checkout:

```bash
xcrun swift scripts/export_brand_assets.swift assets/brand
```

The exporter uses AppKit/Core Graphics and writes the ICNS container directly. It does not launch CamPresso, use Photos or require an image-generation service. It overwrites only the named brand exports. The app build copies the icon and both mark variants into bundle resources before signing. Inspect the large icon, small icon and both light/dark lockups after changing geometry or colors.

Historical names are retained only for compatibility with the previous installation and its recovery records. Do not replace recorded library/journal/media paths as part of branding.
