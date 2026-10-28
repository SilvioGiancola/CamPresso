# CamPresso branding

[README](../README.md) · [Developer guide](DEVELOPER_GUIDE.md)

**App:** CamPresso

**Tagline:** Action, packed lighter

<img src="../assets/brand/campresso-icon.png" width="192" alt="CamPresso application icon">

## Mark and palette

The primary identity follows the supplied reference: exactly three navy vertical motion lines above an orange T-shaped press, a pale-cyan video capsule, a navy play symbol and a navy base. The center motion line is longer than the two outer lines. The wordmark uses heavy rounded lettering with a widely spaced tagline. Use the transparent full-color mark on white/light backgrounds and the white-backed app icon on dark backgrounds.

Every mark, lockup and app-icon export includes the same three motion lines. The app icon places the full-color logo on a white rounded square, with balanced space above and below. A one-color navy lockup is provided for monochrome use. The `campresso-vertical` files remain compatible aliases of the primary mark; there is no separate motionless logo.

| Color | Hex | Use |
| --- | --- | --- |
| Navy | `#071E2A` | Base, play symbol, lettering and motion strokes |
| Orange | `#FF5B24` | Press and UI accent |
| Pale cyan | `#B0E8F0` | Compressed video capsule |
| White | `#FFFFFF` | App-icon background, monochrome play symbol and light presentation background |

The standalone SVG uses a 512 × 512 coordinate space. A 70-unit-wide stem meets a 348 × 66 press bar. The video capsule is 316 × 102 units at (98, 242), with fully rounded ends; the base is 376 × 50 units at (68, 354). The three 10-unit-wide strokes have round caps at x=224/256/288, starting at y=48/20/48 and ending at y=76. Preserve the rounded triangle, capsule proportions and separation between the three layers. The app icon uses a 488 × 488 white square at (12, 12), corner radius 110, and translates the mark by (15, 59) at 94% scale to center its visible bounds.

## Assets

- [Editable mark SVG](../assets/brand/campresso-mark.svg) and [transparent 1024px PNG](../assets/brand/campresso-mark.png).
- [Editable name/tagline lockup SVG](../assets/brand/campresso-lockup.svg) and [transparent 2880px PNG](../assets/brand/campresso-lockup.png).
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

The exporter uses AppKit/Core Graphics and writes the ICNS container directly. It does not launch CamPresso, use Photos or require an image-generation service. It overwrites only the named brand exports. The app build copies the icon and mark into bundle resources before signing. Inspect the large icon, small icon and lockup after changing geometry.

Historical names are retained only for compatibility with the previous installation and its recovery records. Do not replace recorded library/journal/media paths as part of branding.
