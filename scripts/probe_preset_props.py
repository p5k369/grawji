"""Probe the gen5 custom-preset PTP properties over USB.

Usage:
    python scripts/probe_preset_props.py caps
    python scripts/probe_preset_props.py list
    python scripts/probe_preset_props.py write 7 recipe.json \
        --name PROBE --write-settings
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import rawji

from grawji.camera import camera_presets, preset_recipe
from grawji.camera.camera_backup import setup
from grawji.camera.camera_presets import _get_prop, _read_name
from grawji.recipe import Recipe

# PTP device properties are u16. Values at or past this read as signed.
_SIGN_BIT = 0x8000
# Fuji vendor properties start here.
_VENDOR_PROPS = 0xD000

_PROP_NAMES = {
    preset_recipe.PROP_IMAGE_SIZE: "image size",
    preset_recipe.PROP_IMAGE_QUALITY: "image quality",
    preset_recipe.PROP_DYNAMIC_RANGE: "dynamic range %",
    preset_recipe.PROP_UNKNOWN_D191: "unknown d191",
    preset_recipe.PROP_FILM_SIMULATION: "film simulation",
    preset_recipe.PROP_MONO_WC: "mono warm-cool x10",
    preset_recipe.PROP_MONO_MG: "mono magenta-green x10",
    preset_recipe.PROP_GRAIN: "grain",
    preset_recipe.PROP_COLOR_CHROME: "color chrome",
    preset_recipe.PROP_COLOR_CHROME_BLUE: "color chrome blue",
    preset_recipe.PROP_SMOOTH_SKIN: "smooth skin",
    preset_recipe.PROP_WHITE_BALANCE: "white balance",
    preset_recipe.PROP_WB_SHIFT_R: "WB shift R",
    preset_recipe.PROP_WB_SHIFT_B: "WB shift B",
    preset_recipe.PROP_WB_COLOR_TEMP: "WB color temp K",
    preset_recipe.PROP_HIGHLIGHT: "highlight x10",
    preset_recipe.PROP_SHADOW: "shadow x10",
    preset_recipe.PROP_COLOR: "color x10",
    preset_recipe.PROP_SHARPNESS: "sharpness x10",
    preset_recipe.PROP_NOISE_REDUCTION: "noise reduction",
    preset_recipe.PROP_CLARITY: "clarity x10",
    preset_recipe.PROP_LONG_EXPOSURE_NR: "long exposure NR",
    preset_recipe.PROP_COLOR_SPACE: "color space",
    preset_recipe.PROP_UNKNOWN_D1A5: "unknown d1a5",
}

_FILM_SIMS = {int(e): e.name for e in rawji.FilmSimulation}
# Preset WB codes beyond rawji's enum, from the filmkit capture.
_WB_MODES = {int(e): e.name for e in rawji.WhiteBalance} | {
    0x8021: "AutoAmbiencePriority",
}

# DR Auto is stored as 0xFFFF, not a percentage.
_DR_AUTO = 0xFFFF


def _connect() -> rawji.FujiCamera:
    """Connect to the camera."""
    cam = rawji.FujiCamera()
    if not cam.connect():
        sys.exit("could not connect (camera in RAW CONV / BACKUP mode?)")
    return cam


def _decorate(prop: int, value: int) -> str:
    """A human-readable rendering of a preset property value."""
    if prop == preset_recipe.PROP_FILM_SIMULATION:
        return str(_FILM_SIMS.get(value, "?"))
    if prop == preset_recipe.PROP_WHITE_BALANCE:
        return str(_WB_MODES.get(value, "?"))
    if prop == preset_recipe.PROP_DYNAMIC_RANGE and value == _DR_AUTO:
        return "Auto"
    if value == preset_recipe.UNSET_SENTINEL:
        return "(unset sentinel)"
    signed = value - 0x10000 if value >= _SIGN_BIT else value
    return str(signed) if signed != value else ""


def cmd_caps(_args: argparse.Namespace) -> None:
    """Report whether the body advertises the preset properties."""
    cam = _connect()
    try:
        info = setup(cam)
    finally:
        cam.disconnect()
    props = sorted(p for p in info.props if p >= _VENDOR_PROPS)
    print(f"model: {info.model}")
    print("fuji properties: " + " ".join(f"0x{p:04x}" for p in props))
    if camera_presets.supports_presets(info.props):
        print("preset slot 0xd18c ADVERTISED: the gen5 path applies")
    else:
        print("preset slot 0xd18c NOT advertised: gen5 path unavailable")


def cmd_list(_args: argparse.Namespace) -> None:
    """Dump every slot's name and property values."""
    cam = _connect()
    try:
        info = setup(cam)
        if not camera_presets.supports_presets(info.props):
            sys.exit(f"{info.model} does not advertise the preset props")
        original = camera_presets._active_slot(cam)
        try:
            for slot in range(preset_recipe.NUM_SLOTS):
                camera_presets._select_slot(cam, slot)
                name = _read_name(cam)
                print(f"C{slot + 1}  {name or '(unnamed)'}")
                for prop, label in _PROP_NAMES.items():
                    value = _get_prop(cam, prop)
                    if value is None:
                        print(f"    0x{prop:04x}  (unreadable)")
                        continue
                    extra = _decorate(prop, value)
                    tail = f"  {extra}" if extra else ""
                    print(f"    0x{prop:04x}  {value:#06x}{tail}  {label}")
        finally:
            camera_presets._restore_slot(cam, original)
    finally:
        cam.disconnect()


def cmd_write(args: argparse.Namespace) -> None:
    """Write one recipe file into one slot."""
    if not args.write_settings:
        sys.exit(
            "refusing to write without --write-settings; run `list` "
            "first and confirm the decoded values on the camera body"
        )
    recipe = Recipe.from_dict(json.loads(Path(args.recipe).read_text()))
    # An explicit empty --name clears the slot name.
    names = {args.slot - 1: args.name} if args.name is not None else None
    cam = _connect()
    try:
        info = setup(cam)
        if not camera_presets.supports_presets(info.props):
            sys.exit(f"{info.model} does not advertise the preset props")
        result = camera_presets.transfer_presets(
            cam,
            {args.slot - 1: recipe},
            names=names,
            model=info.model,
        )
    finally:
        cam.disconnect()
    print(
        f"wrote C{args.slot} on the {result.model}: "
        f"{result.applied} properties confirmed"
    )
    for slot, notes in result.dropped.items():
        print(f"C{slot + 1} notes: " + "; ".join(notes))
    print("now check the preset on the camera body")


def main() -> None:
    """Parse arguments and dispatch to a probe command."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("caps").set_defaults(func=cmd_caps)
    sub.add_parser("list").set_defaults(func=cmd_list)

    write = sub.add_parser("write")
    write.add_argument("slot", type=int, choices=range(1, 8))
    write.add_argument("recipe", help="recipe JSON file (grawji format)")
    write.add_argument("--name", help="new slot name")
    write.add_argument("--write-settings", action="store_true")
    write.set_defaults(func=cmd_write)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
