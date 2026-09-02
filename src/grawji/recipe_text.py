"""Parse community text recipes into Recipes."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from grawji.recipe import Recipe

# todo: the german english mix has to be addressed at some point and other
#  languages should also be considered, maybe dynamically

Handler = Callable[[str, dict[str, object], list[str]], None]

# Community names -> rawji FilmSimulation member names.
_FILM_SIMS = {
    "provia": "Provia",
    "proviastandard": "Provia",
    "standard": "Provia",
    "velvia": "Velvia",
    "velviavivid": "Velvia",
    "vivid": "Velvia",
    "astia": "Astia",
    "astiasoft": "Astia",
    "soft": "Astia",
    "classicchrome": "ClassicChrome",
    "classicneg": "ClassicNeg",
    "classicnegative": "ClassicNeg",
    "proneghi": "ProNegHi",
    "proneghigh": "ProNegHi",
    "pronegstd": "ProNegStd",
    "pronegstandard": "ProNegStd",
    "nostalgicneg": "NostalgicNeg",
    "nostalgicnegative": "NostalgicNeg",
    "eterna": "Eterna",
    "eternacinema": "Eterna",
    "cinema": "Eterna",
    "eternableachbypass": "EternaBleach",
    "bleachbypass": "EternaBleach",
    "acros": "Acros",
    "acrosr": "AcrosR",
    "acrosye": "AcrosYe",
    "acrosy": "AcrosYe",
    "acrosg": "AcrosG",
    "monochrome": "Monochrome",
    "monochromer": "MonochromeR",
    "monochromeye": "MonochromeYe",
    "monochromey": "MonochromeYe",
    "monochromeg": "MonochromeG",
    "sepia": "Sepia",
    "realaace": "RealaAce",
    "reala": "RealaAce",
    # German filter suffixes.
    "acrosgelb": "AcrosYe",
    "acrosrot": "AcrosR",
    "acrosgrün": "AcrosG",
    "acrosgruen": "AcrosG",
    "monochrom": "Monochrome",
    "monochromgelb": "MonochromeYe",
    "monochromrot": "MonochromeR",
    "monochromgrün": "MonochromeG",
    "monochromgruen": "MonochromeG",
}

# Community WB names -> rawji WhiteBalance member names. Modes that a
# RAF conversion cannot express map to Auto with a note.
_WB_MODES = {
    "auto": "Auto",
    "daylight": "Daylight",
    "fine": "Daylight",
    "sunny": "Daylight",
    "shade": "Shade",
    "cloudy": "Shade",
    "cloudyshade": "Shade",
    "incandescent": "Incandescent",
    "tungsten": "Incandescent",
    "underwater": "Underwater",
    "fluorescent": "Fluorescent1",
    "fluorescent1": "Fluorescent1",
    "fluorescent2": "Fluorescent2",
    "fluorescent3": "Fluorescent3",
    "asshot": "AsShot",
    "tageslicht": "Daylight",
    "sonnig": "Daylight",
    "bewölkt": "Shade",
    "bewoelkt": "Shade",
    "glühlampe": "Incandescent",
    "gluehlampe": "Incandescent",
    "kunstlicht": "Incandescent",
    "automatisch": "Auto",
}
_WB_AUTO_VARIANTS = ("autowhitepriority", "whitepriority", "ambiencepriority")

_LEVELS = {
    "off": "Off",
    "weak": "Weak",
    "strong": "Strong",
    "aus": "Off",
    "schwach": "Weak",
    "stark": "Strong",
}
_SIZES = {
    "small": "Small",
    "large": "Large",
    "klein": "Small",
    "groß": "Large",
    "gross": "Large",
}

# Capture-time lines every published recipe carries
_SILENT_LABELS = (
    "iso",
    "exposurecompensation",
    "exposurecomp",
    "drangepriority",
    "dynamicrangepriority",
    "pushpull",
)

# A bare value pair like "R+2 B-5" or "+2, -5" is (red, blue) / (WC, MG).
_PAIR = 2

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
_KELVIN = re.compile(r"(?:(\d{4,5})\s*k\b|\bk\s*(\d{4,5})\b)", re.IGNORECASE)
_RED_SHIFT = re.compile(r"([+-]?\d+)\s*(?:red|rot)", re.IGNORECASE)
_BLUE_SHIFT = re.compile(r"([+-]?\d+)\s*(?:blue|blau)", re.IGNORECASE)
# Shorthand shifts: "R+4 B-4" / "R: +4, B: -4".
_RED_LETTER = re.compile(r"\br\s*:?\s*([+-]?\d+)", re.IGNORECASE)
_BLUE_LETTER = re.compile(r"\bb\s*:?\s*([+-]?\d+)", re.IGNORECASE)
_WC_VALUE = re.compile(
    r"(?:([+-]?\d+)\s*(?:wc\b|\(?\s*warm)|\bwc\s*:?\s*([+-]?\d+))",
    re.IGNORECASE,
)
_MG_VALUE = re.compile(
    r"(?:([+-]?\d+)\s*(?:mg\b|\(?\s*magenta)" r"|\bm[gd]\s*:?\s*([+-]?\d+))",
    re.IGNORECASE,
)


@dataclass
class ParsedText:
    """The outcome of parsing a text recipe."""

    recipe: Recipe
    title: str = ""
    notes: list[str] = field(default_factory=list)


def _key(text: str) -> str:
    """Normalize a label or value word: lowercase alphanumerics only."""
    return "".join(c for c in text.lower() if c.isalnum())


def _number(value: str) -> float | None:
    """The first signed number in value, or None."""
    match = _NUMBER.search(value)
    return float(match.group()) if match else None


def _is_label(key: str) -> bool:
    """Whether a normalized line is a known field label."""
    return key in _HANDLERS or key in _SILENT_LABELS


def parse_recipe_text(text: str) -> ParsedText | None:
    """Parse a pasted community recipe.

    Returns:
        The recipe, its title and notes about skipped or adjusted
        values, or None when the text contains no recognizable recipe
        field at all.
    """
    parsed = ParsedText(recipe=Recipe())
    fields: dict[str, object] = {}
    recognized = 0
    bare: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"^(?:[•*·]+\s*|-\s+)", "", raw_line.strip()).strip()
        if not line:
            continue
        label, sep, value = line.partition(":")
        if sep and value.strip() and _is_label(_key(label)):
            recognized += _apply_field(
                _key(label), value.strip(), fields, parsed.notes
            )
            continue
        # Web tables paste as "Label<TAB>Value" (or wide spacing)
        columns = re.split(r"\t+|\s{2,}", line, maxsplit=1)
        if len(columns) == _PAIR and _is_label(_key(columns[0])):
            recognized += _apply_field(
                _key(columns[0]), columns[1].strip(), fields, parsed.notes
            )
            continue
        if sep and value.strip():
            parsed.notes.append(f"ignored: {_key(label)}: {value.strip()}")
            continue
        bare.append(line)
    recognized += _pair_bare_lines(bare, fields, parsed)
    if recognized == 0:
        return None
    parsed.recipe = replace(parsed.recipe, **fields)  # type: ignore[arg-type]
    return parsed


def _pair_bare_lines(
    lines: list[str], fields: dict[str, object], parsed: ParsedText
) -> int:
    """Match label lines to adjacent value lines."""
    is_label = [_is_label(_key(line)) for line in lines]
    used = list(is_label)
    recognized = 0

    def free_value(index: int) -> bool:
        return 0 <= index < len(lines) and not used[index]

    def pair(label_at: int, value_at: int) -> int:
        used[value_at] = True
        return _apply_field(
            _key(lines[label_at]), lines[value_at], fields, parsed.notes
        )

    pending = []
    for i, labelled in enumerate(is_label):
        if not labelled:
            continue
        if free_value(i + 1) and not free_value(i - 1):
            recognized += pair(i, i + 1)
        else:
            pending.append(i)
    for i in pending:
        if free_value(i - 1):
            recognized += pair(i, i - 1)
        elif free_value(i + 1):
            recognized += pair(i, i + 1)
    for i, line in enumerate(lines):
        if used[i] or "film_simulation" in fields:
            continue
        key = _key(line).removesuffix("filter")
        if key in _FILM_SIMS:
            fields["film_simulation"] = _FILM_SIMS[key]
            used[i] = True
            recognized += 1
    if not parsed.title and lines and not is_label[0] and not used[0]:
        parsed.title = lines[0]
    return recognized


def _apply_field(
    label: str, value: str, fields: dict[str, object], notes: list[str]
) -> int:
    """Route one "Label: value" line."""
    if label in _SILENT_LABELS:
        return 1
    handler = _HANDLERS.get(label)
    if handler is None:
        notes.append(f"ignored: {label}: {value}")
        return 0
    handler(value, fields, notes)
    return 1


def _set_film(value: str, fields: dict[str, object], notes: list[str]) -> None:
    """Film simulation by community name."""
    key = _key(value)
    name = _FILM_SIMS.get(key) or _FILM_SIMS.get(key.removesuffix("filter"))
    if name is None:
        notes.append(f"unknown film simulation: {value}")
        return
    fields["film_simulation"] = name


def _set_grain(
    value: str, fields: dict[str, object], notes: list[str]
) -> None:
    """Grain effect with optional size, any separator or order."""
    words = [_key(w) for w in re.split(r"[,/&\s]+", value) if w.strip()]
    level = next((_LEVELS[w] for w in words if w in _LEVELS), None)
    size = next((_SIZES[w] for w in words if w in _SIZES), None)
    if level is None:
        notes.append(f"unknown grain: {value}")
        return
    fields["grain"] = level
    if size is not None:
        fields["grain_size"] = size


def _set_grain_size(
    value: str, fields: dict[str, object], notes: list[str]
) -> None:
    """A separate grain-size line."""
    size = _SIZES.get(_key(value))
    if size is None:
        notes.append(f"unknown grain size: {value}")
        return
    fields["grain_size"] = size


def _level_setter(field_name: str) -> Handler:
    """A handler storing an Off/Weak/Strong value under field_name."""

    def setter(
        value: str, fields: dict[str, object], notes: list[str]
    ) -> None:
        level = _LEVELS.get(_key(value))
        if level is None:
            notes.append(f"unknown {field_name}: {value}")
            return
        fields[field_name] = level

    return setter


def _value_setter(field_name: str, *, half_steps: bool = False) -> Handler:
    """A handler storing the line's first signed number."""

    def setter(
        value: str, fields: dict[str, object], notes: list[str]
    ) -> None:
        number = _number(value)
        if number is None:
            notes.append(f"unreadable {field_name}: {value}")
            return
        if half_steps:
            fields[field_name] = round(number * 2) / 2
        else:
            fields[field_name] = round(number)

    return setter


