#!/usr/bin/env python3
"""Build (or verify) the conformance byte vectors quoted in SPEC.md.

    python3 tests/make-vectors.py            # write tests/vectors/ and print hex
    python3 tests/make-vectors.py --check     # fail if the code no longer matches

The vectors, not the code, are the contract. If an implementation reproduces
these exact bytes it will interoperate; if it cannot, the disagreement shows up
here in seconds instead of on submission day.
"""

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bhtp import frame as F
from bhtp import hpack
from bhtp.hexdump import hexdump

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "vectors")

HOST = b"localhost"
PATH = b"/hello.txt"
USER_AGENT = b"bcurl/1.0 (BHTP/1)"
ACCEPT_ENCODING = b"identity"
BODY = b"hello from BHTP/1\n"
CTYPE = b"text/plain; charset=utf-8"

REQUEST_FIELDS = [
    (b":method", b"GET"),
    (b":path", PATH),
    (b"host", HOST),
    (b"user-agent", USER_AGENT),
    (b"accept-encoding", ACCEPT_ENCODING),
]
RESPONSE_FIELDS = [
    (b":status", struct.pack(">H", 200)),
    (b"content-type", CTYPE),
    (b"content-length", struct.pack(">I", len(BODY))),
]


def build():
    """Return [(filename, description, bytes)] for one full exchange, twice."""
    client = hpack.Encoder()
    server = hpack.Encoder()

    req1 = client.encode(REQUEST_FIELDS)
    req2 = client.encode(REQUEST_FIELDS)  # same request, warm dynamic table
    res1 = server.encode(RESPONSE_FIELDS)
    res2 = server.encode(RESPONSE_FIELDS)

    settings = F.pack_settings(
        {
            F.SETTING_MAX_FRAME_SIZE: F.DEFAULT_MAX_FRAME_SIZE,
            F.SETTING_MAX_TABLE_ENTRIES: hpack.MAX_TABLE_ENTRIES,
        }
    )
    end = F.FLAG_END_HEADERS | F.FLAG_END_STREAM

    return [
        ("01-preface.bin", "client connection preface, 8 bytes, sent once", F.MAGIC),
        ("02-client-settings.bin", "client SETTINGS frame", settings),
        (
            "03-request-stream1.bin",
            "HEADERS: GET /hello.txt on stream 1, cold dynamic table",
            F.pack_frame(F.TYPE_HEADERS, end, 1, req1),
        ),
        (
            "04-response-headers-stream1.bin",
            "HEADERS: 200, text/plain, content-length 18",
            F.pack_frame(F.TYPE_HEADERS, F.FLAG_END_HEADERS, 1, res1),
        ),
        (
            "05-response-data-stream1.bin",
            "DATA: the 18 body bytes, END_STREAM",
            F.pack_frame(F.TYPE_DATA, F.FLAG_END_STREAM, 1, BODY),
        ),
        (
            "06-request-stream3.bin",
            "the identical request on stream 3, warm dynamic table",
            F.pack_frame(F.TYPE_HEADERS, end, 3, req2),
        ),
        (
            "07-response-headers-stream3.bin",
            "the identical response on stream 3, warm dynamic table",
            F.pack_frame(F.TYPE_HEADERS, F.FLAG_END_HEADERS, 3, res2),
        ),
        (
            "08-unknown-frame.bin",
            "frame type 0x7f: a conformant receiver discards 16 bytes and continues",
            F.pack_frame(0x7F, 0x00, 0, bytes(range(16))),
        ),
    ]


def main(argv):
    vectors = build()
    checking = "--check" in argv

    if checking:
        bad = 0
        for name, _, data in vectors:
            path = os.path.join(OUT, name)
            try:
                with open(path, "rb") as fh:
                    stored = fh.read()
            except OSError:
                print("MISSING  %s" % name)
                bad += 1
                continue
            if stored != data:
                print("CHANGED  %s (stored %d bytes, built %d)" % (name, len(stored), len(data)))
                bad += 1
            else:
                print("ok       %s  %d bytes" % (name, len(data)))
        if bad:
            print("\n%d vector(s) no longer match the implementation." % bad)
            print("Either the code regressed, or the spec changed and both ends must agree.")
        return 1 if bad else 0

    os.makedirs(OUT, exist_ok=True)
    for name, description, data in vectors:
        with open(os.path.join(OUT, name), "wb") as fh:
            fh.write(data)
        print("%s  (%d bytes) — %s" % (name, len(data), description))
        for line in hexdump(data):
            print("    " + line)
        print()

    req1 = len(vectors[2][2]) - F.HEADER_LEN
    req2 = len(vectors[5][2]) - F.HEADER_LEN
    print("header block: %d bytes cold, %d bytes warm (%.0f%% smaller)"
          % (req1, req2, 100.0 * (req1 - req2) / req1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
