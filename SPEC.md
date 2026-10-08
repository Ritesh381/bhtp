# BHTP/1 — Binary Hypertext Transfer Protocol, version 1

**Status** stable · **Version** 1.0 · **Layer** application, over one TCP connection

BHTP/1 carries HTTP/1.1 semantics — method, path, headers, body, status, validator — in a
self-delimiting binary framing. It is not HTTP/2 and does not interoperate with it.

MUST, MUST NOT, SHOULD, MAY as in RFC 2119. All integers, in frame headers and in payloads,
are **unsigned big-endian**. Conformance bytes: `tests/vectors/`. Why it looks like this:
`RATIONALE.md`. Annotated capture: `HEXDUMP.md`.

## 1. Connection

One connection carries many exchanges and stays open until either side sends GOAWAY or closes
the socket. A client MUST NOT open a second connection for further requests to the same host
and port.

**Preface.** Immediately after `connect()`, before any frame, the client MUST send 8 bytes:

```
42 48 54 50 2f 31 0d 0a      "BHTP/1\r\n"
```

A server reading anything else MUST send `GOAWAY 505` and close. Servers MUST NOT send a
preface.

## 2. Frames

Every byte after the preface belongs to a frame: an 8-byte header, then exactly `length` bytes.

```
 0        1        2        3        4        5        6        7
+--------+--------+--------+--------+--------+--------+--------+--------+
|            length (24)           |  type  | flags  |    stream (24)   |
+--------+--------+--------+--------+--------+--------+--------+--------+
```

Streams are odd for client-initiated, even for server-initiated. Stream 0 means the connection
itself.

| type | name | payload |
|------|------|---------|
| `0x01` | HEADERS | a header block (§4) |
| `0x02` | DATA | body bytes, verbatim — never escaped, never scanned, no sentinel |
| `0x03` | SETTINGS | zero or more 6-byte settings (§3) |
| `0x04` | GOAWAY | `u16` error code, then an optional UTF-8 reason |
| other | — | see the extension rule below |

| flag | name | meaning |
|------|------|---------|
| `0x01` | END_STREAM | last frame this endpoint sends on this stream |
| `0x02` | END_HEADERS | this HEADERS frame holds a complete header block |
| `0x04` | ACK | SETTINGS only: acknowledges the peer's settings; payload MUST be empty |
| `0x08`–`0x80` | — | reserved |

A receiver MUST ignore reserved flag bits and flags undefined for the frame type received.

**The extension rule.** A receiver that meets a frame type it does not recognise MUST read and
discard exactly `length` bytes and continue reading frames normally. It MUST NOT close the
connection, send GOAWAY, or treat the frame as an error. This is possible only because
`length` precedes `type`, and it is what leaves room for a version 2.

A frame whose declared `length` exceeds the peer's MAX_FRAME_SIZE cannot be skipped and MUST be
answered with `GOAWAY 413`.

## 3. SETTINGS and errors

A SETTINGS payload is a sequence of 6-byte entries: `u16` identifier, `u32` value. Unknown
identifiers MUST be ignored. Each endpoint SHOULD send SETTINGS once before its first HEADERS
frame, and MUST answer a non-ACK SETTINGS with an empty `SETTINGS | ACK`. Until SETTINGS
arrives, defaults apply. An endpoint MUST NOT send a frame larger than its peer's
MAX_FRAME_SIZE.

| id | name | default | meaning |
|----|------|---------|---------|
| `0x0001` | MAX_FRAME_SIZE | 16384 | largest payload the sender will receive |
| `0x0002` | MAX_TABLE_ENTRIES | 117 | dynamic table capacity the sender maintains |
| `0x0003` | MAX_CONCURRENT_STREAMS | 100 | advisory |

| code | meaning | level |
|------|---------|-------|
| 304 | validator matched; the body is the client's own copy | stream |
| 400 | malformed request, or an undecodable header block | stream / connection |
| 404 | no such resource | stream |
| 405 | method not supported | stream |
| 413 | frame above the negotiated maximum | connection |
| 505 | the peer is not speaking BHTP/1 | connection |

A fault is **stream-level** — a `:status` on the affected stream, connection left open —
whenever the framing is not in doubt. It is **connection-level**, a GOAWAY, only for 413, 505,
and an undecodable header block, which has already desynchronised the dynamic tables.

## 4. Header blocks

