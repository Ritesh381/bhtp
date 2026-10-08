# One complete exchange, annotated byte by byte

Produced by:

```
./bserve ./www 9000 &
./bcurl -v -r localhost:9000/hello.txt /hello.txt
```

`/hello.txt` is fetched **twice on one connection**, then revalidated a third
time, so three things are visible in one capture: the cold request, the same
request once the dynamic table is warm, and a conditional request that comes
back 304 with no body.

`>>>` is client to server, `<<<` is server to client. Offsets restart at `0000`
per frame; bytes `0000`–`0007` are always the frame header, `0008` onward the
payload. The SETTINGS exchange is shown once and then elided.

---

## 1. Preface — 8 bytes, once per connection

```
>>> 42 48 54 50 2f 31 0d 0a                              |BHTP/1..|
```

| bytes | meaning |
|-------|---------|
| `42 48 54 50 2f 31 0d 0a` | the literal `BHTP/1\r\n`. Not a valid HTTP method, so an HTTP/1.1 server rejects it at once; anything else here earns `GOAWAY 505` from a BHTP server. |

---

## 2. SETTINGS

```
>>> SETTINGS len=12 type=0x03 flags=0x00 stream=0
    0000  00 00 0c 03 00 00 00 00                           |........|
    0008  00 01 00 00 40 00 00 02  00 00 00 75              |....@......u|
```

| bytes | field | value |
|-------|-------|-------|
| `00 00 0c` | length | 12 |
| `03` | type | SETTINGS |
| `00` | flags | none — asks for an acknowledgement, is not one |
| `00 00 00` | stream | 0, the connection itself |
| `00 01` / `00 00 40 00` | setting 1, MAX_FRAME_SIZE | 16384 |
| `00 02` / `00 00 00 75` | setting 2, MAX_TABLE_ENTRIES | 117 |

The server answers with its own SETTINGS (adding MAX_CONCURRENT_STREAMS = 100)
and each side returns an empty `SETTINGS | ACK`: `00 00 00 03 04 00 00 00`.

---

## 3. Request 1 — cold dynamic table

```
>>> HEADERS len=63 type=0x01 flags=0x03 stream=1
    0000  00 00 3f 01 03 00 00 01                           |..?.....|
    0008  01 00 03 47 45 54 42 00  0a 2f 68 65 6c 6c 6f 2e  |...GETB../hello.|
    0018  74 78 74 04 00 09 6c 6f  63 61 6c 68 6f 73 74 07  |txt...localhost.|
    0028  00 12 62 63 75 72 6c 2f  31 2e 30 20 28 42 48 54  |..bcurl/1.0 (BHT|
    0038  50 2f 31 29 08 00 08 69  64 65 6e 74 69 74 79     |P/1)...identity|
```

**Frame header**

| bytes | field | value |
|-------|-------|-------|
| `00 00 3f` | length | 63 |
| `01` | type | HEADERS |
| `03` | flags | END_STREAM \| END_HEADERS — a complete request, no body to follow |
| `00 00 01` | stream | 1. Odd, so client-initiated. |

**Header block**, five fields:

| bytes | decode |
|-------|--------|
| `01` | prefix: static index 1 = `:method`, bit `0x40` clear → index this field |
| `00 03` | value length 3 |
| `47 45 54` | `GET`. The field `(:method, GET)` becomes **dynamic index 11**. |
| `42` | `0x40 \| 2` → static index 2 = `:path`, do **not** index — the path changes every request, so a table slot would be wasted |
| `00 0a` | value length 10 |
| `2f 68 65 6c 6c 6f 2e 74 78 74` | `/hello.txt` |
| `04` | static index 4 = `host`, index it |
| `00 09` `6c…74` | `localhost` → **dynamic index 12** |
| `07` | static index 7 = `user-agent`, index it |
| `00 12` `62…29` | `bcurl/1.0 (BHTP/1)`, 18 bytes → **dynamic index 13** |
| `08` | static index 8 = `accept-encoding`, index it |
| `00 08` `69…79` | `identity` → **dynamic index 14** |

