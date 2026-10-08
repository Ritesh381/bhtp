"""Header compression: a 10-entry static name table plus a dynamic
name+value table. HPACK's first two mechanisms, nothing more.

Unlike HPACK, the dynamic table here is append-only with absolute indexes.
Once it is full, new fields are simply sent as literals. That costs a little
compression at the tail and buys the thing that matters for a two-page spec:
an index never means two different fields, so the two sides cannot silently
drift apart.
"""

import struct

from .frame import ERR_MALFORMED, ProtocolError

# Static table. Index 0 is unused so that a prefix byte of 0x00 can mean
# "literal name follows".
# Every one of the ten is a name this implementation actually puts on the wire.
# `date` is deliberately absent: BHTP/1 has no caching intermediary that needs
# it, and its one real HTTP/1.1 job — Last-Modified arithmetic — is done better
# by etag, which does not care whose clock is wrong.
STATIC_TABLE = [
    None,
    b":method",
    b":path",
    b":status",
    b"host",
    b"content-length",
    b"content-type",
    b"user-agent",
    b"accept-encoding",
    b"etag",
    b"if-none-match",
]
STATIC_INDEX = {name: i for i, name in enumerate(STATIC_TABLE) if name is not None}

DYNAMIC_BASE = 11  # first dynamic index
MAX_INDEX = 127  # 0x80 | idx must still fit in one byte
MAX_TABLE_ENTRIES = MAX_INDEX - DYNAMIC_BASE + 1  # 117

FLAG_NO_INDEX = 0x40
FLAG_INDEXED = 0x80

# Fields whose value changes on nearly every request. Indexing them would
# burn table slots for a hit that never comes. Non-normative: a peer may
# index whatever it likes, the decoder follows the bits, not this list.
NEVER_INDEX = {b":path", b":status", b"content-length", b"etag", b"if-none-match"}

MAX_NAME_LEN = 0xFF
MAX_VALUE_LEN = 0xFFFF


class DynamicTable:
    def __init__(self, capacity=MAX_TABLE_ENTRIES):
        self.capacity = min(capacity, MAX_TABLE_ENTRIES)
        self.entries = []

    @property
    def full(self):
        return len(self.entries) >= self.capacity

    def add(self, name, value):
        if not self.full:
            self.entries.append((name, value))

    def lookup(self, index):
        if index < DYNAMIC_BASE:
            raise ProtocolError(
                ERR_MALFORMED,
                "index %d is a static name and carries no value" % index,
            )
        pos = index - DYNAMIC_BASE
        if pos >= len(self.entries):
            raise ProtocolError(ERR_MALFORMED, "dynamic index %d not yet defined" % index)
        return self.entries[pos]


class Encoder:
    def __init__(self, capacity=MAX_TABLE_ENTRIES):
        self.table = DynamicTable(capacity)
        self._index = {}

    def encode(self, fields):
        """fields: iterable of (name: bytes, value: bytes) -> header block bytes."""
        out = bytearray()
        for name, value in fields:
            if len(name) > MAX_NAME_LEN:
                raise ValueError("header name longer than 255 bytes")
            if len(value) > MAX_VALUE_LEN:
                raise ValueError("header value longer than 65535 bytes")

            known = self._index.get((name, value))
            if known is not None:
                out.append(FLAG_INDEXED | known)
                continue

            indexing = name not in NEVER_INDEX and not self.table.full
            static = STATIC_INDEX.get(name)

            if static is not None:
                out.append(static if indexing else (FLAG_NO_INDEX | static))
            else:
                out.append(0x00 if indexing else FLAG_NO_INDEX)
                out.append(len(name))
                out += name
            out += struct.pack(">H", len(value))
            out += value

            if indexing:
                next_index = DYNAMIC_BASE + len(self.table.entries)
                self.table.add(name, value)
                self._index[(name, value)] = next_index
        return bytes(out)


class Decoder:
    def __init__(self, capacity=MAX_TABLE_ENTRIES):
        self.table = DynamicTable(capacity)

    def decode(self, payload):
        fields = []
        i = 0
        n = len(payload)
        while i < n:
            prefix = payload[i]
            i += 1

            if prefix & FLAG_INDEXED:
                fields.append(self.table.lookup(prefix & 0x7F))
                continue

            indexing = not (prefix & FLAG_NO_INDEX)
            name_index = prefix & 0x3F

            if name_index == 0:
                i, raw = self._take(payload, i, 1)
                i, name = self._take(payload, i, raw[0])
            elif name_index < len(STATIC_TABLE):
                name = STATIC_TABLE[name_index]
            else:
                raise ProtocolError(
                    ERR_MALFORMED, "reserved header prefix 0x%02x" % prefix
                )

            i, raw = self._take(payload, i, 2)
            i, value = self._take(payload, i, struct.unpack(">H", raw)[0])

            fields.append((name, value))
            if indexing:
                self.table.add(name, value)
        return fields

    @staticmethod
    def _take(payload, i, count):
        end = i + count
        if end > len(payload):
            raise ProtocolError(ERR_MALFORMED, "header block truncated")
        return end, payload[i:end]


def find(fields, name, default=None):
    for key, value in fields:
        if key == name:
            return value
    return default
