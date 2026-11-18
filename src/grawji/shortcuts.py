"""The keyboard shortcuts."""

from __future__ import annotations

from dataclasses import dataclass

from grawji.marks import MAX_RATING, Label

# Accelerators the application binds to a window action.
ACCELS: dict[str, tuple[str, ...]] = {
    "export": ("<Ctrl>e",),
    "save-recipe": ("<Ctrl>s",),
    "paste-recipe": ("<Ctrl><Shift>v",),
    "copy-recipe": ("<Ctrl><Shift>c",),
    "reset": ("<Ctrl>r",),
    "undo": ("<Ctrl>z",),
    "redo": ("<Ctrl><Shift>z", "<Ctrl>y"),
    "preferences": ("<Ctrl>comma",),
    "select-all": ("<Ctrl>a",),
    "zoom-in": ("<Ctrl>plus", "<Ctrl>equal"),
    "zoom-out": ("<Ctrl>minus",),
    "zoom-fit": ("<Ctrl>0",),
    "shortcuts": ("<Ctrl>question",),
    **{
        f"rate({stars})": (f"<Alt>{stars}",) for stars in range(MAX_RATING + 1)
    },
    **{
        f"toggle-label('{label.value}')": (f"F{index}",)
        for index, label in enumerate(Label, start=1)
    },
}

# Bare keys, bound by the shortcut controller of the window template.
BARE_KEYS: dict[str, str] = {
    "cycle-background": "b",
    "toggle-peek": "backslash",
    "toggle-crop": "c",
    "toggle-export": "e",
    "toggle-reject": "x",
    "toggle-grid": "g",
    "toggle-histogram": "h",
    "toggle-marks-bar": "m",
    "toggle-source": "j",
    "cancel-selection": "Escape",
}


@dataclass(frozen=True)
class Entry:
    """One line of the shortcuts dialog.

    Attributes:
        label: What the shortcut does.
        action: The window action it triggers.
        keys: The key of a shortcut.
    """

    label: str
    action: str = ""
    keys: str = ""

    @property
    def accelerator(self) -> str:
        """The key combination to show, or an empty string."""
        if not self.action:
            return self.keys
        if self.action in BARE_KEYS:
            return BARE_KEYS[self.action]
        return ACCELS[self.action][0]


GROUPS: dict[str, tuple[Entry, ...]] = {
    "Files": (
        Entry("Export JPEG", "export"),
        Entry("Move to trash", keys="Delete"),
        Entry("Select all images", "select-all"),
        Entry("Add or remove one image (Ctrl + click)"),
        Entry("Select a range (Shift + click)"),
        Entry("Cancel selection", "cancel-selection"),
    ),
    "Marks": (
        Entry("Rate one to five stars, again to clear", "rate(1)"),
        Entry("Clear the rating", "rate(0)"),
        Entry("Reject, or take a reject back", "toggle-reject"),
        Entry("Mark for export", "toggle-export"),
        *(
            Entry(
                f"{label.value.capitalize()} label",
                f"toggle-label('{label.value}')",
            )
            for label in Label
        ),
    ),
    "Recipe": (
        Entry("Save recipe", "save-recipe"),
        Entry("Copy recipe as text", "copy-recipe"),
        Entry("Paste recipe from clipboard", "paste-recipe"),
        Entry("Undo the last edit", "undo"),
        Entry("Redo the undone edit", "redo"),
        Entry("Reset to default", "reset"),
        Entry("Apply the recipe assigned to a number key (1 to 9)", keys="1"),
        Entry(
            "Develop a RAW+JPEG shot from the RAW or the camera JPEG",
            "toggle-source",
        ),
    ),
    "Navigation": (
        Entry("Previous image (hold to glide)", keys="Left"),
        Entry("Next image (hold to glide)", keys="Right"),
    ),
    "View": (
        Entry("Zoom in", "zoom-in"),
        Entry("Zoom out", "zoom-out"),
        Entry("Zoom at the pointer (Ctrl + scroll)"),
        Entry("Fit to window", "zoom-fit"),
        Entry("Cycle background", "cycle-background"),
        Entry("Show original (before/after)", "toggle-peek"),
        Entry("Toggle histogram", "toggle-histogram"),
        Entry("Toggle marks bar", "toggle-marks-bar"),
        Entry("Switch between developing and browsing", "toggle-grid"),
        Entry("Crop and straighten", "toggle-crop"),
        Entry("Apply crop", keys="Return"),
        Entry("Cancel crop", keys="Escape"),
    ),
    "Application": (
        Entry("Preferences", "preferences"),
        Entry("Keyboard shortcuts", "shortcuts"),
    ),
}