71 bytes on the wire. The equivalent HTTP/1.1 request text is 103 bytes.

---

## 4. Response 1 headers

```
<<< HEADERS len=54 type=0x01 flags=0x02 stream=1
    0000  00 00 36 01 02 00 00 01                           |..6.....|
    0008  43 00 02 00 c8 06 00 19  74 65 78 74 2f 70 6c 61  |C.......text/pla|
    0018  69 6e 3b 20 63 68 61 72  73 65 74 3d 75 74 66 2d  |in; charset=utf-|
    0028  38 45 00 04 00 00 00 12  49 00 0b 36 61 63 37 36  |8E......I..6ac76|
    0038  61 64 37 2d 31 32                                 |ad7-12|
```

| bytes | decode |
|-------|--------|
| `00 00 36` | length 54 |
| `01` / `02` | HEADERS, END_HEADERS only — DATA follows, so no END_STREAM |
| `00 00 01` | stream 1, matching the request |
| `43` | `0x40 \| 3` → `:status`, not indexed |
| `00 02` `00 c8` | value length 2, then `0x00c8` = **200** as a `u16`. Not the ASCII `"200"`. |
| `06` | static index 6 = `content-type`, index it |
| `00 19` `74…38` | 25 bytes, `text/plain; charset=utf-8` → **dynamic index 11 of the server's table** |
| `45` | `0x40 \| 5` → `content-length`, not indexed |
| `00 04` `00 00 00 12` | value length 4, then `u32` 18 |
| `49` | `0x40 \| 9` → `etag`, not indexed |
| `00 0b` `36…32` | 11 bytes, `6ac76ad7-12` — mtime and size in hex, the validator §7 uses |

The two directions keep separate tables, which is why index 11 means
`(:method, GET)` going up and `(content-type, text/plain…)` coming down.
62 bytes on the wire against 101 bytes of HTTP/1.1 status line and headers.

---

## 5. Response 1 body

```
<<< DATA len=18 type=0x02 flags=0x01 stream=1
    0000  00 00 12 02 01 00 00 01                           |........|
    0008  68 65 6c 6c 6f 20 66 72  6f 6d 20 42 48 54 50 2f  |hello from BHTP/|
    0018  31 0a                                             |1.|
```

| bytes | decode |
|-------|--------|
| `00 00 12` | length 18 |
| `02` | DATA |
| `01` | END_STREAM — stream 1 is finished |
| `00 00 01` | stream 1 |
| `68 … 0a` | the body, verbatim. No escaping, no sentinel, nothing scanned. A PNG and this text file take exactly the same path. |

A body is only ever a byte count in a frame header, so there is no second way
to state a length and nothing for a `Content-Length` / `Transfer-Encoding`
disagreement to smuggle a request through.

---

## 6. Request 2 — warm dynamic table

Same request, same connection, stream 3.

```
>>> HEADERS len=17 type=0x01 flags=0x03 stream=3
    0000  00 00 11 01 03 00 00 03                           |........|
    0008  8b 42 00 0a 2f 68 65 6c  6c 6f 2e 74 78 74 8c 8d  |.B../hello.txt..|
    0018  8e                                                |.|
```

| bytes | decode |
|-------|--------|
| `00 00 11` | length 17, down from 63 |
| `00 00 03` | stream 3. Still odd, still client-initiated. |
| `8b` | `0x80 \| 11` → dynamic index 11, the whole field `:method: GET`, **one byte** |
| `42 00 0a 2f…74` | `:path: /hello.txt`, still a literal — it is the only thing that varies |
| `8c` | dynamic index 12 → `host: localhost` |
| `8d` | dynamic index 13 → `user-agent: bcurl/1.0 (BHTP/1)` |
| `8e` | dynamic index 14 → `accept-encoding: identity` |

