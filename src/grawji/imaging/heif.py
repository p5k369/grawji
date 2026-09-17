"""Reading and editing a HEIF container, byte by byte."""

from __future__ import annotations

# Four bytes of box length plus the four-character box type.
_BOX_HEADER = 8
# A box with length 1 carries a 64-bit length after the type.
_LARGE_BOX_HEADER = 16
# Item IDs widen from 16 to 32 bits at these box versions.
_INFE_LARGE_IDS = 3
_ILOC_LARGE_IDS = 2
# What an item table field of each width can address.
_OFFSET_LIMITS = {4: 2**32, 8: 2**64}


def is_heif(data: bytes) -> bool:
    """Whether the bytes look like a HEIF file."""
    return len(data) > _BOX_HEADER and data[4:8] == b"ftyp"


def is_complete(data: bytes) -> bool:
    """Whether the file's box chain ends exactly at the last byte."""
    offset = 0
    while offset + _BOX_HEADER <= len(data):
        size = int.from_bytes(data[offset : offset + 4], "big")
        if size == 0:
            return True
        if size == 1:
            if offset + _LARGE_BOX_HEADER > len(data):
                return False
            size = int.from_bytes(
                data[offset + 8 : offset + _LARGE_BOX_HEADER], "big"
            )
        if size < _BOX_HEADER or offset + size > len(data):
            return False
        offset += size
    return offset == len(data)


def exif_block(data: bytes) -> bytes | None:
    """The file's Exif block."""
    extent = _exif_extent(data)
    if extent is None:
        return None
    return data[extent[0] : extent[0] + extent[1]]


def replace_exif(data: bytes, block: bytes) -> bytes | None:
    """Put a new Exif block into a HEIF file."""
    extent = _exif_extent(data)
    if extent is None:
        return None
    offset, length, offset_field, length_field, offset_size, length_size = (
        extent
    )
    out = bytearray(data)
    if len(block) <= length:
        out[offset : offset + len(block)] = block
        _write_field(out, length_field, length_size, len(block))
        return bytes(out)
    new_offset = len(out) + _BOX_HEADER
    if new_offset + len(block) > _OFFSET_LIMITS.get(offset_size, 0):
        return None
    header = (len(block) + _BOX_HEADER).to_bytes(4, "big")
    out += header + b"mdat" + block
    _write_field(out, offset_field, offset_size, new_offset)
    _write_field(out, length_field, length_size, len(block))
    return bytes(out)


def _exif_extent(
    data: bytes,
) -> tuple[int, int, int, int, int, int] | None:
    """Where the Exif bytes are, and where the iloc records that."""
    item = _exif_item_id(data)
    if item is None:
        return None
    found = _find_box(data, b"iloc")
    if found is None:
        return None
    start, header, _size = found
    version = data[start + header]
    sizes = data[start + header + 4]
    offset_size, length_size = sizes >> 4, sizes & 0xF
    bases = data[start + header + 5]
    base_size, index_size = bases >> 4, bases & 0xF
    if offset_size not in _OFFSET_LIMITS or length_size not in _OFFSET_LIMITS:
        return None
    cursor = start + header + 6
    id_width = 2 if version < _ILOC_LARGE_IDS else 4
    count = int.from_bytes(data[cursor : cursor + id_width], "big")
    cursor += id_width
    for _ in range(count):
        item_id = int.from_bytes(data[cursor : cursor + id_width], "big")
        cursor += id_width
        if version in (1, 2):
            cursor += 2
        cursor += 2
        base = int.from_bytes(data[cursor : cursor + base_size], "big")
        cursor += base_size
        extents = int.from_bytes(data[cursor : cursor + 2], "big")
        cursor += 2
        for _extent in range(extents):
            cursor += index_size
            offset_field, length_field = cursor, cursor + offset_size
            cursor += offset_size + length_size
            if item_id == item and extents == 1:
                offset = base + int.from_bytes(
                    data[offset_field : offset_field + offset_size], "big"
                )
                length = int.from_bytes(
                    data[length_field : length_field + length_size], "big"
                )
                return (
                    offset,
                    length,
                    offset_field,
                    length_field,
                    offset_size,
                    length_size,
                )
    return None


def _exif_item_id(data: bytes) -> int | None:
    """The item ID of the file's Exif metadata, from the iinf box."""
    found = _find_box(data, b"iinf")
    if found is None:
        return None
    start, header, size = found
    version = data[start + header]
    cursor = start + header + 4
    width = 2 if version == 0 else 4
    count = int.from_bytes(data[cursor : cursor + width], "big")
    cursor += width
    for _ in range(count):
        if cursor + 20 > start + size:
            return None
        entry = int.from_bytes(data[cursor : cursor + 4], "big")
        infe_version = data[cursor + 8]
        id_width = 2 if infe_version < _INFE_LARGE_IDS else 4
        item_id = int.from_bytes(
            data[cursor + 12 : cursor + 12 + id_width], "big"
        )
        type_at = cursor + 12 + id_width + 2
        if data[type_at : type_at + 4] == b"Exif":
            return item_id
        cursor += entry
    return None


def _find_box(
    data: bytes, want: bytes, start: int = 0, end: int | None = None
) -> tuple[int, int, int] | None:
    """Locate a box by type, descending into the meta box."""
    offset = start
    end = len(data) if end is None else end
    while offset + _BOX_HEADER <= end:
        size = int.from_bytes(data[offset : offset + 4], "big")
        box = data[offset + 4 : offset + _BOX_HEADER]
        header = _BOX_HEADER
        if size == 1:
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        if size == 0 or size < _BOX_HEADER or offset + size > end:
            return None
        if box == want:
            return offset, header, size
        if box == b"meta":
            inner = _find_box(data, want, offset + header + 4, offset + size)
            if inner is not None:
                return inner
        offset += size
    return None


def _write_field(out: bytearray, at: int, width: int, value: int) -> None:
    """Overwrite a big-endian field of the given width."""
    out[at : at + width] = value.to_bytes(width, "big")
