"""Hexdump with per-field annotation.

"If you cannot annotate your own bytes, the spec is not finished."
"""

from . import frame as F

PRINTABLE = bytes(range(0x20, 0x7F))


def hexdump(data, base=0, indent=""):
    lines = []
    for off in range(0, len(data), 16):
        chunk = data[off : off + 16]
        hi = " ".join("%02x" % b for b in chunk[:8])
        lo = " ".join("%02x" % b for b in chunk[8:])
        text = "".join(chr(b) if b in PRINTABLE else "." for b in chunk)
        lines.append(
            "%s%04x  %-23s  %-23s  |%s|" % (indent, base + off, hi, lo, text)
        )
    return lines


def describe_header(fr):
    return (
        "len=%d type=0x%02x(%s) flags=0x%02x(%s) stream=%d"
        % (
            len(fr.payload),
            fr.type,
            fr.type_name,
            fr.flags,
            "|".join(fr.flag_names) or "none",
            fr.stream,
        )
    )


def _show(value):
    """A header value is opaque bytes. Print the useful rendering."""
    if value.isascii() and all(b in PRINTABLE for b in value):
        return value.decode()
    if len(value) == 2:
        return "0x%s (u16 %d)" % (value.hex(), int.from_bytes(value, "big"))
    if len(value) == 4:
        return "0x%s (u32 %d)" % (value.hex(), int.from_bytes(value, "big"))
    return "0x" + value.hex()


def annotate_payload(fr, fields=None):
    """Human-readable decode of a frame payload, best effort.

    HEADERS frames cannot be decoded here: decoding mutates the dynamic
    table, and doing it twice would desynchronise the two ends. The caller
    decodes once and passes the result in as `fields`.
    """
    notes = []
    if fr.type == F.TYPE_HEADERS:
        if fields is None:
            notes.append("%d bytes of encoded header block" % len(fr.payload))
        else:
            for name, value in fields:
                notes.append("%s: %s" % (name.decode("utf-8", "replace"), _show(value)))
    elif fr.type == F.TYPE_SETTINGS:
        if fr.has(F.FLAG_ACK):
            notes.append("acknowledgement, no payload")
        else:
            names = {
                F.SETTING_MAX_FRAME_SIZE: "max_frame_size",
                F.SETTING_MAX_TABLE_ENTRIES: "max_table_entries",
                F.SETTING_MAX_CONCURRENT_STREAMS: "max_concurrent_streams",
            }
            for key, value in F.parse_settings(fr.payload).items():
                notes.append("%s = %d" % (names.get(key, "setting 0x%04x" % key), value))
    elif fr.type == F.TYPE_GOAWAY:
        code, reason = F.parse_goaway(fr.payload)
        notes.append("code=%d reason=%r" % (code, reason))
    elif fr.type == F.TYPE_DATA:
        notes.append("%d body bytes, opaque" % len(fr.payload))
    else:
        notes.append("unrecognised frame type — skipped, %d bytes discarded" % len(fr.payload))
    return notes


def dump_frame(direction, fr, fields=None, body_limit=64):
    """direction: '>>>' for sent, '<<<' for received."""
    lines = ["%s %s %s" % (direction, fr.type_name, describe_header(fr))]
    for note in annotate_payload(fr, fields):
        lines.append("    . " + note)
    lines += hexdump(fr.header_bytes(), 0, "    ")
    payload = fr.payload
    truncated = len(payload) > body_limit
    lines += hexdump(payload[:body_limit], F.HEADER_LEN, "    ")
    if truncated:
        lines.append("    ... %d more payload bytes" % (len(payload) - body_limit))
    return "\n".join(lines)