def _shift_matches(value: str) -> tuple[re.Match[str] | None, ...]:
    """The red, blue shift matches, words first, letters second."""
    red = _RED_SHIFT.search(value) or _RED_LETTER.search(value)
    blue = _BLUE_SHIFT.search(value) or _BLUE_LETTER.search(value)
    return red, blue


def _set_wb(value: str, fields: dict[str, object], notes: list[str]) -> None:
    """White balance: a mode or kelvin, plus optional R/B shifts."""
    red, blue = _shift_matches(value)
    if red:
        fields["wb_shift_r"] = int(red.group(1))
    if blue:
        fields["wb_shift_b"] = int(blue.group(1))
    mode_text = re.split(r"[,(]", value, maxsplit=1)[0]
    kelvin = _KELVIN.search(mode_text)
    if kelvin:
        fields["white_balance"] = "Temperature"
        fields["color_temp"] = int(kelvin.group(1) or kelvin.group(2))
        return
    key = _key(mode_text)
    if key in _WB_AUTO_VARIANTS:
        fields["white_balance"] = "Auto"
        notes.append(
            f"{mode_text.strip()} is a capture-time auto-WB bias; "
            "the conversion knows plain Auto"
        )
        return
    mode = _WB_MODES.get(key)
    if mode is None:
        notes.append(f"unknown white balance: {mode_text.strip()}")
        return
    fields["white_balance"] = mode


