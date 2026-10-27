# CamPresso branding

[README](../README.md) · [Developer guide](DEVELOPER_GUIDE.md)

**App:** CamPresso

**Tagline:** Action, packed lighter

<img src="../assets/brand/campresso-icon.png" width="192" alt="CamPresso application icon">

## Mark and palette

The mark depicts a vertical press above a compressed, rounded video tile with a play symbol. It has exactly three downward motion strokes. Use the standalone transparent mark on white/cream backgrounds; use the cream-backed application icon where a dark background would obscure espresso lines. The wordmark uses native system sans-serif with a semibold name and regular tagline.

| Color | Hex | Use |
| --- | --- | --- |
| Espresso | `#2B1A16` | Press, frame and lettering |
| Orange | `#F27A2A` | Video tile, motion strokes and UI accent |
| Cream | `#FFF4E6` | Play symbol and icon background |

The standalone SVG uses a 512 × 512 coordinate space. Posts are at x=108/376, with a top beam, center ram, pressing platen and bottom support. The rounded video tile is 196 × 90 units at (158, 290). Motion strokes are at x=216/256/296, ending at y=262; the middle stroke is longer. Preserve those three strokes, play symbol and compressed proportions when resizing.

## Assets

- [Editable mark SVG](../assets/brand/campresso-mark.svg) and [transparent 1024px PNG](../assets/brand/campresso-mark.png).
- [Editable name/tagline lockup SVG](../assets/brand/campresso-lockup.svg) and [transparent 2400px PNG](../assets/brand/campresso-lockup.png).
- [1024px application-icon PNG](../assets/brand/campresso-icon.png), [32px preview](../assets/brand/campresso-icon-32.png) and [macOS ICNS](../assets/brand/CamPresso.icns).

The ICNS contains standard and Retina PNG representations from 16px through 1024px. The SVG and native raster paths share the documented geometry; exports use sRGB. The lockup SVG keeps editable text, so its exact font metrics depend on the viewer; the PNG freezes the native rendering.

## Regenerate

On macOS, from the checkout:

```bash
xcrun swift scripts/export_brand_assets.swift assets/brand
```

The exporter uses AppKit/Core Graphics and writes the ICNS container directly. It does not launch CamPresso, use Photos or require an image-generation service. It overwrites only the named brand exports. The app build copies the icon and mark into bundle resources before signing. Inspect the large icon, small icon and lockup after changing geometry.

Historical names are retained only for compatibility with the previous installation and its recovery records. Do not replace recorded library/journal/media paths as part of branding.
