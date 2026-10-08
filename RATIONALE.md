# Why BHTP/1 looks like this

`SPEC.md` says what the protocol is, in two pages, for a stranger who has to implement it.
This file is the defence: every field width, every divergence from HTTP/2, and what the
result actually costs on the wire. Nothing here is normative.

---

## 1. Relationship to HTTP

BHTP/1 is a binary re-encoding of **HTTP/1.1 semantics**. Everything it can express, HTTP/1.1
can express: GET, a path, a host, a content type, a length, a status, an entity tag, a
conditional request, and a connection that stays open. It adds no capability HTTP/1.1 lacks;
the whole difference is how the bytes are arranged.

It independently arrives at two of HTTP/2's mechanisms, because both are forced on anyone
re-encoding HTTP in binary:

- **Self-delimiting frames**, because a binary body cannot be terminated by a sentinel. Any
  byte you reserve as a delimiter is a byte a PNG is allowed to contain.
- **A numbered header table**, because the measured cost of HTTP/1.1 is not its bodies. It is
  re-sending the same `User-Agent` on every request of a page load.

Everything else HTTP/2 carries is left out: no Huffman coding, no priority tree, no flow
control, no server push, no TLS requirement, no HPACK-style eviction. Those are the parts that
make HTTP/2 hard to implement correctly, and none of them is needed to show the two mechanisms
above working.

---

## 2. The frame header — 8 bytes

HTTP/2 uses `24 | 8 | 8 | 1 reserved + 31` = 9 bytes. BHTP/1 uses 8.

### `length` precedes `type`

This is the load-bearing decision of the whole format. A receiver reads 8 bytes, learns the
payload length, and can therefore discard a frame whose `type` it has never heard of.

Put `type` first and a receiver would need to understand a frame in order to find its end. The
skip rule becomes unimplementable, and the protocol can never gain a frame type without
breaking every deployed peer. Field *order* here matters more than any field *width*.

### `length` is 24 bits

A 16 MiB ceiling. The binding constraint is not addressing, it is memory: `length` is the
number of bytes a peer can make you buffer before you are allowed to act on any of them. It
has to be small enough to be safe against a hostile peer and large enough that the 8-byte
header is noise. The negotiated default is 16 KiB; a 17 MiB file goes out as 1089 DATA frames,
and `tests/interop.sh` test 3 checks it arrives byte-identical.

Widening this to 32 bits would buy nothing: nobody wants a 4 GiB frame, and the framing cost
at 16 KiB is already 0.05%.

### `stream` is 24 bits, odd/even split

16,777,215 identifiers outlast any realistic connection. The odd/even split — client-initiated
odd, server-initiated even — lets both ends allocate identifiers with no negotiation round
trip, which is the same trick HTTP/2 uses and for the same reason.

HTTP/2 spends 31 bits plus a reserved bit. Nothing in a request/response protocol needs two
billion concurrent streams; a connection that issued one stream per microsecond would take
half a minute to exhaust 24 bits.

### `flags` is 8 bits, five unused

Three flags are defined, five bits and 251 frame type numbers are spare. This is deliberate and
it is cheap. FastCGI kept reserved bytes in its record header and could extend without a new
version; TCP did not reserve room for a wider port number and now cannot grow one, which is why
a port is still 16 bits in 2026. Reserved bits are the cheapest future-proofing available.

### Why 8 bytes and not 9

8 is a power of two, so in a stream of frames every frame header lands on an 8-byte boundary
and a parser reads one aligned word instead of straddling. The entire cost is 7 stream-ID bits,
already argued above to be surplus.

This is the one place BHTP/1 claims to improve on HTTP/2's choice, and the claim is modest:
HTTP/2 needed its 9th byte for a 31-bit stream ID plus a reserved bit, and we do not.

---

## 3. Error levels

A fault is answered on the stream whenever the connection is still trustworthy, and at the
connection level only when it is not. A missing `:method`, an unknown method, a path that does
not resolve: the framing is not in doubt, so these get a `:status` and the connection carries
on. Killing a connection over a 404 would make keep-alive pointless.

Two faults genuinely cannot be recovered:

- **A frame above the negotiated maximum (413).** There is no safe way to skip bytes you have
  refused to read, so the stream position is lost.
