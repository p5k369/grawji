"""The GExiv2 binding, at the newest namespace version installed."""

from __future__ import annotations

import gi

__all__ = ["GExiv2", "newest_version", "require_version"]


def newest_version() -> str | None:
    """The highest GExiv2 typelib version installed."""
    repository = gi.Repository.get_default()
    versions: list[str] = repository.enumerate_versions("GExiv2")
    if not versions:
        return None
    return max(versions, key=lambda v: tuple(int(n) for n in v.split(".")))


def require_version() -> str:
    """Claim the newest installed GExiv2 namespace."""
    version = newest_version()
    if version is None:
        raise ValueError(
            "Namespace GExiv2 not available "
            "(is gexiv2 installed with introspection?)"
        )
    gi.require_version("GExiv2", version)
    return version


require_version()

from gi.repository import GExiv2  # noqa: E402
