# Fujifilm body feature matrix

Recipe-relevant features per body, for every camera grawji can possibly talk
to. The inclusion criterion is **USB RAW conversion support** (the X RAW
Studio protocol rawji speaks): everything from the X-Processor Pro generation
onward. Older bodies (X-Pro1, X-E1/E2/E2S, X-T1/T10, X100/S/T, X70, X-M1 and
all X-A/X-T100/X-T200 models) have no USB RAW conversion at all and can never
work with grawji.

Legend: `Y` available · `fw` added later via firmware · `-` absent.
In-camera availability is the best proxy for what the USB engine honours,
but they are not the same thing,  the **USB-verified** section below is the
ground truth I have measured.

**This table is code:** `grawji.capabilities` encodes it as a per-model
tier table keyed on the RAF's EXIF model (the RAF is provably from the
connected body). An unknown model falls back to the X-Pro2 baseline row,
and the live profile can only narrow a row, never widen it.

## X series

| Body | Year | Processor | Grain | Grain size | Color Chrome | FX Blue | Clarity | Smooth skin | 0.5-tone | Newest film sim |
|------|------|-----------|-------|------------|--------------|---------|---------|-------------|----------|-----------------|
| X-Pro2 | 2016 | X-Processor Pro | Y | - | - | - | - | - | - | Acros |
| X-T2 | 2016 | X-Processor Pro | Y | - | - | - | - | - | - | Acros |
| X100F | 2017 | X-Processor Pro | Y | - | - | - | - | - | - | Acros |
| X-T20 | 2017 | X-Processor Pro | Y | - | - | - | - | - | - | Acros |
| X-E3 | 2017 | X-Processor Pro | Y | - | - | - | - | - | - | Acros |
| X-H1 | 2018 | X-Processor Pro | Y | - | - | - | - | - | - | Eterna |
| X-T3 | 2018 | X-Processor 4 | Y | - | Y | - | - | - | - | Eterna |
| X-T30 | 2019 | X-Processor 4 | Y | - | Y | - | - | - | - | Eterna |
| X-Pro3 | 2019 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | Classic Neg |
| X100V | 2020 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | Classic Neg |
| X-T4 | 2020 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | Eterna Bleach Bypass |
| X-S10 | 2020 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | Eterna Bleach Bypass |
| X-E4 | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | Eterna Bleach Bypass |
| X-T30 II | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | - | Y | Eterna Bleach Bypass |
| X-H2S | 2022 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Nostalgic Neg |
| X-H2 | 2022 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Nostalgic Neg |
| X-T5 | 2022 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Nostalgic Neg |
| X-S20 | 2023 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Nostalgic Neg |
| X100VI | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |
| X-T50 | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |
| X-M5 * | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |
| X-E5 | 2025 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |
| X-T30 III * | 2025 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |

`*` X-M5 and X-T30 III pair the X-Processor 5 with the older X-Trans 4
sensor; feature set follows the processor.

## GFX series

| Body | Year | Processor | Grain | Grain size | Color Chrome | FX Blue | Clarity | Smooth skin | 0.5-tone | Newest film sim |
|------|------|-----------|-------|------------|--------------|---------|---------|-------------|----------|-----------------|
| GFX 50S | 2017 | X-Processor Pro | Y | - | fw | - | - | fw | - | Acros |
| GFX 50R | 2018 | X-Processor Pro | Y | - | fw | - | - | fw | - | Acros |
| GFX100 | 2019 | X-Processor 4 | Y | Y | Y | fw | Y | Y | Y | Classic Neg (fw), Nostalgic Neg (fw) |
| GFX100S | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | Y | Y | Nostalgic Neg (debut) |
| GFX 50S II | 2021 | X-Processor 4 | Y | Y | Y | Y | Y | Y | Y | Nostalgic Neg |
| GFX100 II | 2023 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace (debut) |
| GFX100S II | 2024 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |
| GFX100RF | 2025 | X-Processor 5 | Y | Y | Y | Y | Y | Y | Y | Reala Ace |

## Notes on the feature groups

- **The X-T3/X-T30 gap.** Color Chrome Effect arrived with the X-T3, but
  Clarity, grain size, Color Chrome FX Blue, Classic Neg and half-step tone
  arrived one year later with the X-Pro3 and were never backported. The
  X-T3/X-T30 are the only X-Processor 4 bodies without them.
- **Nostalgic Neg** debuted on the GFX100S (still X-Processor 4), then
  shipped in every X-Processor 5 body — so it is not a clean processor-
  generation split.
- **Reala Ace** debuted on the GFX100 II and ships on 2024+ bodies. The
  X-H2S/X-H2/X-T5/X-S20 never received it.
- **Smooth Skin Effect** debuted on the GFX100 and was backported to the
  GFX 50S/50R by firmware. Present on every X-Processor 5 body.

## USB-verified ground truth (grawji hardware findings)

In-camera menus tell you what the *body* offers, what the *USB conversion
engine* honours in the d185 profile can differ. Measured so far:

| Body | Profile | IOPCode | Verified via USB                                                                                                                                                            |
|------|---------|---------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| X100F | 601 B, 22 slots | X-Processor Pro | Core params. High-index effect slots absent (Color Chrome @549 present but inert. Body predates the feature).                                                               |
| X-T3 | 605 B, 23 slots | 0xff159501 (X-Processor 4) | Core params + Color Chrome @549 (render-verified). No Clarity/FX Blue/smooth-skin slots.                                                                                    |
| X-E5 | 629 B, 29 slots | 0xff179504 (X-Processor 5) | Everything: Color Chrome @549, grain effect+size combined @545 (Off=1, W/S=2, S/S=3, W/L=4, S/L=5), smooth skin @605, FX Blue @609, Clarity @617 (value*10), 0.5-step tone. |

Sources: Fujifilm X RAW Studio compatibility page, Fujifilm per-camera
online manuals (fujifilm-dsc.com), fujifilm-x.com film-simulation pages,
Fuji X Weekly, Alik Griffin's JPG-effects guide.
