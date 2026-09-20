# Fujifilm body feature matrix

Recipe-relevant features per body, for every camera grawji can possibly talk
to. The inclusion criterion is **USB RAW conversion support** (the X RAW
Studio protocol rawji speaks): everything from the X-Processor Pro generation
onward. Older bodies (X-Pro1, X-E1/E2/E2S, X-T1/T10, X100/S/T, X70, X-M1 and
all X-A/X-T100/X-T200 models) have no USB RAW conversion at all and can never
work with grawji.

Legend: `Y` available · `fw` added later via firmware · `-` absent.
In the `Output` column `-` means the body writes JPEG only.
Every cell assumes the body's **latest firmware**, features delivered by
updates count as present (and `grawji.capabilities` assumes the same).
In-camera availability is the best proxy for what the USB engine honors,
but they are not the same thing. A `✓` in `Verified` marks a body whose row has
been checked over USB, by me on the hardware or through a user's report.
Every other row is read from the manuals and the capability database.

**This table is code:** `grawji.capabilities` encodes it as a per-model
tier table keyed on the RAF's EXIF model (the RAF is provably from the
connected body). An unknown model falls back to the X-Pro2 baseline row,
and the live profile can only narrow a row, never widen it.

## X series

| Body | Year | Processor | Grain | Grain size | Color Chrome | FX Blue | Clarity | Smooth skin | 0.5-tone | Banks | Newest film sim | Output | Verified |
|------|------|-----------|-------|------------|--------------|---------|---------|-------------|----------|-------|-----------------|--------|----------|
| X-Pro2 | 2016 | X-Processor Pro | Y | - | - | - | - | - | - | 7 | Acros | - |   |
| X-T2 | 2016 | X-Processor Pro | Y | - | - | - | - | - | - | 7 | Acros | - |   |
| X100F | 2017 | X-Processor Pro | Y | - | - | - | - | - | - | 7 | Acros | - | ✓ |
| X-T20 | 2017 | X-Processor Pro | Y | - | - | - | - | - | - | 7 | Acros | - |   |
| X-E3 | 2017 | X-Processor Pro | Y | - | - | - | - | - | - | 7 | Acros | - |   |
| X-H1 | 2018 | X-Processor Pro | Y | - | - | - | - | - | - | 7 | Eterna | - |   |
| X-T3 | 2018 | X-Processor 4 | Y | - | Y | - | - | - | - | 7 | Eterna | - | ✓ |
| X-T30 | 2019 | X-Processor 4 | Y | - | Y | - | - | - | - | 7 | Eterna | - |   |
| X-Pro3 | 2019 | X-Processor 4 | Y | Y | Y | Y | Y | - | - | 7 | Classic Neg | TIFF 8/16 |   |
| X100V | 2020 | X-Processor 4 | Y | Y | Y | Y | Y | - | - | 7 | Classic Neg | - |   |
| X-T4 | 2020 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | 7 | Eterna Bleach Bypass | TIFF 8/16 |   |
| X-S10 | 2020 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | 4 | Eterna Bleach Bypass | - |   |
| X-E4 | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | 7 | Eterna Bleach Bypass | - |   |
| X-T30 II | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | 7 | Eterna Bleach Bypass | - |   |
| X-H2S | 2022 | X-Processor 5 | Y | Y | Y | Y | Y | - | Y | 7 | Reala Ace (fw) | TIFF 8/16, HEIF |   |
| X-H2 | 2022 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 7 | Reala Ace (fw) | TIFF 8/16, HEIF |   |
| X-T5 | 2022 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 7 | Reala Ace (fw) | TIFF 8/16, HEIF |   |
| X-S20 | 2023 | X-Processor 5 | Y | Y | Y | Y | Y | - | Y | 4 | Reala Ace (fw) | TIFF 8/16, HEIF |   |
| X100VI | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 7 | Reala Ace | TIFF 8/16, HEIF |   |
| X-T50 | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 7 + 3 ** | Reala Ace | TIFF 8/16, HEIF |   |
| X-M5 * | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | - | Y | 4 + 3 ** | Reala Ace | TIFF 8/16, HEIF |   |
| X-E5 | 2025 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 7 + 3 ** | Reala Ace | TIFF 8/16, HEIF | ✓ |
| X-T30 III * | 2025 | X-Processor 5 | Y | Y | Y | Y | Y | - | Y | 7 | Reala Ace | TIFF 8/16, HEIF |   |

`*` X-M5 and X-T30 III pair the X-Processor 5 with the older X-Trans 4
sensor. Feature set follows the processor.

`**` Three of these are the FS1 to FS3 positions on the film simulation
dial, which only these bodies have. They are set in IMAGE QUALITY
SETTING > FILM SIMULATION DIAL SETTING, not as custom banks, and hold a
film simulation with its effects. grawji writes them on the **X-E5**
only: they live at body-specific offsets in the settings blob, and that
is the one body whose layout is mapped and hardware-verified.

