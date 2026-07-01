"""Pure geometry for the preview navigator (no GTK dependency).

Kept separate from navigator.py so the rotation maths can be unit-tested
without importing GTK.
"""

from __future__ import annotations

# At/above this visible fraction the whole image shows, so no rectangle.
FULL = 0.999

# Map a preview-space (fx, fy, fw, fh) fraction rect into the unrotated
# thumbnail space.
RECT_ROTATIONS = {
    90: lambda fx, fy, fw, fh: (fy, 1 - fx - fw, fh, fw),
    180: lambda fx, fy, fw, fh: (1 - fx - fw, 1 - fy - fh, fw, fh),
    270: lambda fx, fy, fw, fh: (1 - fy - fh, fx, fh, fw),
}

# Inverse of the above for a single point.
POINT_ROTATIONS = {
    90: lambda ix, iy: (1 - iy, ix),
    180: lambda ix, iy: (1 - ix, 1 - iy),
    270: lambda ix, iy: (iy, 1 - ix),
}
