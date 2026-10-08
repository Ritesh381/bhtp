#!/usr/bin/env python3
"""Generate the binary test fixtures. No third-party imaging library.

    python3 tests/make-fixtures.py

Writes www/logo.png (a real 64x64 PNG) and www/big.bin (17 MiB, which forces
multi-frame DATA at the default 16 KiB max frame size).
"""

import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(os.path.dirname(HERE), "www")

BIG_SIZE = 17 * 1024 * 1024


def chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def make_png(path, size=64):
    rows = bytearray()
    for y in range(size):
        rows.append(0)  # filter type 0 for this scanline
        for x in range(size):
            rows += bytes((x * 4 % 256, y * 4 % 256, (x ^ y) * 4 % 256))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    return len(png)


def make_big(path, size=BIG_SIZE):
    # Deterministic, so two machines produce identical bytes.
    rng = 0x12345678
    block = bytearray()
    for _ in range(65536):
        rng = (1103515245 * rng + 12345) & 0xFFFFFFFF
        block.append((rng >> 16) & 0xFF)
    with open(path, "wb") as fh:
        written = 0
        while written < size:
            take = min(len(block), size - written)
            fh.write(block[:take])
            written += take
    return size


def main():
    os.makedirs(WWW, exist_ok=True)
    png = os.path.join(WWW, "logo.png")
    big = os.path.join(WWW, "big.bin")
    print("logo.png  %d bytes" % make_png(png))
    if os.path.exists(big) and os.path.getsize(big) == BIG_SIZE:
        print("big.bin   already present, %d bytes" % BIG_SIZE)
    else:
        print("big.bin   %d bytes" % make_big(big))
    return 0


if __name__ == "__main__":
    sys.exit(main())
