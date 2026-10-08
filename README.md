# BHTP/1 — HTTP, in binary

SST Network Architecture course project. A binary reimagining of HTTP/1.1: fixed-size frame
headers, a numbered header table, one TCP connection for everything.

Python 3.8+, **standard library only**. No frameworks, no pip install, nothing to build.

> **Track:** `bserve` (the server) is the submitted track. `bcurl` is a reference client,
> written so that the spec could be validated end to end and so that the conformance vectors
> could be proved to come from two independent sides of the wire. Cross-implementation results
> against a classmate's client are at the bottom of this file.

---

## Run it

```bash
python3 tests/make-fixtures.py      # generates www/logo.png and www/big.bin
./bserve ./www 9000 &               # terminal 1
./bcurl -v localhost:9000/index.html
```

Everything else:

```bash
./tests/interop.sh                  # the whole conformance matrix, 25 checks
```

Expected output ends with `25 passed, 0 failed`.

---

## Read it, in this order

| file | what it is |
|------|------------|
| **`SPEC.md`** | the protocol. Two pages, normative, everything a stranger needs to implement it. |
| **`PARTNER.md`** | hand this to whoever takes the client track. Build order, the five values to agree on, the traps. |
| **`RATIONALE.md`** | the defence. Why each field is the width it is, every divergence from HTTP/2, what it costs on the wire. |
| **`HEXDUMP.md`** | one complete request and response, every byte annotated, plus the same request against a warm header table and a 304. |
| `bhtp/frame.py` | the 8-byte frame header and the socket reader |
| `bhtp/hpack.py` | static table + dynamic table |
| `bserve` | the server |
| `bcurl` | the client |
| `tests/vectors/` | the conformance bytes. The spec's actual contract. |

---

## The two programs

### `bserve <root> <port>`

Static file server. One `fork()` per connection, the way Apache did it before threads.
`BHTP_NO_FORK=1` keeps it in one process for debugging; `BHTP_VERBOSE=1` hexdumps every frame
it handles.

Serves any file under `root`; `/` maps to `index.html`. Nothing outside `root` is reachable
however many `../` segments a request contains. Sends an `etag` with every `200` and answers
`304` to a matching `if-none-match`.

| fault | answer | level |
|-------|--------|-------|
| missing `:method` / `:path` | `400` | stream — connection stays open |
| method other than GET | `405` | stream |
| no such file, or outside root | `404` | stream |
| undecodable header block | `GOAWAY 400` | connection — the tables have desynchronised |
| frame above the negotiated max | `GOAWAY 413` | connection |
| client is not speaking BHTP/1 | `GOAWAY 505` | connection |

### `bcurl [-v] [-p] [-r] [--stats] [-o FILE] host:port/path [more paths…]`

| flag | effect |
|------|--------|
| `-v` | hexdump every frame, both directions, to **stderr** — so `./bcurl host:port/logo.png > out.png` stays byte-exact |
| `-p` | pipeline: put every request in flight before reading any response |
| `-r` | after fetching, re-request with `if-none-match` and show the 304 |
| `--stats` | print header-block sizes, which is how you watch the dynamic table work |
| `-o FILE` | write bodies to a file instead of stdout |

Body bytes reach stdout untouched. Exit status: `0` all fine, `1` some response was 4xx, `2`
some response was 5xx, `3` the connection or the protocol broke.

**One connection, ever.** Extra paths are fetched on the same socket, including the `-r`
revalidation pass. Giving a second `host:port` is a hard error rather than a second `connect()`.

---

## What the test matrix checks

```
Conformance vectors   0  all 8 byte vectors still match the implementation
Bodies                1  index.html byte-for-byte
                      2  logo.png md5 matches
                      3  big.bin md5 matches across 1089 DATA frames
Connection reuse      4  6 requests arrived on exactly 1 connection
Errors and safety     5  missing path → 404, bcurl exits non-zero
                      6  a frame header that lies about its length did not take the server down
                      6b frame above the negotiated maximum → GOAWAY 413
                      6c undefined dynamic index → GOAWAY 400
                      6d HTTP/1.1 request → GOAWAY 505
                      9  path traversal refused
                      10 client vanishing mid-DATA left the server healthy
Room for a version 2  7  unknown frame type 0x7f skipped, next request still served
                      8  reserved flag bit ignored
Conditional requests  13 etag round-trip: if-none-match returned 304 with no body
Hostile server        14 eight ways a lying server fails to break bcurl
Header compression    11 header block shrank 63B → 17B on the second request
Pipelining (bonus)    12 3 requests in flight at once, reassembled by stream id
```

Two adversarial harnesses, because either side can be the weak one:

```bash
python3 tests/rawclient.py unknown-frame localhost 9000   # hostile client vs our server
python3 tests/rawserver.py http1-reply                    # hostile server vs our client
```

Run either with no case name to see the list. `rawserver.py` with no arguments runs all eight.

### Proving the single connection yourself

Test 4 counts connections in the server's own log. The independent check:

```bash
sudo tcpdump -i lo0 -n "tcp port 9000 and tcp[tcpflags] & tcp-syn != 0" &
./bcurl localhost:9000/index.html /hello.txt /logo.png /index.html /hello.txt /logo.png
# exactly one SYN for six requests
```

---

## Interoperating with someone else's implementation

The spec is the only thing that should cross between two implementations. To test this client
against another server:

```bash
BHTP_SERVER=their-host:9000 ./tests/interop.sh
```

To let someone test their client against this server, run `./bserve ./www 9000` and point them
at it.

Before writing any code, two implementations should agree on exactly five things. These are
where independent implementations diverge in practice:

1. the 8 preface bytes
2. the 10 static table names **and their order**
3. the frame header field widths
4. the default maximum frame size
5. whether the dynamic table is mandatory or negotiated through SETTINGS

Then check both ends reproduce `tests/vectors/` byte for byte:

```bash
python3 tests/make-vectors.py --check
```

A disagreement shows up there in seconds instead of on submission day.

### Cross-implementation results

| partner | their track | direction | result |
|---------|-------------|-----------|--------|
| _to fill in_ | client | their `bcurl` → this `bserve` | — |
| _to fill in_ | client | this `bcurl` → their `bserve` | — |
