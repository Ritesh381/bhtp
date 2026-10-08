#!/usr/bin/env bash
# The conformance matrix. Run from anywhere:
#
#     ./tests/interop.sh                  # test bserve + bcurl together
#     BHTP_SERVER=host:port ./tests/interop.sh   # test OUR client against THEIR server
#
# Exits non-zero if any check fails.

set -uo pipefail
set +m   # no "Terminated:" job-control chatter when we kill our own servers

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
cd "$ROOT"

PORT="${PORT:-9000}"
COUNT_PORT="${COUNT_PORT:-9100}"
TMP="$(mktemp -d)"
PASS=0
FAIL=0
OWN_SERVER=""

# Killing a background job makes bash announce "Terminated". Swallow it.
quiet_kill() {
  [ -z "${1:-}" ] && return 0
  { kill "$1" 2>/dev/null; wait "$1" 2>/dev/null; } 2>/dev/null
}

cleanup() {
  quiet_kill "$OWN_SERVER"
  quiet_kill "${COUNT_SERVER:-}"
  rm -rf "$TMP"
}
trap cleanup EXIT

md5of() {
  if command -v md5sum >/dev/null 2>&1; then md5sum "$1" | cut -d' ' -f1
  else md5 -q "$1"; fi
}

ok()   { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }
check() { if [ "$1" = "0" ]; then ok "$2"; else bad "$2"; fi; }

# raw <number> <case> — run a hand-crafted protocol case, report its own message
raw() {
  local num="$1" case="$2" out rc
  out="$(python3 tests/rawclient.py "$case" "$HOST" "$PORTNUM" 2>&1)"
  rc=$?
  if [ $rc -eq 0 ]; then ok "$num $(echo "$out" | sed 's/^PASS  //')"
  else bad "$num $(echo "$out" | sed 's/^FAIL  //')"; fi
}

# ---------------------------------------------------------------- fixtures
[ -f www/big.bin ] || python3 tests/make-fixtures.py >/dev/null
[ -f www/logo.png ] || python3 tests/make-fixtures.py >/dev/null

# ---------------------------------------------------------------- server
if [ -n "${BHTP_SERVER:-}" ]; then
  HOSTPORT="$BHTP_SERVER"
  echo "Testing our client against an external server at $HOSTPORT"
else
  ./bserve ./www "$PORT" >"$TMP/server.log" 2>&1 &
  OWN_SERVER=$!
  HOSTPORT="localhost:$PORT"
  sleep 1
  if ! kill -0 $OWN_SERVER 2>/dev/null; then
    echo "bserve failed to start:"; cat "$TMP/server.log"; exit 1
  fi
  echo "Testing bserve + bcurl on $HOSTPORT"
fi
HOST="${HOSTPORT%%:*}"
PORTNUM="${HOSTPORT##*:}"
echo

# ---------------------------------------------------------------- 0 vectors
echo "Conformance vectors (the spec's contract)"
if python3 tests/make-vectors.py --check >"$TMP/vectors" 2>&1; then
  ok "0  all $(grep -c '^ok ' "$TMP/vectors") byte vectors still match the implementation"
else
  bad "0  byte vectors drifted from the implementation"
  sed 's/^/        /' "$TMP/vectors"
fi
echo

# ---------------------------------------------------------------- 1-3 bodies
echo "Bodies"
./bcurl "$HOSTPORT/index.html" >"$TMP/index.html" 2>"$TMP/e1"
if cmp -s www/index.html "$TMP/index.html"; then ok "1  index.html byte-for-byte"
else bad "1  index.html byte-for-byte"; fi

./bcurl "$HOSTPORT/logo.png" >"$TMP/logo.png" 2>"$TMP/e2"
if [ "$(md5of www/logo.png)" = "$(md5of "$TMP/logo.png")" ]; then
  ok "2  logo.png md5 matches ($(md5of www/logo.png))"
else bad "2  logo.png md5 matches"; fi

./bcurl "$HOSTPORT/big.bin" >"$TMP/big.bin" 2>"$TMP/e3"
if [ "$(md5of www/big.bin)" = "$(md5of "$TMP/big.bin")" ]; then
  ok "3  big.bin md5 matches across $(( 17*1024*1024 / 16384 + 1 )) DATA frames"
else bad "3  big.bin md5 matches (multi-frame DATA)"; fi

