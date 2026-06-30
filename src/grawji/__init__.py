"""grawji - GTK4 frontend for the rawji Fuji RAF converter.

The name is **g**(tk) + **rawji**. grawji wraps rawji's public API in a
thin adapter and presents an interactive UI for tuning a recipe with a
live preview, then exporting at full resolution.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("grawji")
except PackageNotFoundError:  # not installed (e.g. running from a raw tree)
    __version__ = "0.0.0"
