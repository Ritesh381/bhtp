#!/usr/bin/env python3
"""Hostile servers, to test that bcurl survives a peer that is lying to it.

    python3 tests/rawserver.py [case]        # one case, or all of them

"I would actually literally run your client against the server" — so the
client has to be as hard to break as the server. Every case here binds an
ephemeral port, speaks something wrong, and asserts that bcurl exits with a
sane status and no Python traceback.

Cases
    http1-reply     answer a BHTP client in HTTP/1.1 text
    goaway-at-once  GOAWAY 400 before any response
    oversize        declare a frame larger than the negotiated maximum
    bad-status      :status whose value is not 2 bytes
    bad-index       reference a dynamic-table index that was never defined
    truncated       promise 18 body bytes, send 5, hang up
    unknown-frame   an unknown frame type, then a correct response
    ghost-stream    DATA for a stream the client never opened, then a response
"""

import os
import socket
import struct
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from bhtp import frame as F
from bhtp import hpack

BODY = b"hello from BHTP/1\n"
END = F.FLAG_END_HEADERS


def ok_headers(stream=1, length=len(BODY), status=200):
    enc = hpack.Encoder()
    return F.pack_frame(
        F.TYPE_HEADERS,
        END,
        stream,
        enc.encode(
            [
                (b":status", struct.pack(">H", status)),
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", struct.pack(">I", length)),
            ]
        ),
    )


def ok_body(stream=1, body=BODY):
    return F.pack_frame(F.TYPE_DATA, F.FLAG_END_STREAM, stream, body)


# -- the hostile behaviours ----------------------------------------------


def case_http1_reply(conn):
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Type: text/plain\r\n\r\nhi"
    )


def case_goaway_at_once(conn):
    conn.sendall(F.pack_goaway(F.ERR_MALFORMED, "I don't like you"))


def case_oversize(conn):
    conn.sendall(F.pack_header(F.TYPE_DATA, 0, 1, 0xFFFFFF))


def case_bad_status(conn):
    enc = hpack.Encoder()
    conn.sendall(
        F.pack_frame(
            F.TYPE_HEADERS,
            END | F.FLAG_END_STREAM,
            1,
            enc.encode([(b":status", b"\xc8")]),  # one byte, not two
        )
    )


def case_bad_index(conn):
    conn.sendall(
        F.pack_frame(F.TYPE_HEADERS, END | F.FLAG_END_STREAM, 1, bytes((0x80 | 99,)))
    )


def case_truncated(conn):
    conn.sendall(ok_headers())
    conn.sendall(F.pack_frame(F.TYPE_DATA, 0, 1, b"hello"))  # 5 of the promised 18


def case_unknown_frame(conn):
    conn.sendall(F.pack_frame(0x7F, 0x00, 0, b"\xde\xad\xbe\xef" * 8))
    conn.sendall(ok_headers())
    conn.sendall(ok_body())


def case_ghost_stream(conn):
    conn.sendall(F.pack_frame(F.TYPE_DATA, F.FLAG_END_STREAM, 99, b"not yours"))
    conn.sendall(ok_headers())
    conn.sendall(ok_body())


# case -> (behaviour, allowed exit codes, expected stdout or None)
CASES = {
    "http1-reply": (case_http1_reply, {3}, None),
    "goaway-at-once": (case_goaway_at_once, {3}, None),
    "oversize": (case_oversize, {3}, None),
    "bad-status": (case_bad_status, {3}, None),
    "bad-index": (case_bad_index, {3}, None),
    "truncated": (case_truncated, {3}, None),
    "unknown-frame": (case_unknown_frame, {0}, BODY),
    "ghost-stream": (case_ghost_stream, {0}, BODY),
}


def run_case(name):
    behaviour, allowed_exits, expected_body = CASES[name]

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    error = []

    def serve():
        try:
            conn, _ = listener.accept()
            conn.settimeout(10)
            try:
                F.recv_exactly(conn, len(F.MAGIC))  # consume the preface
                behaviour(conn)
            finally:
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
        except Exception as exc:  # the harness itself broke, not the client
            error.append(exc)
        finally:
            listener.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()

    proc = subprocess.run(
        [os.path.join(ROOT, "bcurl"), "127.0.0.1:%d/hello.txt" % port],
        capture_output=True,
        timeout=30,
    )
    thread.join(timeout=5)

    problems = []
    if error:
        problems.append("harness error: %r" % error[0])
    if proc.returncode not in allowed_exits:
        problems.append(
            "exit %d, wanted one of %s" % (proc.returncode, sorted(allowed_exits))
        )
    if b"Traceback" in proc.stderr:
        problems.append("bcurl crashed with a traceback")
    if expected_body is not None and proc.stdout != expected_body:
        problems.append("body was %r" % proc.stdout[:40])

    detail = (proc.stderr.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
    if problems:
        print("FAIL  %-15s %s" % (name, "; ".join(problems)))
        return 1
    print("PASS  %-15s exit %d — %s" % (name, proc.returncode, detail or "clean"))
    return 0


def main(argv):
    names = [argv[1]] if len(argv) > 1 else list(CASES)
    for name in names:
        if name not in CASES:
            sys.stderr.write(__doc__)
            return 2
    return 1 if sum(run_case(n) for n in names) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