# ---------------------------------------------------------------- 4 keep-alive
echo
echo "Connection reuse"
if [ -z "${BHTP_SERVER:-}" ]; then
  ./bserve ./www "$COUNT_PORT" >"$TMP/count.log" 2>&1 &
  COUNT_SERVER=$!
  sleep 1
  ./bcurl "localhost:$COUNT_PORT/index.html" /hello.txt /logo.png /index.html \
      /hello.txt /logo.png >/dev/null 2>&1
  sleep 0.3
  N=$(grep -c 'accepted connection' "$TMP/count.log")
  quiet_kill "$COUNT_SERVER"; COUNT_SERVER=""
  if [ "$N" = "1" ]; then ok "4  6 requests arrived on exactly 1 connection"
  else bad "4  6 requests arrived on $N connections, expected 1"; fi
else
  ok "4  skipped (external server; see README for the tcpdump proof)"
fi

# ---------------------------------------------------------------- 5 errors
echo
echo "Errors and safety"
./bcurl "$HOSTPORT/definitely-not-here.html" >/dev/null 2>&1
[ $? -ne 0 ] && ok "5  missing path → 404, bcurl exits non-zero" \
             || bad "5  missing path → 404, bcurl exits non-zero"

raw "6 " lying-length
raw "6b" oversize-frame
raw "6c" bad-header-index
raw "6d" bad-preface

./bcurl "$HOSTPORT/../../../../etc/passwd" >"$TMP/trav" 2>/dev/null
if [ $? -ne 0 ] && ! grep -q 'root:' "$TMP/trav"; then
  ok "9  path traversal refused, nothing outside root served"
else bad "9  path traversal refused"; fi

raw "10" abandon-mid-data

# ---------------------------------------------------------------- 7-8 extensibility
echo
echo "Room for a version 2"
raw "7 " unknown-frame
raw "8 " unknown-flags

# ---------------------------------------------------------------- 13 validators
echo
echo "Conditional requests"
./bcurl -r "$HOSTPORT/hello.txt" >/dev/null 2>"$TMP/reval"
if grep -q 'status 304' "$TMP/reval"; then
  ok "13 etag round-trip: if-none-match returned 304 with no body"
else
  bad "13 etag round-trip returned 304"
  sed 's/^/        /' "$TMP/reval"
fi

# ---------------------------------------------------------------- 14 hostile peer
echo
echo "Client against a hostile server (bcurl must not be the weak side)"
if [ -z "${BHTP_SERVER:-}" ]; then
  while IFS= read -r line; do
    case "$line" in
      PASS*) ok "14 ${line#PASS  }" ;;
      FAIL*) bad "14 ${line#FAIL  }" ;;
      *) [ -n "$line" ] && echo "        $line" ;;
    esac
  done < <(python3 tests/rawserver.py 2>&1)
else
  ok "14 skipped (external server under test, not our client)"
fi

# ---------------------------------------------------------------- 11 header shrink
echo
echo "Header compression"
./bcurl --stats "$HOSTPORT/hello.txt" /hello.txt /logo.png >/dev/null 2>"$TMP/stats"
FIRST=$(grep 'stream=1 ' "$TMP/stats" | sed 's/.*header_block=//')
SECOND=$(grep 'stream=3 ' "$TMP/stats" | sed 's/.*header_block=//')
if [ -n "$FIRST" ] && [ -n "$SECOND" ] && [ "$SECOND" -lt "$FIRST" ]; then
  ok "11 header block shrank ${FIRST}B → ${SECOND}B on the second request"
else bad "11 header block shrank on the second request (got '$FIRST' → '$SECOND')"; fi

# ---------------------------------------------------------------- 12 pipelining
echo
echo "Pipelining (bonus)"
./bcurl -p "$HOSTPORT/index.html" /hello.txt /logo.png >"$TMP/pipe" 2>/dev/null
cat www/index.html www/hello.txt www/logo.png >"$TMP/pipe.expect"
if cmp -s "$TMP/pipe" "$TMP/pipe.expect"; then
  ok "12 3 requests in flight at once, bodies reassembled by stream id"
else bad "12 3 requests in flight at once, bodies reassembled by stream id"; fi

# ---------------------------------------------------------------- summary
echo
echo "================================"
printf '%d passed, %d failed\n' "$PASS" "$FAIL"
if [ -z "${BHTP_SERVER:-}" ]; then
  echo
  echo "Partner interop is the one check this script cannot do for you:"
  echo "  BHTP_SERVER=their-host:port ./tests/interop.sh"
  echo "  ./bserve ./www 9000      # then have them point their client here"
fi
[ "$FAIL" -eq 0 ]