A header block is the payload of a HEADERS frame: fields read until the payload is exhausted.
A field is a name and a value, both opaque byte strings. Names are lower-case ASCII and are
compared case-sensitively, so senders MUST lower-case them. A name is at most 255 bytes, a
value at most 65535.

Pseudo-field names begin with `:`. A request MUST carry `:method` and `:path`; a response MUST
carry `:status`. A receiver MUST accept fields in any order. `:status` is a `u16`, not ASCII
digits. `content-length` is a `u32`; a body of 4 GiB or more MUST omit it and rely on
END_STREAM.

**Static table.** Ten names, frozen, shared by both ends. Index 0 is unassigned so that a
prefix byte of `0x00` can mean "a literal name follows".

| 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|
| `:method` | `:path` | `:status` | `host` | `content-length` |

| 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|
| `content-type` | `user-agent` | `accept-encoding` | `etag` | `if-none-match` |

**Field encoding.** Each field starts with one prefix byte. Bit `0x80` means fully indexed;
bit `0x40` means do not add this field to the table.

| prefix | form | what follows |
|--------|------|--------------|
| `0x00` | literal name, literal value, index it | `u8` namelen, name, `u16` vallen, value |
| `0x01`–`0x0A` | static name at that index, literal value, index it | `u16` vallen, value |
| `0x40` | literal name, literal value, do not index | `u8` namelen, name, `u16` vallen, value |
| `0x41`–`0x4A` | static name at that index, literal value, do not index | `u16` vallen, value |
| `0x80 \| i` | dynamic field `i`, name **and** value | nothing — one byte total |
| `0x0B`–`0x3F`, `0x4B`–`0x7F` | reserved | receiver MUST answer `400` |

**Dynamic table.** Each endpoint keeps one per direction: an append-only table of complete
fields, indexed from 11 up to at most 127. A field is appended by both the sender and the
receiver exactly when its prefix byte has bit `0x40` clear, so the two tables are built from
the same information in the same order. When the table is full no further entries are added
and senders fall back to literals. An index, once assigned, means one field for the life of
the connection.

Senders SHOULD NOT index a field whose value changes per resource — `:path`, `:status`,
`content-length`, `etag`, `if-none-match`. That is advice; a receiver follows the prefix bits,
never a policy.

A header block MUST NOT be gzipped, deflated or otherwise entropy-coded. Compressed length
leaks secret length, and header blocks carry credentials.

## 5. Exchanges

A request is one HEADERS frame on a fresh odd stream with END_HEADERS and END_STREAM. Version 1
has no request bodies; a server MUST consume and ignore any DATA it receives. A response is one
HEADERS frame on the same stream, then zero or more DATA frames, the last carrying END_STREAM.
An empty body is signalled by END_STREAM on the response HEADERS frame itself.

```
client → HEADERS   stream 1  END_HEADERS|END_STREAM   :method GET, :path /hello.txt, host, …
server → HEADERS   stream 1  END_HEADERS              :status 200, content-type, length, etag
server → DATA      stream 1  END_STREAM               18 bytes
client → HEADERS   stream 3  END_HEADERS|END_STREAM   next request, same connection
client → HEADERS   stream 5  END_HEADERS|END_STREAM   …plus if-none-match
server → HEADERS   stream 5  END_HEADERS|END_STREAM   :status 304, etag. No body.
```

A client MAY keep several streams in flight and MUST reassemble by stream identifier; a server
MAY respond in any order.

## 6. Conditional requests

A `200` response SHOULD carry an `etag`, an opaque byte string that changes whenever the
representation does. A peer MUST treat it as opaque and compare only for equality.

A client holding an `etag` MAY repeat the request with `if-none-match` set to it. If the
validator still matches, the server MUST answer `:status 304` carrying the `etag`, with
END_HEADERS and END_STREAM on that one frame, and MUST NOT send `content-length` or any DATA.
Otherwise it answers as though no validator had been sent.

## 7. Conformance

An implementation conforms if it reproduces every file in `tests/vectors/` byte for byte and
passes `./tests/interop.sh`. The vectors cover the preface, SETTINGS, a cold request, its
response headers and body, the same request and response against a warm dynamic table, and an
unknown frame type.

**Absent by design:** TLS, request bodies, trailers, methods other than GET, Huffman coding,
stream priority, flow control, server push, HPACK-style eviction, and `0x80 | i` for `i < 11`
(a static index has a name but no value, so it can never be fully indexed).