## GFX series

| Body | Year | Processor | Grain | Grain size | Color Chrome | FX Blue | Clarity | Smooth skin | 0.5-tone | Banks | Newest film sim | Output | Verified |
|------|------|-----------|-------|------------|--------------|---------|---------|-------------|----------|-------|-----------------|--------|----------|
| GFX 50S | 2017 | X-Processor Pro | Y | - | Y | - | - | fw | - | 7 | Classic Neg (fw) | TIFF 8 |   |
| GFX 50R | 2018 | X-Processor Pro | Y | - | Y | - | - | fw | - | 7 | Classic Neg (fw) | TIFF 8 |   |
| GFX100 | 2019 | X-Processor 4 | Y | Y | Y | fw | Y | Y | Y | 7 | Nostalgic Neg (fw) | TIFF 8/16 |   |
| GFX100S | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | Y | Y | 6 | Nostalgic Neg (debut) | TIFF 8/16 |   |
| GFX 50S II | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | Y | Y | 6 | Nostalgic Neg | TIFF 8/16 |   |
| GFX100 II | 2023 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 6 | Reala Ace (debut) | TIFF 8/16, HEIF |   |
| GFX100S II | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 6 | Reala Ace | TIFF 8/16, HEIF |   |
| GFX100RF | 2025 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | 7 | Reala Ace | TIFF 8/16, HEIF |   |

## Notes on the feature groups

- **The X-T3/X-T30 gap.** Color Chrome Effect arrived with the X-T3, but
  Clarity, grain size, Color Chrome FX Blue, Classic Neg and half-step tone
  arrived one year later with the X-Pro3 and were never backported. The
  X-T3/X-T30 are the only X-Processor 4 bodies without them.
- **Nostalgic Neg** debuted on the GFX100S (still X-Processor 4), then
  shipped in every X-Processor 5 body — so it is not a clean processor-
  generation split.
- **Reala Ace** debuted on the GFX100 II and ships on 2024+ bodies.
  Fujifilm backported it to the X-H2S/X-H2/X-T5/X-S20 in the firmware
  round of 2024-06-27 (v7.00 / v5.00 / v4.00 / v3.00). USB rendering
  with the sim was confirmed on the X-S20 in the issue #108.
- **Smooth Skin Effect** debuted on the GFX100 and was backported to the
  GFX 50S/50R by firmware.
- **Custom settings over USB** need the PTP preset properties (0xD18C
  to 0xD1A5). The X100F and X-T3 lack them, so grawji patches their
  settings blob instead. From the X-Pro3 on, newer bodies should take
  the preset path, but only the X-E5 and the X-S20 are tested.
- **Custom banks** 7 on every body, except the PASM-mode-dial models
  where the banks are the dial's C positions, 4 on the X-S10, X-S20
  and X-M5, 6 on the GFX100S, GFX 50S II, GFX100 II and GFX100S II.
  Verified against every body's manual, not readable from the camera.
- **GFX 50S/50R firmware** (v4.00 / v2.00, 2020) added Eterna, Classic
  Neg and Smooth Skin in one bundle.

## Sources

- USB RAW conversion support: [X RAW Studio compatibility chart](https://www.fujifilm-x.com/global/support/compatibility/software/x-raw-studio/)
  (currently omits the X-T3/X-T30, both do support it)
- Per-body feature columns: the official
  [per-camera manuals](https://fujifilm-dsc.com/en/manual/),
  each model's IMAGE QUALITY SETTING page
- Firmware-added features (`fw` cells): the official
  [firmware histories](https://www.fujifilm-x.com/global/support/download/firmware/cameras/),
  release notes per model
- Film sim debuts: [Acros](https://www.fujifilm-x.com/global/stories/the-newest-film-simulation-acros/),
  [Eterna](https://www.fujifilm-x.com/en-us/stories/x-h1-development-story-4/),
  [Classic Neg](https://www.fujifilm-x.com/en-us/stories/x-pro3-stories-2-learning-from-film/),
  [Eterna Bleach Bypass](https://www.fujifilm-x.com/global/stories/tales-of-the-x-t4-tale-3-eterna-bleach-bypass/),
  [Nostalgic Neg](https://www.fujifilm.com/de/en/news/hq/5890),
  [Reala Ace](https://www.fujifilm.com/de/en/news/hq/9891)
- Half-step tone (Fujifilm never documents the step size):
  [dpreview X-T4 review](https://www.dpreview.com/reviews/3092032226/fujifilm-x-t4-review/)
- Recipes and film-sim background:
  [Fuji X Weekly](https://fujixweekly.com/recipes/)
