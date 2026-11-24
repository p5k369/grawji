"""Named recipes with JSON storage, organised into folders."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grawji import safefile
from grawji.recipe import Recipe
from grawji.settings import config_dir

# Recipes with no folder live at the top level, keyed by this sentinel.
UNGROUPED = ""

# The number keys a recipe can be assigned to for one-press applying.
HOTKEYS = range(1, 10)


def recipes_path() -> Path:
    """Return the path to the recipes JSON file."""
    return config_dir() / "recipes.json"


def decode_recipes(data: object) -> dict[str, Recipe]:
    """Turn a parsed flat JSON object into a name -> Recipe mapping."""
    if not isinstance(data, dict):
        return {}
    out: dict[str, Recipe] = {}
    for name, value in data.items():
        if isinstance(name, str) and isinstance(value, dict):
            out[name] = Recipe.from_dict(value)
    return out


def recipe_matches(query: str, *fields: str) -> bool:
    """Whether every word of the query occurs in one of the fields."""
    haystack = " ".join(fields).casefold()
    return all(term in haystack for term in query.casefold().split())


@dataclass(frozen=True)
class Removed:
    """A deleted recipe with everything needed to bring it back.

    Attributes:
        name: The name it was stored under.
        recipe: The recipe.
        index: Its position in the display order.
        folder: The folder it was in.
        thumb: Its picture as stored, or None.
        comment: Its comment.
        hotkey: Its number key, or None.
        baseline: Whether it was the compare baseline.
    """

    name: str
    recipe: Recipe
    index: int
    folder: str
    thumb: str | None
    comment: str
    hotkey: int | None
    baseline: bool


class RecipeLibrary:
    """Saved recipes plus their folders and the compare baseline."""

    def __init__(self, path: Path) -> None:
        """Load the library from path (missing/unreadable gives empty)."""
        self._path = path
        self._recipes: dict[str, Recipe] = {}
        self._folder_of: dict[str, str] = {}
        self._thumb_of: dict[str, str] = {}
        self._comment_of: dict[str, str] = {}
        self._hotkey_of: dict[str, int] = {}
        self._folders: list[str] = []
        self._collapsed: set[str] = set()
        self._baseline: str | None = None
        self._load()

    @property
    def names(self) -> list[str]:
        """All recipe names, in display order."""
        return list(self._recipes)

    def get(self, name: str) -> Recipe | None:
        """Return the named recipe, or None."""
        return self._recipes.get(name)

    def folder_of(self, name: str) -> str:
        """The folder a recipe belongs to ("" = ungrouped)."""
        return self._folder_of.get(name, UNGROUPED)

    def folders(self) -> list[str]:
        """The folder names, in display order (excludes ungrouped)."""
        return list(self._folders)

    def names_in(self, folder: str) -> list[str]:
        """Recipe names in a folder, in display order."""
        return [
            name
            for name in self._recipes
            if self._folder_of.get(name, UNGROUPED) == folder
        ]

    @property
    def baseline(self) -> str | None:
        """The name of the recipe marked as compare baseline, or None."""
        return self._baseline

    def baseline_recipe(self) -> Recipe | None:
        """The recipe marked as compare baseline, or None."""
        return self._recipes.get(self._baseline) if self._baseline else None

    def add(self, name: str, recipe: Recipe, folder: str = UNGROUPED) -> None:
        """Store recipe under name."""
        self._recipes[name] = recipe
        self._folder_of[name] = folder
        self._ensure_folder(folder)
        self._save()

    def thumb_jpeg(self, name: str) -> bytes | None:
        """The recipe's stored thumbnail as JPEG bytes, or None."""
        encoded = self._thumb_of.get(name)
        if encoded is None:
            return None
        try:
            return base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return None

    def set_thumb(self, name: str, jpeg: bytes | None) -> bool:
        """Store or clear a recipe's thumbnail JPEG."""
        if name not in self._recipes:
            return False
        if not jpeg:
            if self._thumb_of.pop(name, None) is None:
                return False
        else:
            self._thumb_of[name] = base64.b64encode(jpeg).decode("ascii")
        self._save()
        return True

    def comment(self, name: str) -> str:
        """The recipe's hover comment."""
        return self._comment_of.get(name, "")

    def set_comment(self, name: str, text: str) -> bool:
        """Store or clear a recipe's hover comment."""
        if name not in self._recipes:
            return False
        text = text.strip()
        if not text:
            if self._comment_of.pop(name, None) is None:
                return False
        elif self._comment_of.get(name) == text:
            return False
        else:
            self._comment_of[name] = text
        self._save()
        return True

    def hotkey_of(self, name: str) -> int | None:
        """The number key assigned to a recipe, or None."""
        return self._hotkey_of.get(name)

    def recipe_for_hotkey(self, key: int) -> str | None:
        """The recipe name assigned to a number key, or None."""
        for name, assigned in self._hotkey_of.items():
            if assigned == key:
                return name
        return None

    def set_hotkey(self, name: str, key: int | None) -> bool:
        """Assign a number key to a recipe, or clear it with None."""
        if name not in self._recipes:
            return False
        if key is None:
            if self._hotkey_of.pop(name, None) is None:
                return False
        elif key not in HOTKEYS or self._hotkey_of.get(name) == key:
            return False
        else:
            holder = self.recipe_for_hotkey(key)
            if holder is not None:
                del self._hotkey_of[holder]
            self._hotkey_of[name] = key
        self._save()
        return True

    def delete(self, name: str) -> bool:
        """Remove the named recipe; False if it did not exist."""
        if name not in self._recipes:
            return False
        del self._recipes[name]
        self._folder_of.pop(name, None)
        self._thumb_of.pop(name, None)
        self._comment_of.pop(name, None)
        self._hotkey_of.pop(name, None)
        if self._baseline == name:
            self._baseline = None
        self._save()
        return True

    def remove(self, names: list[str]) -> list[Removed]:
        """Delete the named recipes and return what restore needs."""
        order = list(self._recipes)
        removed = [
            Removed(
                name=name,
                recipe=self._recipes[name],
                index=order.index(name),
                folder=self._folder_of.get(name, UNGROUPED),
                thumb=self._thumb_of.get(name),
                comment=self._comment_of.get(name, ""),
                hotkey=self._hotkey_of.get(name),
                baseline=self._baseline == name,
            )
            for name in dict.fromkeys(names)
            if name in self._recipes
        ]
        for entry in removed:
            del self._recipes[entry.name]
            self._folder_of.pop(entry.name, None)
            self._thumb_of.pop(entry.name, None)
            self._comment_of.pop(entry.name, None)
            self._hotkey_of.pop(entry.name, None)
            if entry.baseline:
                self._baseline = None
        if removed:
            self._save()
        return removed

    def restore(self, removed: list[Removed]) -> list[str]:
        """Bring deleted recipes back to where they were."""
        order = list(self._recipes)
        recipes = dict(self._recipes)
        restored: list[str] = []
        for entry in sorted(removed, key=lambda entry: entry.index):
            if entry.name in recipes:
                continue
            order.insert(min(entry.index, len(order)), entry.name)
            recipes[entry.name] = entry.recipe
            self._folder_of[entry.name] = entry.folder
            self._ensure_folder(entry.folder)
            if entry.thumb is not None:
                self._thumb_of[entry.name] = entry.thumb
            if entry.comment:
                self._comment_of[entry.name] = entry.comment
            taken = self._hotkey_of.values()
            if entry.hotkey is not None and entry.hotkey not in taken:
                self._hotkey_of[entry.name] = entry.hotkey
            if entry.baseline and self._baseline is None:
                self._baseline = entry.name
            restored.append(entry.name)
        if restored:
            self._recipes = {name: recipes[name] for name in order}
            self._save()
        return restored

    def rename(self, old: str, new: str) -> bool:
        """Rename a recipe, keeping its place and folder, False if invalid.

        A recipe already stored under the new name is dropped: the rename
        wins the collision.
        """
        if old not in self._recipes or not new or new == old:
            return False
        folder = self._folder_of.get(old, UNGROUPED)
        renamed = self._recipes[old]
        rebuilt: dict[str, Recipe] = {}
        for name, value in self._recipes.items():
            if name == old:
                rebuilt[new] = renamed
            elif name != new:
                rebuilt[name] = value
        self._recipes = rebuilt
        self._folder_of.pop(old, None)
        self._folder_of.pop(new, None)
        self._folder_of[new] = folder
        thumb = self._thumb_of.pop(old, None)
        self._thumb_of.pop(new, None)
        if thumb is not None:
            self._thumb_of[new] = thumb
        comment = self._comment_of.pop(old, None)
        self._comment_of.pop(new, None)
        if comment is not None:
            self._comment_of[new] = comment
        hotkey = self._hotkey_of.pop(old, None)
        self._hotkey_of.pop(new, None)
        if hotkey is not None:
            self._hotkey_of[new] = hotkey
        if self._baseline in (old, new):
            self._baseline = new
        self._save()
        return True

    def move(self, name: str, folder: str) -> bool:
        """Move a recipe into folder ("" = ungrouped); False if unknown."""
        if name not in self._recipes:
            return False
        self._folder_of[name] = folder
        self._ensure_folder(folder)
        self._save()
        return True

    def place_recipe(
        self, name: str, folder: str, before: str | None = None
    ) -> bool:
        """Move a recipe into folder, positioned before another recipe."""
        if name not in self._recipes or name == before:
            return False
        self._folder_of[name] = folder
        self._ensure_folder(folder)
        order = [n for n in self._recipes if n != name]
        if before is not None and before in order:
            index = order.index(before)
        else:
            members = [
                i
                for i, n in enumerate(order)
                if self._folder_of.get(n, UNGROUPED) == folder
            ]
            index = members[-1] + 1 if members else len(order)
        order.insert(index, name)
        self._recipes = {n: self._recipes[n] for n in order}
        self._save()
        return True

    def set_baseline(self, name: str | None) -> bool:
        """Mark name as the compare baseline (or None to clear).

        False if name is given but is not a saved recipe.
        """
        if name is not None and name not in self._recipes:
            return False
        self._baseline = name
        self._save()
        return True

    def is_collapsed(self, folder: str) -> bool:
        """Whether the manager shows a folder folded away."""
        return folder in self._collapsed

    def set_collapsed(self, folder: str, collapsed: bool) -> None:
        """Remember a folder as folded away."""
        if collapsed == (folder in self._collapsed):
            return
        if collapsed:
            self._collapsed.add(folder)
        else:
            self._collapsed.discard(folder)
        self._save()

    def create_folder(self, name: str) -> bool:
        """Create an empty folder; False if blank or already present."""
        if not name or name in self._folders:
            return False
        self._folders.append(name)
        self._save()
        return True

    def rename_folder(self, old: str, new: str) -> bool:
        """Rename a folder and its members; False if invalid or a clash."""
        if old not in self._folders or not new or new in self._folders:
            return False
        self._folders[self._folders.index(old)] = new
        for name, folder in self._folder_of.items():
            if folder == old:
                self._folder_of[name] = new
        if old in self._collapsed:
            self._collapsed.discard(old)
            self._collapsed.add(new)
        self._save()
        return True

    def delete_folder(self, name: str) -> bool:
        """Delete a folder; its recipes move to ungrouped. False if absent."""
        if name not in self._folders:
            return False
        self._folders.remove(name)
        self._collapsed.discard(name)
        for recipe, folder in self._folder_of.items():
            if folder == name:
                self._folder_of[recipe] = UNGROUPED
        self._save()
        return True

    def reorder_folder(self, name: str, *, up: bool) -> bool:
        """Swap a folder with its neighbour, False if at the edge."""
        if name not in self._folders:
            return False
        index = self._folders.index(name)
        target = index - 1 if up else index + 1
        if target < 0 or target >= len(self._folders):
            return False
        self._folders[index], self._folders[target] = (
            self._folders[target],
            self._folders[index],
        )
        self._save()
        return True

    def _ensure_folder(self, folder: str) -> None:
        """Register a non-empty folder in the display order if new."""
        if folder and folder not in self._folders:
            self._folders.append(folder)

    def _load(self) -> None:
        """Read the library, migrating the flat format when needed."""
        try:
            text = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            safefile.set_aside(self._path)
            return
        if isinstance(data.get("recipes"), dict):
            self._load_v2(data)
        else:
            self._recipes = decode_recipes(data)
            self._folder_of = dict.fromkeys(self._recipes, UNGROUPED)
        self._normalise()

    def _load_v2(self, data: dict[str, Any]) -> None:
        """Load the versioned format (recipes carry a folder field)."""
        for name, value in data["recipes"].items():
            if not (isinstance(name, str) and isinstance(value, dict)):
                continue
            folder = value.get("folder")
            thumb = value.get("thumb")
            comment = value.get("comment")
            hotkey = value.get("hotkey")
            skip = ("folder", "thumb", "comment", "hotkey")
            fields = {k: v for k, v in value.items() if k not in skip}
            self._recipes[name] = Recipe.from_dict(fields)
            self._folder_of[name] = folder if isinstance(folder, str) else ""
            if isinstance(thumb, str) and thumb:
                self._thumb_of[name] = thumb
            if isinstance(comment, str) and comment:
                self._comment_of[name] = comment
            if (
                isinstance(hotkey, int)
                and hotkey in HOTKEYS
                and hotkey not in self._hotkey_of.values()
            ):
                self._hotkey_of[name] = hotkey
        self._folders = [
            f for f in data.get("folders", []) if isinstance(f, str)
        ]
        self._collapsed = {
            f for f in data.get("collapsed", []) if isinstance(f, str)
        }
        baseline = data.get("baseline")
        self._baseline = baseline if isinstance(baseline, str) else None

    def _normalise(self) -> None:
        """Make folders self-consistent after loading."""
        for folder in self._folder_of.values():
            self._ensure_folder(folder)
        self._collapsed &= {UNGROUPED, *self._folders}
        if self._baseline not in self._recipes:
            self._baseline = None

    def _save(self) -> None:
        """Persist the library to its path in the v2 format."""
        encoded: dict[str, Any] = {}
        for name, recipe in self._recipes.items():
            entry = recipe.to_dict()
            folder = self._folder_of.get(name, UNGROUPED)
            if folder:
                entry["folder"] = folder
            thumb = self._thumb_of.get(name)
            if thumb:
                entry["thumb"] = thumb
            comment = self._comment_of.get(name)
            if comment:
                entry["comment"] = comment
            hotkey = self._hotkey_of.get(name)
            if hotkey:
                entry["hotkey"] = hotkey
            encoded[name] = entry
        data = {
            "version": 2,
            "baseline": self._baseline,
            "folders": self._folders,
            "collapsed": sorted(self._collapsed),
            "recipes": encoded,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        safefile.write_text(self._path, json.dumps(data, indent=2))