- **An undecodable header block (400).** Decoding is what appends to the dynamic table, so a
  block that fails partway has already left the receiver's table and the sender's encoder
  disagreeing about what index 11 means. Every later index would silently decode to the wrong
  field. Failing loudly is the only honest option, and it is the reason a reserved prefix byte
  is an error rather than something to skip: a receiver that cannot name a field must not guess.

---

## 4. The header table

### Ten names, and which ten

The static table holds exactly the names this protocol puts on the wire: five in a request
(`:method`, `:path`, `host`, `user-agent`, `accept-encoding`), four in a response (`:status`,
`content-type`, `content-length`, `etag`), one in a conditional request (`if-none-match`). A
static table of names nobody sends is not compression, it is decoration.

`date` is the notable omission. HTTP/1.1 needs it for `Last-Modified` arithmetic, which is only
as reliable as the agreement between two clocks. BHTP/1 validates with `etag` instead, and has
no caching intermediary that needs a wall-clock stamp.

The ten names and their order are frozen. Adding a static entry later would renumber the table
and silently break every deployed peer — the failure would not even look like a protocol error,
just wrong header names. A future version therefore extends through new **frame types**, never
through new static indexes.

### Append-only, with stable indexes

HPACK evicts from the far end of its table and renumbers as it goes, so an HPACK index means
different fields at different times. A desynchronised table is the classic HPACK implementation
bug, and it fails silently: both sides keep parsing, and the headers are simply wrong.

BHTP/1 gives that up. The table is append-only with absolute indexes; once the 117 slots are
full, senders fall back to literals. An index means one field for the life of the connection.

The cost is a little compression on a very long connection. The benefit is that the one bug
class that is both easy to write and impossible to notice cannot occur — which, for a protocol
whose point is that a stranger's implementation must interoperate with mine, is the better
trade.

### Why not gzip the headers

Because compressed length leaks secret length, and header blocks carry cookies and bearer
tokens. This is exactly why HTTP/2 built HPACK instead of reusing the gzip it already had, and
the reasoning applies unchanged to a table-only scheme. It is also why the table stores whole
fields rather than trying to be clever about values.

---

## 5. Framing choices inherited from elsewhere

The length-prefixed body is not a style preference. HTTP/1.1 can state a body's length two
ways — `Content-Length` and `Transfer-Encoding: chunked` — and when a request carries both,
two implementations can disagree about where the body ends. That disagreement is request
smuggling. In BHTP/1 a body is only ever a count of bytes in a frame header, so there is no
second way to say it and nothing to smuggle a request through.

The preface is the `PRI * HTTP/2.0` trick with different bytes. `BHTP/1\r\n` is not a valid
HTTP method, so an HTTP/1.1 server rejects a BHTP client immediately, and a BHTP server
recognises a non-BHTP client on the first 8 bytes. Version detection costs one round trip and
is never ambiguous. The asymmetry — servers send no preface — is intentional: the party that
chose the protocol is the party that has to announce it.

---

## 6. What it costs on the wire

Measured, not asserted. `HEXDUMP.md` annotates every byte behind these numbers and
`./tests/interop.sh` reproduces them.

| | HTTP/1.1 text | BHTP/1 cold | BHTP/1 warm |
|---|---|---|---|
| request | 103 B | 71 B | **25 B** |
| response headers | 101 B | 62 B | 35 B |
| conditional request | 133 B | — | 39 B |
| 304 response | 50 B | — | 27 B |

The header block itself goes from 63 bytes to 17, 73% smaller, because `:method`, `host`,
`user-agent` and `accept-encoding` each collapse to a single byte once both ends have agreed on
an index. The cold row is paid once per connection; the warm row is what the second and every
later request costs, which on a real page load is almost all of them.

**What this does not claim.** BHTP/1 does nothing for bodies — entropy coding a body is
`accept-encoding`'s job and is orthogonal to this protocol. The claim is narrow: the
per-request overhead of HTTP/1.1 is mostly repeated header text, and a numbered table removes
it. On a page of one large asset, BHTP/1 and HTTP/1.1 cost the same. On a page of thirty small
ones, which is what the web actually is, the difference is most of the overhead.