def _set_wb_shift(
    value: str, fields: dict[str, object], notes: list[str]
) -> None:
    """A separate WB-shift line ("+2 Red & -5 Blue" or "R+2 B-5")."""
    red, blue = _shift_matches(value)
    if red:
        fields["wb_shift_r"] = int(red.group(1))
    if blue:
        fields["wb_shift_b"] = int(blue.group(1))
    if not red and not blue:
        numbers = _NUMBER.findall(value)
        if len(numbers) == _PAIR:
            fields["wb_shift_r"] = int(float(numbers[0]))
            fields["wb_shift_b"] = int(float(numbers[1]))
        else:
            notes.append(f"unreadable WB shift: {value}")


def _set_dr(value: str, fields: dict[str, object], notes: list[str]) -> None:
    """Dynamic range: DR100/DR200/DR400."""
    # todo: auto should be checked at some point after rawji gets the DR update
    key = _key(value)
    for amount in ("400", "200", "100"):
        if amount in key:
            fields["dynamic_range"] = f"DR{amount}"
            return
    if "auto" in key:
        notes.append("Dynamic Range Auto is capture-time; kept DR100")
        return
    notes.append(f"unknown dynamic range: {value}")


def _set_mono(value: str, fields: dict[str, object], notes: list[str]) -> None:
    """Monochromatic Color: WC and MG, in any of the common spellings."""
    wc = _WC_VALUE.search(value)
    mg = _MG_VALUE.search(value)
    if wc:
        fields["mono_warm_cool"] = int(wc.group(1) or wc.group(2))
    if mg:
        fields["mono_magenta_green"] = int(mg.group(1) or mg.group(2))
    if wc or mg:
        return
    numbers = _NUMBER.findall(value)
    if len(numbers) == _PAIR:
        fields["mono_warm_cool"] = int(float(numbers[0]))
        fields["mono_magenta_green"] = int(float(numbers[1]))
    elif len(numbers) == 1:
        fields["mono_warm_cool"] = int(float(numbers[0]))
    else:
        notes.append(f"unreadable monochromatic color: {value}")


