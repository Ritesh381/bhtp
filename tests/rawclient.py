#!/usr/bin/env python3
"""Hand-crafted BHTP/1 clients for the cases bcurl is too well-behaved to produce.

    python3 tests/rawclient.py <case> [host] [port]

Cases
    unknown-frame     send frame type 0x7f, then a normal GET on the same connection
    unknown-flags     send a GET with a reserved flag bit set
    bad-preface       speak HTTP/1.1 at a BHTP server
    lying-length      frame header promises more bytes than are sent, then hang up
    oversize-frame    frame larger than the negotiated maximum
    bad-header-index  reference a dynamic-table index that was never defined
    abandon-mid-data  ask for a big file and hang up after the first frame

Every case prints PASS or FAIL and exits 0 or 1.
"""

import os
import socket
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bhtp import frame as F
from bhtp import hpack


def connect(host, port, preface=True):
    sock = socket.create_connection((host, port), timeout=10)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if preface:
        F.send_all(sock, F.MAGIC)
    return sock


def get_block(path, host, encoder=None):
    encoder = encoder or hpack.Encoder()
    return encoder.encode(
        [
            (b":method", b"GET"),
            (b":path", path.encode()),
            (b"host", host.encode()),
        ]
    )


def read_until(sock, types, max_frames=200):
    """Collect frames until one of `types` shows up. Returns (frame, all_frames)."""
    reader = F.FrameReader(sock, F.DEFAULT_MAX_FRAME_SIZE)
    seen = []
    for _ in range(max_frames):
        fr = reader.read_frame()
        if fr is None:
            return None, seen
        seen.append(fr)
        if fr.type in types:
            return fr, seen
    return None, seen


def status_of(fr, decoder=None):
    fields = (decoder or hpack.Decoder()).decode(fr.payload)
    raw = hpack.find(fields, b":status")
    return struct.unpack(">H", raw)[0] if raw and len(raw) == 2 else None


def report(ok, message):
    print("%s  %s" % ("PASS" if ok else "FAIL", message))
    return 0 if ok else 1


# -- cases ----------------------------------------------------------------


def case_unknown_frame(host, port):
    sock = connect(host, port)
    try:
        F.send_all(sock, F.pack_frame(0x7F, 0x00, 0, b"\xde\xad\xbe\xef" * 4))
        F.send_all(
            sock,
            F.pack_frame(
                F.TYPE_HEADERS,
                F.FLAG_END_HEADERS | F.FLAG_END_STREAM,
                1,
                get_block("/index.html", host),
            ),
        )
        fr, seen = read_until(sock, {F.TYPE_HEADERS, F.TYPE_GOAWAY})
        if fr is None or fr.type == F.TYPE_GOAWAY:
            return report(False, "unknown frame type 0x7f was not skipped cleanly")
        code = status_of(fr)
        return report(
            code == 200,
            "unknown frame type 0x7f skipped, next request on the same "
            "connection returned %s" % code,
        )
    finally:
        sock.close()


def case_unknown_flags(host, port):
    sock = connect(host, port)
    try:
        flags = F.FLAG_END_HEADERS | F.FLAG_END_STREAM | 0x40
        F.send_all(
            sock, F.pack_frame(F.TYPE_HEADERS, flags, 1, get_block("/index.html", host))
        )
        fr, _ = read_until(sock, {F.TYPE_HEADERS, F.TYPE_GOAWAY})
        if fr is None or fr.type == F.TYPE_GOAWAY:
            return report(False, "reserved flag bit 0x40 was not ignored")
        return report(status_of(fr) == 200, "reserved flag bit 0x40 ignored, status 200")
    finally:
        sock.close()


def case_bad_preface(host, port):
    sock = connect(host, port, preface=False)
    try:
        F.send_all(sock, b"GET /index.html HTTP/1.1\r\nHost: x\r\n\r\n")
        fr, _ = read_until(sock, {F.TYPE_GOAWAY})
        if fr is None:
            return report(False, "HTTP/1.1 request got no GOAWAY")
        code, reason = F.parse_goaway(fr.payload)
        return report(
            code == F.ERR_VERSION_MISMATCH,
            "HTTP/1.1 request rejected with GOAWAY %d (%s)" % (code, reason),
        )
    finally:
        sock.close()


