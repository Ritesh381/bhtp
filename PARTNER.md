# If you're taking the client — read this, then `SPEC.md`

I built the server (Track 1). You'd be building the client (Track 2). This is an **individual**
submission for both of us: separate repos, separate marks. The only thing that crosses between
us is the spec.

Everything you need is two things in this repo:

- **`SPEC.md`** — the protocol, two pages, normative.
- **`tests/vectors/`** — eight files of actual bytes. These are the contract. Prose can be read
  two ways; bytes cannot.

You do **not** need my code, and you'll learn more if you don't read it. Write the client in
whatever language you like.

---

## The five things we have to agree on first

These are where two independent implementations diverge in practice. If we agree on these and
nothing else, our programs will talk.

| # | value | what it is |
|---|-------|------------|
| 1 | preface | `42 48 54 50 2f 31 0d 0a` — the 8 bytes `BHTP/1\r\n`, client sends, server never does |
| 2 | frame header | `length(24) \| type(8) \| flags(8) \| stream(24)` = 8 bytes, big-endian, **length first** |
| 3 | static table | ten names, **in this order**: `:method :path :status host content-length content-type user-agent accept-encoding etag if-none-match` |
| 4 | max frame size | 16384 by default, negotiable via SETTINGS |
| 5 | dynamic table | append-only, indexes 11–127, inserted whenever prefix bit `0x40` is **clear** |

If you want to change any of these, say so now and I'll bump the spec to v1.1. Changing them
after either of us has written code is the expensive way to find out.

---

## Build order that works

1. **Connect, send the preface, send SETTINGS, read frames in a loop.** Print each frame's
   type/flags/stream/length. Stop here and check it against `tests/vectors/01` and `02`.
2. **Encode one HEADERS frame** with `:method GET`, `:path`, `host`, `user-agent`,
   `accept-encoding`. Compare your bytes to `tests/vectors/03-request-stream1.bin`. When they
   match exactly, your encoder is right.
3. **Decode the response HEADERS.** Check against `04`. Watch out for the two traps below.
4. **Collect DATA frames** until one carries `END_STREAM`, write the body to stdout as **raw
   bytes**.
5. **Second request on the same socket**, stream 3. Your header block should drop to 17 bytes
   because four fields are now one byte each. `06-request-stream3.bin` is what it should look
   like.
6. **`-v` hexdump, exit codes, the skip rule.** Details below.

---

## The four things that will bite you

- **`:status` is a `u16`, not ASCII.** 200 on the wire is `00 c8`, not `"200"`. Same idea for
  `content-length`, which is a `u32`. If you get `"200"` you've mis-read §4 of the spec.
- **`length` comes before `type` on purpose.** You read 8 bytes, you know where the frame ends,
  and so you can **skip a frame type you've never heard of** — read `length` bytes, throw them
  away, keep going, do *not* close the connection. The slide calls this the one line you may not
  skip. `tests/vectors/08-unknown-frame.bin` is exactly this case; I will send it to your client.
- **The dynamic table is inserted into by both sides.** Encoder and decoder append a field at
  exactly the same moment — when the prefix byte has `0x40` clear. Miss one insertion and every
  later index silently decodes to the wrong field. This is the single most common bug in this
  kind of code.
- **Body bytes go to stdout untouched; `-v` output goes to stderr.** Otherwise
  `yourclient host:port/logo.png > out.png` corrupts the PNG. He said he'd test with a binary
  file, and he will.

Also required by the slide: **never open a second connection**, and **exit non-zero on 4xx/5xx**.

---

## Checking yourself, before we ever talk

```bash
# do your bytes match the contract?
# compare your output for GET /hello.txt against:
xxd tests/vectors/03-request-stream1.bin
```

Then point your client at my server:

```bash
./bserve ./www 9000          # I run this
yourclient -v localhost:9000/index.html
yourclient localhost:9000/logo.png > out.png && md5 out.png www/logo.png   # must match
yourclient localhost:9000/nope.html; echo $?                              # 404, non-zero exit
```

Things my server will do to you, all of which the spec covers: send an unknown frame type,
send `etag` and answer `304` to `if-none-match`, split a 17 MiB file across 1089 DATA frames,
answer `400`/`404`/`405` on the stream without dropping the connection, and `GOAWAY` if you
send a frame bigger than we agreed.

---

## What we each hand in

Separately. Mine: `SPEC.md`, `bserve`, the annotated hexdump. Yours: the same spec, your client,
your own annotated hexdump of one complete request and response. The spec is the shared part —
we should both ship the identical file, and both say in our READMEs which implementation we
tested against.