_HANDLERS: dict[str, Handler] = {}


def _register() -> None:
    """Build the label routing table."""
    _HANDLERS.update(
        {
            "filmsimulation": _set_film,
            "filmsim": _set_film,
            "grain": _set_grain,
            "graineffect": _set_grain,
            "grainsize": _set_grain_size,
            "colorchrome": _level_setter("color_chrome"),
            "colorchromeeffect": _level_setter("color_chrome"),
            "colorchromefx": _level_setter("color_chrome"),
            "colorchromeeffectblue": _level_setter("color_chrome_blue"),
            "colorchromefxblau": _level_setter("color_chrome_blue"),
            "colorchromefxblue": _level_setter("color_chrome_blue"),
            "colorchromeblue": _level_setter("color_chrome_blue"),
            "smoothskin": _level_setter("smooth_skin"),
            "smoothskineffect": _level_setter("smooth_skin"),
            "whitebalance": _set_wb,
            "wb": _set_wb,
            "whitebalanceshift": _set_wb_shift,
            "waverschieben": _set_wb_shift,
            "wbverschieben": _set_wb_shift,
            "wbshift": _set_wb_shift,
            "autowbshift": _set_wb_shift,
            "wbshiftautowb": _set_wb_shift,
            "dynamicrange": _set_dr,
            "highlight": _value_setter("highlights", half_steps=True),
            "highlights": _value_setter("highlights", half_steps=True),
            "highlighttone": _value_setter("highlights", half_steps=True),
            "shadow": _value_setter("shadows", half_steps=True),
            "shadows": _value_setter("shadows", half_steps=True),
            "shadowtone": _value_setter("shadows", half_steps=True),
            "color": _value_setter("color"),
            "saturation": _value_setter("color"),
            "sharpness": _value_setter("sharpness"),
            "sharpening": _value_setter("sharpness"),
            "noisereduction": _value_setter("noise_reduction"),
            "noisered": _value_setter("noise_reduction"),
            "highisonr": _value_setter("noise_reduction"),
            "nr": _value_setter("noise_reduction"),
            "clarity": _value_setter("clarity"),
            "monochromaticcolor": _set_mono,
            "monochromefarbe": _set_mono,
            "monochromfarbe": _set_mono,
            "toning": _set_mono,
            # German labels
            "weißabgleich": _set_wb,
            "weissabgleich": _set_wb,
            "farbtemperatur": _set_wb,
            "dynamikbereich": _set_dr,
            "dynamikumfang": _set_dr,
            "körnung": _set_grain,
            "koernung": _set_grain,
            "körnungseffekt": _set_grain,
            "koernungseffekt": _set_grain,
            "lichter": _value_setter("highlights", half_steps=True),
            "tonlichter": _value_setter("highlights", half_steps=True),
            "schatten": _value_setter("shadows", half_steps=True),
            "schattierton": _value_setter("shadows", half_steps=True),
            "farbe": _value_setter("color"),
            "sättigung": _value_setter("color"),
            "saettigung": _value_setter("color"),
            "schärfe": _value_setter("sharpness"),
            "schaerfe": _value_setter("sharpness"),
            "rauschreduzierung": _value_setter("noise_reduction"),
            "hoheisonr": _value_setter("noise_reduction"),
            "rauschunterdrückung": _value_setter("noise_reduction"),
            "rauschunterdrueckung": _value_setter("noise_reduction"),
            "klarheit": _value_setter("clarity"),
        }
    )


_register()
