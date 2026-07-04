"""Hardware verification of candidate rawji profile offsets.

Run with the camera connected and a RAF from the *connected body*:

    python scripts/verify_offsets.py 20240612_0413.RAF
"""

from __future__ import annotations

import io
import struct
import sys

import numpy as np
import rawji
from PIL import Image
from rawji.fuji_profile import (
    INDEX_TO_PARAM,
    PROFILE_PARAMS_OFFSET,
    PROFILE_SIZE_STANDARD,
)

_inv = (
    {v: k for k, v in INDEX_TO_PARAM.items()}
    if isinstance(INDEX_TO_PARAM, dict)
    else {n: i for i, n in enumerate(INDEX_TO_PARAM)}
)


def off(name: str) -> int:
    """Byte offset of a parameter per INDEX_TO_PARAM."""
    return PROFILE_PARAMS_OFFSET + _inv[name] * 4


def slot_name(i: int) -> str:
    """Parameter name for slot index i, or '?' if unknown."""
    if isinstance(INDEX_TO_PARAM, dict):
        return INDEX_TO_PARAM.get(i, "?")
    return INDEX_TO_PARAM[i] if i < len(INDEX_TO_PARAM) else "?"


def main(path: str) -> None:  # noqa: PLR0915
    cam = rawji.FujiCamera()
    if not cam.connect():
        sys.exit("could not connect")
    try:
        cam.send_raf(path)
        base = cam.get_profile()
        print(f"profile: {len(base)} bytes; RAF {path}\n")

        def render(
            patches: dict[int, int], base_bytes: bytes | None = None
        ) -> np.ndarray:
            out = bytearray(base if base_bytes is None else base_bytes)
            for offset, val in patches.items():
                raw = (1 << 32) + val if val < 0 else val
                struct.pack_into("<I", out, offset, raw)
            cam.set_profile(bytes(out))
            cam.trigger_conversion(full_resolution=False)
            jpg = cam.wait_for_result(30)
            im = Image.open(io.BytesIO(jpg)).convert("RGB")
            return np.asarray(im, dtype=float)

        def stats(a: np.ndarray) -> str:
            r, g, b = a[..., 0].mean(), a[..., 1].mean(), a[..., 2].mean()
            lum = a.mean()
            # high-freq energy: stddev of a simple Laplacian, for grain/clarity
            gray = a.mean(axis=2)
            lap = (
                -4 * gray[1:-1, 1:-1]
                + gray[:-2, 1:-1]
                + gray[2:, 1:-1]
                + gray[1:-1, :-2]
                + gray[1:-1, 2:]
            )
            # Mean chroma (max-min per pixel): Color Chrome deepens
            # saturation, so this catches it where a luma metric would not.
            sat = (a.max(axis=2) - a.min(axis=2)).mean()
            return (
                f"lum={lum:6.2f} R={r:6.2f} G={g:6.2f} B={b:6.2f} "
                f"sat={sat:6.2f} hf={lap.std():6.2f}"
            )

        def sat_of(a: np.ndarray) -> float:
            return float((a.max(axis=2) - a.min(axis=2)).mean())

        def dump_profile() -> None:
            """Show the raw profile bytes and per-slot values."""
            print(f"raw profile ({len(base)} bytes):")
            print(base.hex(" ", 4))
            n_slots = (len(base) - PROFILE_PARAMS_OFFSET) // 4
            print(f"\nparam slots (offset 513 + i*4), {n_slots} slots:")
            for i in range(n_slots):
                offset = PROFILE_PARAMS_OFFSET + i * 4
                (val,) = struct.unpack_from("<i", base, offset)
                print(f"  idx {i:2d} @ {offset}: {val:6d}  {slot_name(i)}")
            print()

        def sweep_chroma() -> None:
            """Toggle every slot Off/Strong and report the chroma change."""
            base_sat = sat_of(base_img)
            print("chroma sweep (each slot 0 -> 2):")
            n_slots = (len(base) - PROFILE_PARAMS_OFFSET) // 4
            for i in range(n_slots):
                offset = PROFILE_PARAMS_OFFSET + i * 4
                try:
                    hi = render({offset: 2})
                except Exception as exc:
                    print(f"  idx {i:2d} @ {offset}: ERROR {exc}")
                    continue
                dsat = sat_of(hi) - base_sat
                print(f"  idx {i:2d} @ {offset}: dSat={dsat:+6.2f}")
            print()

        def sweep_extended() -> None:
            """Probe slots 23-28 that only exist in the 632-byte profile.

            The X-T3 returns a truncated 23-slot profile, Color Chrome may
            live in a higher slot present only in the full format. Extend the
            real profile (RMW) so the RAF's recipe is kept intact.
            """
            ext = bytearray(PROFILE_SIZE_STANDARD)
            ext[: len(base)] = base
            struct.pack_into("<H", ext, 0, 29)  # declare all 29 params
            try:
                base_ext = render({}, bytes(ext))
            except Exception as exc:
                print(f"extended profile rejected: {exc}\n")
                return
            print(f"{'ext baseline':<22} {stats(base_ext)}")
            base_sat = sat_of(base_ext)
            for i in range(23, 29):
                offset = PROFILE_PARAMS_OFFSET + i * 4
                for val in (1, 2, 3):
                    try:
                        hi = render({offset: val}, bytes(ext))
                    except Exception as exc:
                        print(f"  idx {i} @ {offset} ={val}: ERROR {exc}")
                        continue
                    dsat = sat_of(hi) - base_sat
                    print(
                        f"  idx {i} @ {offset} ={val}: "
                        f"dSat={dsat:+6.2f}  {stats(hi)}"
                    )
            print()

        dump_profile()
        base_img = render({})
        print(f"{'baseline':<22} {stats(base_img)}\n")
        if "sweep" in sys.argv:
            sweep_chroma()
        if "extended" in sys.argv:
            sweep_extended()
        if "sweep" in sys.argv or "extended" in sys.argv:
            return

        wb_temp = int(rawji.WhiteBalance["Temperature"])

        # (label, patches-low, patches-high)
        cases = [
            (
                "ExposureBias -2/+2EV",
                {off("ExposureBias"): -2000},
                {off("ExposureBias"): 2000},
            ),
            ("WBShiftR -9/+9", {off("WBShiftR"): -9}, {off("WBShiftR"): 9}),
            ("WBShiftB -9/+9", {off("WBShiftB"): -9}, {off("WBShiftB"): 9}),
            (
                "WBColorTemp 2500/10000",
                {
                    off("WhiteBalance"): wb_temp,
                    off("WBShootCond"): 2,
                    off("WBColorTemp"): 2500,
                },
                {
                    off("WhiteBalance"): wb_temp,
                    off("WBShootCond"): 2,
                    off("WBColorTemp"): 10000,
                },
            ),
            (
                "GrainEffect Off/Strong",
                {off("GrainEffect"): 1},
                {off("GrainEffect"): 3},
            ),
            (
                "SmoothSkin Off/Strong",
                {off("SmoothSkinEffect"): 0},
                {off("SmoothSkinEffect"): 3},
            ),
            (
                "NoiseReduction -4/+4",
                {off("NoiseReduction"): -40},
                {off("NoiseReduction"): 40},
            ),
            ("Clarity -5/+5", {off("Clarity"): -50}, {off("Clarity"): 50}),
            ("ColorSpace 0/1", {off("ColorSpace"): 0}, {off("ColorSpace"): 1}),
        ]
        for label, lo, hi in cases:
            try:
                a = render(lo)
                b = render(hi)
                print(f"{label:<22} LOW  {stats(a)}")
                print(f"{'':<22} HIGH {stats(b)}")
                print(
                    f"{'':<22} d-lum={b.mean() - a.mean():+6.2f} "
                    f"dR={b[..., 0].mean() - a[..., 0].mean():+6.2f} "
                    f"dB={b[..., 2].mean() - a[..., 2].mean():+6.2f}\n"
                )
            except Exception as exc:
                print(f"{label:<22} ERROR {exc}\n")
    finally:
        cam.disconnect()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "")
