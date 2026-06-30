# Changelog

All notable changes to grawji are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-30

First public release.

### Added

- Live preview through the camera's own conversion engine over USB.
- Full recipe control: film simulation, white balance (incl. the Fuji WB-shift
  grid and Kelvin presets), dynamic range, exposure, highlights, shadows,
  color, sharpness, grain and noise reduction.
- Recipes: save, apply and delete named recipes, plus import and export in
  X RAW Studio's FP format (FP1/FP2/FP3).
- Processor-aware tone ranges read from the profile's IOPCode.
- Filmstrip browser with EXIF info, single and full-folder batch export at
  full resolution, EXIF preserved on export.
- Flatpak packaging: a one-command install that bundles GTK4, the EXIF and USB
  stacks, rawji and grawji.

[Unreleased]: https://github.com/p5k369/grawji/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/p5k369/grawji/releases/tag/v0.1.0
