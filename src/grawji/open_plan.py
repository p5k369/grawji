"""What the recipe controls show when an image opens."""

from __future__ import annotations

from dataclasses import dataclass

from grawji.recipe import Recipe
from grawji.settings import FROM_IMAGE, FROM_IMAGE_LABEL


@dataclass(frozen=True)
class OpenPlan:
    """The state the controls take for a freshly opened image.

    Attributes:
        recipe: The recipe to load, None to leave the controls alone.
        label: The name the recipe is applied under.
        exposure: The image's own exposure compensation.
        render: Whether the camera has to render.
    """

    recipe: Recipe | None
    label: str | None
    exposure: float
    render: bool


def plan_open(
    *,
    shot: Recipe | None,
    stored_ev: float | None,
    carry: Recipe | None,
    selection: str,
    saved: Recipe | None,
    has_embedded: bool,
) -> OpenPlan:
    """Decide which recipe an opened image starts with.

    Args:
        shot: The recipe the image was shot with.
        stored_ev: The exposure stored beside the image.
        carry: The unsaved edits to keep.
        selection: The sticky choice, a recipe name or the image marker.
        saved: The saved recipe behind the choice.
        has_embedded: Whether the image's embedded JPEG is on screen.
    """
    shot_ev = shot.exposure if shot is not None else Recipe().exposure
    exposure = stored_ev if stored_ev is not None else shot_ev
    if carry is not None:
        return OpenPlan(carry, None, exposure, render=True)
    from_image = selection == FROM_IMAGE or saved is None
    if shot is not None and from_image:
        shows_it = has_embedded and exposure == shot.exposure
        return OpenPlan(shot, FROM_IMAGE_LABEL, exposure, render=not shows_it)
    if saved is not None:
        return OpenPlan(saved, selection, exposure, render=True)
    return OpenPlan(None, None, exposure, render=True)