def case_lying_length(host, port):
    sock = connect(host, port)
    try:
        F.send_all(sock, F.pack_header(F.TYPE_DATA, 0, 1, 100) + b"only ten.."[:10])
        sock.close()
    except OSError:
        pass
    # The real assertion: the server is still accepting work afterwards.
    probe = connect(host, port)
    try:
        F.send_all(
            probe,
            F.pack_frame(
                F.TYPE_HEADERS,
                F.FLAG_END_HEADERS | F.FLAG_END_STREAM,
                1,
                get_block("/index.html", host),
            ),
        )
        fr, _ = read_until(probe, {F.TYPE_HEADERS, F.TYPE_GOAWAY})
        ok = fr is not None and fr.type == F.TYPE_HEADERS and status_of(fr) == 200
        return report(ok, "a frame header that lies about its length did not take the server down")
    finally:
        probe.close()


def case_oversize_frame(host, port):
    sock = connect(host, port)
    try:
        F.send_all(sock, F.pack_header(F.TYPE_DATA, 0, 1, 0xFFFFFF))
        fr, _ = read_until(sock, {F.TYPE_GOAWAY})
        if fr is None:
            return report(False, "oversize frame got no GOAWAY")
        code, reason = F.parse_goaway(fr.payload)
        return report(
            code == F.ERR_FRAME_TOO_LARGE,
            "frame above the negotiated maximum rejected with GOAWAY %d (%s)" % (code, reason),
        )
    finally:
        sock.close()


def case_bad_header_index(host, port):
    sock = connect(host, port)
    try:
        payload = bytes((0x80 | 120,))  # dynamic index 120, never defined
        F.send_all(
            sock,
            F.pack_frame(F.TYPE_HEADERS, F.FLAG_END_HEADERS | F.FLAG_END_STREAM, 1, payload),
        )
        fr, _ = read_until(sock, {F.TYPE_GOAWAY})
        if fr is None:
            return report(False, "undefined header index got no GOAWAY")
        code, reason = F.parse_goaway(fr.payload)
        return report(
            code == F.ERR_MALFORMED,
            "undefined dynamic index rejected with GOAWAY %d (%s)" % (code, reason),
        )
    finally:
        sock.close()


def case_abandon_mid_data(host, port):
    sock = connect(host, port)
    try:
        F.send_all(
            sock,
            F.pack_frame(
                F.TYPE_HEADERS,
                F.FLAG_END_HEADERS | F.FLAG_END_STREAM,
                1,
                get_block("/big.bin", host),
            ),
        )
        sock.recv(4096)  # take a little, then walk away
    except OSError:
        pass
    finally:
        sock.close()

    probe = connect(host, port)
    try:
        F.send_all(
            probe,
            F.pack_frame(
                F.TYPE_HEADERS,
                F.FLAG_END_HEADERS | F.FLAG_END_STREAM,
                1,
                get_block("/index.html", host),
            ),
        )
        fr, _ = read_until(probe, {F.TYPE_HEADERS, F.TYPE_GOAWAY})
        ok = fr is not None and fr.type == F.TYPE_HEADERS and status_of(fr) == 200
        return report(ok, "client vanishing mid-DATA left the server healthy")
    finally:
        probe.close()


CASES = {
    "unknown-frame": case_unknown_frame,
    "unknown-flags": case_unknown_flags,
    "bad-preface": case_bad_preface,
    "lying-length": case_lying_length,
    "oversize-frame": case_oversize_frame,
    "bad-header-index": case_bad_header_index,
    "abandon-mid-data": case_abandon_mid_data,
}


def main(argv):
    if len(argv) < 2 or argv[1] not in CASES:
        sys.stderr.write(__doc__)
        return 2
    host = argv[2] if len(argv) > 2 else "localhost"
    port = int(argv[3]) if len(argv) > 3 else 9000
    try:
        return CASES[argv[1]](host, port)
    except F.ProtocolError as exc:
        return report(False, "unexpected protocol error: %s" % exc)
    except OSError as exc:
        return report(False, "socket error: %s" % exc)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