**63 → 17 bytes, 73% smaller.** 13 of those 17 bytes are the path, the one
field that could not be indexed. The other four fields cost one byte each.

---

## 7. Response 2 headers

```
<<< HEADERS len=27 type=0x01 flags=0x02 stream=3
    0000  00 00 1b 01 02 00 00 03                           |........|
    0008  43 00 02 00 c8 8b 45 00  04 00 00 00 12 49 00 0b  |C.....E......I..|
    0018  36 61 63 37 36 61 64 37  2d 31 32                 |6ac76ad7-12|
```

| bytes | decode |
|-------|--------|
| `43 00 02 00 c8` | `:status` 200 — re-sent each time, because the next response may differ |
| `8b` | dynamic index 11 → `content-type: text/plain; charset=utf-8`, one byte instead of 28 |
| `45 00 04 00 00 00 12` | `content-length` 18, re-sent, because it changes per resource |
| `49 00 0b 36…32` | `etag`, re-sent, for the same reason |

54 → 27 bytes.

---

## 8. A conditional request, and the 304

`bcurl -r` now re-asks for the same resource carrying the validator the server
just gave it.

```
>>> HEADERS len=31 type=0x01 flags=0x03 stream=5
    0000  00 00 1f 01 03 00 00 05                           |........|
    0008  8b 42 00 0a 2f 68 65 6c  6c 6f 2e 74 78 74 8c 8d  |.B../hello.txt..|
    0018  8e 4a 00 0b 36 61 63 37  36 61 64 37 2d 31 32     |.J..6ac76ad7-12|
```

| bytes | decode |
|-------|--------|
| `8b … 8e` | the four indexed fields again, one byte each |
| `4a` | `0x40 \| 10` → static index 10 = `if-none-match`, not indexed |
| `00 0b` `36…32` | the etag from §4, handed straight back |

```
<<< HEADERS len=19 type=0x01 flags=0x03 stream=5
    0000  00 00 13 01 03 00 00 05                           |........|
    0008  43 00 02 01 30 49 00 0b  36 61 63 37 36 61 64 37  |C...0I..6ac76ad7|
    0018  2d 31 32                                          |-12|
```

| bytes | decode |
|-------|--------|
| `01` / `03` | HEADERS with **END_HEADERS \| END_STREAM** — the stream is over inside one frame, because a 304 has no body by definition |
| `43 00 02 01 30` | `:status` = `0x0130` = **304** |
| `49 00 0b 36…32` | the matching `etag` |
| — | no `content-length`, no DATA frame, 0 body bytes |

27 bytes on the wire, and the 18-byte body was not re-sent. On a real page the
body is not 18 bytes, which is the whole point of the mechanism: the request is
paid for, the payload is not.

---

## Totals for this exchange

| | HTTP/1.1 text | BHTP/1 cold | BHTP/1 warm |
|---|---|---|---|
| request | 103 B | 71 B | 25 B |
| response headers | 101 B | 62 B | 35 B |
| conditional request | 133 B | — | 39 B |
| 304 response | 50 B | — | 27 B |

The warm column is what a real page load looks like, where one connection
fetches a document and then thirty assets. The cold row is paid once per
connection.

---

## 9. An unknown frame, skipped

`tests/vectors/08-unknown-frame.bin` is a frame of a type that does not exist
in this version:

```
    0000  00 00 10 7f 00 00 00 00                           |........|
    0008  00 01 02 03 04 05 06 07  08 09 0a 0b 0c 0d 0e 0f  |........|
```

`00 00 10` says 16 payload bytes; `7f` is the unrecognised type. A conformant
receiver reads the 8-byte header, discards 16 bytes, and carries on — which is
what `tests/interop.sh` test 7 asserts by injecting this frame and then serving
a normal request down the same connection, and what test 14 asserts in the
other direction by feeding it to `bcurl`.

Nothing had to understand `0x7f` to find where it ended, because the length was
read before the type was consulted. That is the whole argument for the field
order in §2.1 of the spec, and it is the door a version 2 walks through.
