"""Trying recipes on the open image, one render at a time."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import astuple
from functools import partial

from grawji.recipe import Recipe

Render = Callable[[Recipe, Callable[[bytes | None], None]], None]
OnPicture = Callable[[str, bytes], None]
OnSettled = Callable[[], None]


class Tryout:
    """Renders recipes on one image and keeps the pictures."""

    def __init__(self, render: Render) -> None:
        """Bind the tryout to what renders a recipe on the open image.

        Args:
            render: Renders a recipe into a small JPEG and calls back with it.
        """
        self._render = render
        self._image: str | None = None
        self._pictures: dict[tuple[object, ...], bytes] = {}
        self._queue: list[tuple[str, Recipe]] = []
        self._on_picture: OnPicture | None = None
        self._on_settled: OnSettled | None = None
        self._flight = 0
        self._flying = False

    def show(
        self,
        image: str,
        entries: list[tuple[str, Recipe]],
        on_picture: OnPicture,
        on_settled: OnSettled,
    ) -> None:
        """Hand out the pictures of these recipes, rendering the missing.

        Args:
            image: The image the recipes are tried on.
            entries: The recipes by name.
            on_picture: Takes a recipe's name and its picture.
            on_settled: Called once no further picture is coming.
        """
        if image != self._image:
            self._image = image
            self._pictures = {}
            self._land()
        self._on_picture = on_picture
        self._on_settled = on_settled
        self._queue = []
        for name, recipe in entries:
            picture = self._pictures.get(astuple(recipe))
            if picture is None:
                self._queue.append((name, recipe))
            else:
                on_picture(name, picture)
        self._next()

    def stop(self) -> None:
        """Render nothing further and hand out nothing more."""
        self._queue = []
        self._on_picture = None
        self._on_settled = None
        self._land()

    def _land(self) -> None:
        """Stop waiting for the render that is under way."""
        self._flight += 1
        self._flying = False

    def _next(self) -> None:
        """Render the next missing picture."""
        if self._flying or self._image is None:
            return
        if not self._queue:
            if self._on_settled is not None:
                self._on_settled()
            return
        name, recipe = self._queue.pop(0)
        self._flying = True
        self._flight += 1
        self._render(
            recipe,
            partial(
                self._on_rendered, self._flight, self._image, name, recipe
            ),
        )

    def _on_rendered(
        self,
        flight: int,
        image: str,
        name: str,
        recipe: Recipe,
        picture: bytes | None,
    ) -> None:
        """Keep a finished picture and go on with the next."""
        key = astuple(recipe)
        if picture is not None and image == self._image:
            self._pictures[key] = picture
        if flight != self._flight:
            return
        self._flying = False
        if picture is None:
            self._queue = []
        else:
            served = [n for n, queued in self._queue if astuple(queued) == key]
            self._queue = [
                entry for entry in self._queue if astuple(entry[1]) != key
            ]
            if self._on_picture is not None:
                for each in dict.fromkeys([name, *served]):
                    self._on_picture(each, picture)
        self._next()
