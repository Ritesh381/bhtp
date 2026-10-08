"""Framing layer: the 8-byte frame header, and reading frames off a socket.

The whole point of putting `length` first is visible here: read_frame() can
hand back a frame of a type it has never heard of, because it never needs to
understand `type` to know where the frame ends.
"""

import struct

MAGIC = b"BHTP/1\r\n"
HEADER_LEN = 8

# Frame types.
TYPE_HEADERS = 0x01
TYPE_DATA = 0x02
TYPE_SETTINGS = 0x03
TYPE_GOAWAY = 0x04

TYPE_NAMES = {
    TYPE_HEADERS: "HEADERS",
    TYPE_DATA: "DATA",
    TYPE_SETTINGS: "SETTINGS",
    TYPE_GOAWAY: "GOAWAY",
}

# Flags.
FLAG_END_STREAM = 0x01
FLAG_END_HEADERS = 0x02
FLAG_ACK = 0x04

FLAG_NAMES = [
    (FLAG_END_STREAM, "END_STREAM"),
    (FLAG_END_HEADERS, "END_HEADERS"),
    (FLAG_ACK, "ACK"),
]

# Settings identifiers.
SETTING_MAX_FRAME_SIZE = 0x0001
SETTING_MAX_TABLE_ENTRIES = 0x0002
SETTING_MAX_CONCURRENT_STREAMS = 0x0003

DEFAULT_MAX_FRAME_SIZE = 16384
HARD_MAX_FRAME_SIZE = 0xFFFFFF  # what 24 bits of length can express

# Error codes, deliberately equal to their HTTP counterparts.
ERR_MALFORMED = 400
ERR_NOT_FOUND = 404
ERR_METHOD_NOT_ALLOWED = 405
ERR_FRAME_TOO_LARGE = 413
ERR_VERSION_MISMATCH = 505


class ProtocolError(Exception):
    """A peer broke the protocol. `code` goes out in a GOAWAY frame."""

    def __init__(self, code, reason=""):
        super().__init__("%d %s" % (code, reason))
        self.code = code
        self.reason = reason


class Frame:
    __slots__ = ("type", "flags", "stream", "payload")

    def __init__(self, type_, flags, stream, payload):
        self.type = type_
        self.flags = flags
        self.stream = stream
        self.payload = payload

    def has(self, flag):
        return bool(self.flags & flag)

    @property
    def type_name(self):
        return TYPE_NAMES.get(self.type, "UNKNOWN(0x%02x)" % self.type)

    @property
    def flag_names(self):
        names = [n for bit, n in FLAG_NAMES if self.flags & bit]
        unknown = self.flags & ~(FLAG_END_STREAM | FLAG_END_HEADERS | FLAG_ACK)
        if unknown:
            names.append("0x%02x?" % unknown)
        return names

    def header_bytes(self):
        return pack_header(self.type, self.flags, self.stream, len(self.payload))

    def __repr__(self):
        return "<Frame %s flags=%s stream=%d len=%d>" % (
            self.type_name,
            "|".join(self.flag_names) or "-",
            self.stream,
            len(self.payload),
        )


def _u24(value):
    return struct.pack(">I", value)[1:]


def pack_header(type_, flags, stream, length):
    if not 0 <= length <= HARD_MAX_FRAME_SIZE:
        raise ValueError("length does not fit in 24 bits: %d" % length)
    if not 0 <= stream <= 0xFFFFFF:
        raise ValueError("stream id does not fit in 24 bits: %d" % stream)
    return _u24(length) + bytes((type_ & 0xFF, flags & 0xFF)) + _u24(stream)


def pack_frame(type_, flags, stream, payload=b""):
    return pack_header(type_, flags, stream, len(payload)) + payload


def settings_payload(values):
    """values: dict of setting id -> u32."""
    return b"".join(struct.pack(">HI", k, v) for k, v in sorted(values.items()))


def pack_settings(values, ack=False):
    flags = FLAG_ACK if ack else 0
    return pack_frame(TYPE_SETTINGS, flags, 0, settings_payload(values))


def parse_settings(payload):
    if len(payload) % 6:
        raise ProtocolError(ERR_MALFORMED, "SETTINGS payload not a multiple of 6")
    out = {}
    for off in range(0, len(payload), 6):
        key, value = struct.unpack_from(">HI", payload, off)
        out[key] = value
    return out


def pack_goaway(code, reason=""):
    payload = struct.pack(">H", code) + reason.encode("utf-8")
    return pack_frame(TYPE_GOAWAY, 0, 0, payload)


def parse_goaway(payload):
    if len(payload) < 2:
        raise ProtocolError(ERR_MALFORMED, "GOAWAY shorter than 2 bytes")
    code = struct.unpack_from(">H", payload, 0)[0]
    return code, payload[2:].decode("utf-8", "replace")


def recv_exactly(sock, count):
    """Read exactly `count` bytes. None on a clean EOF before the first byte."""
    chunks = []
    got = 0
    while got < count:
        chunk = sock.recv(count - got)
        if not chunk:
            if got == 0:
                return None
            raise ProtocolError(
                ERR_MALFORMED, "peer vanished mid-read (%d of %d bytes)" % (got, count)
            )
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def send_all(sock, data):
    """sendall(), but tolerant of the partial writes lecture 2 warned about."""
    view = memoryview(data)
    sent = 0
    while sent < len(view):
        n = sock.send(view[sent:])
        if n == 0:
            raise ProtocolError(ERR_MALFORMED, "socket refused to accept bytes")
        sent += n
    return sent


class FrameReader:
    def __init__(self, sock, max_frame_size=DEFAULT_MAX_FRAME_SIZE):
        self.sock = sock
        self.max_frame_size = max_frame_size
        self._first_frame = True

    def read_frame(self):
        """Next frame, or None at a clean end of connection."""
        header = recv_exactly(self.sock, HEADER_LEN)
        if header is None:
            return None
        if self._first_frame:
            self._first_frame = False
            # An HTTP/1.x status line parses as a frame header claiming
            # megabytes. Say what actually happened instead.
            if header.startswith(b"HTTP/1."):
                raise ProtocolError(
                    ERR_VERSION_MISMATCH, "peer answered in HTTP/1.x, not BHTP/1"
                )
        length = int.from_bytes(header[0:3], "big")
        type_ = header[3]
        flags = header[4]
        stream = int.from_bytes(header[5:8], "big")
        if length > self.max_frame_size:
            raise ProtocolError(
                ERR_FRAME_TOO_LARGE,
                "frame of %d bytes exceeds the agreed %d" % (length, self.max_frame_size),
            )
        payload = b"" if length == 0 else recv_exactly(self.sock, length)
        if payload is None:
            raise ProtocolError(ERR_MALFORMED, "header promised %d bytes, got EOF" % length)
        return Frame(type_, flags, stream, payload)
