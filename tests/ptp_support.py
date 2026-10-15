"""A fake camera answering the PTP object operations, for camera tests."""

import struct

UPLOAD_FORMAT = 0xF802
JPEG_FORMAT = 0x3801
OK = 0x2001
ACCESS_DENIED = 0x200F
NOT_SUPPORTED = 0x2005
GET_OBJECT_HANDLES = 0x1007
GET_OBJECT_INFO = 0x1008
GET_OBJECT = 0x1009
GET_THUMB = 0x100A
DELETE_OBJECT = 0x100B


class PtpCamera:
    """A camera answering the PTP object operations from a dict.

    objects maps handle -> [format, data]. The other dicts script
    refusals per handle; ops records every (opcode, *params) sent.
    """

    def __init__(self, objects=None):
        """Start with the given objects listed."""
        self.objects = dict(objects or {})
        self.ops = []
        self.hidden = {}  # handle -> polls before it is listed
        self.info_refusals = {}  # handle -> GetObjectInfo 0x200F count
        self.get_refusals = {}  # handle -> GetObject 0x200F count
        self.get_code = {}  # handle -> GetObject code, always
        self.thumbs = {}  # handle -> GetThumb data (missing: refused)
        self.delete_code = OK
        self.handles_code = OK
        self.lost_reply_on = None  # opcode whose reply never comes

    def send_command(self, code, params=None):
        """Answer one PTP operation."""
        params = list(params or [])
        self.ops.append((code, *params))
        if code == self.lost_reply_on:
            raise OSError("USB read failed: [Errno 110] Operation timed out")
        handlers = {
            GET_OBJECT_HANDLES: lambda _h: self._handles(),
            GET_OBJECT_INFO: self._info,
            GET_THUMB: self._thumb,
            GET_OBJECT: self._get,
            DELETE_OBJECT: self._delete,
        }
        handler = handlers.get(code)
        if handler is None:
            return NOT_SUPPORTED, [], b""
        return handler(params[0] if params else None)

    def _info(self, handle):
        """GetObjectInfo, with scripted refusals."""
        if self.info_refusals.get(handle, 0):
            self.info_refusals[handle] -= 1
            return ACCESS_DENIED, [], b""
        fmt = self.objects[handle][0]
        return OK, [], struct.pack("<IHHI", 0x10001, fmt, 0, 99)

    def _thumb(self, handle):
        """GetThumb: scripted data, else refused."""
        if handle in self.thumbs:
            return OK, [], self.thumbs[handle]
        return 0x2010, [], b""

    def _delete(self, handle):
        """DeleteObject, unless scripted to refuse."""
        if self.delete_code == OK:
            del self.objects[handle]
        return self.delete_code, [], b""

    def _handles(self):
        """GetObjectHandles: listed objects, hiding ones still converting."""
        if self.handles_code != OK:
            return self.handles_code, [], b""
        listed = []
        for handle in self.objects:
            if self.hidden.get(handle, 0):
                self.hidden[handle] -= 1
            else:
                listed.append(handle)
        data = struct.pack(f"<I{len(listed)}I", len(listed), *listed)
        return OK, [], data

    def _get(self, handle):
        """GetObject, with scripted refusals."""
        if handle in self.get_code:
            return self.get_code[handle], [], b""
        if self.get_refusals.get(handle, 0):
            self.get_refusals[handle] -= 1
            return ACCESS_DENIED, [], b""
        return OK, [], self.objects[handle][1]

    def count(self, code):
        """How many times an opcode was sent."""
        return sum(1 for op in self.ops if op[0] == code)
