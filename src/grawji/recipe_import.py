"""Import many recipes into the library at once."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path

from grawji.recipe import Recipe
from grawji.recipe_dedup import canonical_look
from grawji.recipes import UNGROUPED, RecipeLibrary

FP_SUFFIXES = (".fp1", ".fp2", ".fp3")
PHOTO_SUFFIXES = (".jpg", ".jpeg", ".hif", ".heif", ".jxl", ".raf")


@dataclass(frozen=True)
class Candidate:
    """A recipe read from a file, on its way into the library.

    Attributes:
        name: The name to store it under, before making it unique.
        recipe: The recipe read.
        folder: The library folder it belongs in, or None for the
            import's default folder.
    """

    name: str
    recipe: Recipe
    folder: str | None = None


@dataclass
class ImportReport:
    """What an import did.

    Attributes:
        added: The names the new recipes were stored under.
        duplicates: The candidates skipped because the library already
            holds their look.
        failed: The files that held no readable recipe.
    """

    added: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One line for a toast."""
        if not (self.added or self.duplicates or self.failed):
            return "Nothing to import."
        parts = [f"{len(self.added)} imported"]
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} already in the library")
        if self.failed:
            parts.append(f"{len(self.failed)} not readable")
        return ", ".join(parts) + "."


def is_recipe_file(path: Path) -> bool:
    """Whether a file may carry a recipe."""
    return path.suffix.lower() in FP_SUFFIXES + PHOTO_SUFFIXES


def recipe_files(root: Path) -> list[tuple[Path, str | None]]:
    """The recipe files under a folder."""
    found: list[tuple[Path, str | None]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_recipe_file(path):
            continue
        parts = path.relative_to(root).parts
        if any(part.startswith(".") for part in parts):
            continue
        found.append((path, parts[0] if len(parts) > 1 else None))
    return found


def unique_name(name: str, taken: set[str]) -> str:
    """The name itself, or the first numbered variant that is free."""
    base = name.strip() or "Recipe"
    if base not in taken:
        return base
    number = 2
    while f"{base} {number}" in taken:
        number += 1
    return f"{base} {number}"


def add_candidates(
    library: RecipeLibrary,
    candidates: list[Candidate],
    default_folder: str = UNGROUPED,
) -> ImportReport:
    """Store the candidates whose look the library does not hold yet."""
    report = ImportReport()
    taken = set(library.names)
    looks = {
        canonical_look(recipe)
        for name in library.names
        if (recipe := library.get(name)) is not None
    }
    for candidate in candidates:
        recipe = replace(candidate.recipe, exposure=0.0)
        look = canonical_look(recipe)
        if look in looks:
            report.duplicates.append(candidate.name)
            continue
        name = unique_name(candidate.name, taken)
        folder = (
            default_folder if candidate.folder is None else candidate.folder
        )
        library.add(name, recipe, folder=folder)
        taken.add(name)
        looks.add(look)
        report.added.append(name)
    return report
