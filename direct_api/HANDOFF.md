# direct_api handoff

Replace the phone+Waydroid+mitmproxy scraper with pure HTTP calls to
TikTok's mobile search API. The signing is currently outsourced to
RapidAPI's `bytedance-services` MSSDK signer, but that's rate-limited to
20 requests/day on the BASIC plan — the outstanding work is to self-host
the signer using an existing open-source MSSDK/Argus port.

## What works today

`python3 scrape_keyword.py <keyword>` end-to-end scrapes **all
sort-by-likes videos** for a keyword, one page at a time, until it hits
a stop condition. No phone involved. The code was tested offline against
captured phone traffic and is protocol-correct, but we have not been
able to run it end-to-end because RapidAPI's BASIC plan gave out after
~20 sign calls (daily quota).

Verified by direct testing before quota ran out:

- `python3 replay_search.py mario` — HTTP 200, 30 videos, top like count
  4.6M (matched the phone capture).
- `python3 replay_search.py "kawaii desk"` — HTTP 200, 30 videos,
  like-sorted (proves it isn't replay-cache).
- `count=30` on `/aweme/v1/search/item/` is the hard ceiling; `≥35`
  returns empty.

Verified by phone capture (`captured_boston_paginated.jsonl`) without
replay:

- The Like-count-filter flow paginates at least to cursor=80 (i.e. ≥90
  videos per keyword; "boston" had 9 successful pages).
- There is only ONE endpoint for pagination — `/aweme/v1/search/item/`.
  Not `/general/search/single/`. That was my earlier mis-assumption
  based on CLAUDE.md; CLAUDE.md describes full TikTok's relevance-search
  flow, not Lite's like-sort flow.

## 2026-04-24 update — public signers tested, all silently rejected

Tried three public MSSDK-style signers end-to-end (local_signer.py,
try_metasec.py — still in this directory). All three produce `HTTP 200
+ status_code=0 + aweme_list=null` with `server_stream_time≈80ms`
(versus ~700ms for real searches). That's TikTok's **silent-reject
pattern**: signature is decoded but doesn't match the expected shape,
so the backend short-circuits the search and returns an empty response
instead of a 4xx.

Evidence the rejection is at the signature layer (not device/session):

- Phone-captured sig (same query, same headers, X-Khronos 2h stale) →
  same 200/empty. Re-signing the captured exact query with our signer
  also gives 200/empty.
- Earlier RapidAPI-signed calls from this same device returned 30
  videos — so the device + session are warm.
- Captured phone X-Argus is **120 bytes** decoded; SignerPy produces
  90, int4444 Metasec produces 306, iqbalmh18 produces 306. None match
  the phone. Lite (aid=1340) clearly uses a different protobuf shape
  than full TikTok (aid=1233) which is what all public signers target.
- Phone X-Gorgon prefix is `8404a0d61000...`. SignerPy produces
  `8404{random}0000...`, int4444 hardcodes `8404a0ae1000...`,
  iqbalmh18 produces `8404b4d94000...`. The 4 bytes after `8404` are
  app-specific constants and none of the public libs have the
  aid=1340 values.

Candidates tested:

| Signer | Source | Targets | Result |
|---|---|---|---|
| SignerPy 0.12.0 | PyPI (is-L7N) | aid=1233 GorgonV1 (`8404`) | 200 empty |
| Metasec | github.com/int4444/tiktok-api | aid=1233 `8404a0ae1000` | 200 empty |
| iqbalmh18 tiktok-signer | github.com/iqbalmh18/tiktok-signer | aid=1233 `8404b4d9...` | 200 empty |

**Conclusion: public ports all target full TikTok (aid=1233).** aid=1340
(TikTok Lite / musically_go) uses a specific lanusk and a
version-specific Argus protobuf shape that no public implementation
carries.

### Next step — Frida extraction is now the realistic path

See Step 2 in the original plan below. The key extractions needed:

1. **sign_key (32-byte AES key)** — used as AES-CBC key+IV for the
   final Argus wrapping. Live in `libtiktok.so` or `libmetasec_ov.so`.
2. **Argus protobuf field schema** — what fields does aid=1340 emit
   (and omit) vs int4444's schema. Hook the protobuf builder and dump.
3. **Gorgon prefix bytes** — the `a0 d6 10 00` after `8404`. Probably
   hardcoded per app-version; dump them from the binary.

With Frida on a rooted Waydroid+TikTok-Lite on the APK patching VM
(34.162.181.247), a single signing event captures all three. Then
port iqbalmh18's signer with the extracted constants substituted.

## RapidAPI blocker (still true — context for why we went down this path)

**RapidAPI BASIC plan = 20 sign requests per day.** Response header
from the 429 confirms this:

```
X-RateLimit-Requests-Limit: 20
X-RateLimit-Requests-Remaining: 0
X-RateLimit-Requests-Reset: 82917   (seconds)
```

Each TikTok page requires a fresh signature (the signer output contains
`X-Khronos` = wall-clock seconds and `X-Gorgon` = hash over query +
khronos, so nothing caches across requests). 3,524 keywords × ~10
pages/keyword = ~35,000 sign calls — three orders of magnitude above
the free quota.

Two ways out: **(a)** upgrade RapidAPI, or **(b)** stand up our own
signer. The user chose (b), and specifically asked for the low-effort
route — adopt an existing open-source port instead of reversing MSSDK
ourselves.

## Next session plan — stand up our own signer

### Step 1: hunt for an existing working signer

What we need: an implementation that takes a TikTok URL + device
fingerprint and returns `X-Argus`, `X-Ladon`, `X-Gorgon`, `X-Khronos`.

Search locations, in priority order:

- **GitHub** — search terms:
  - `tiktok signature aid 1340` (1340 = TikTok Lite `musically_go`)
  - `mssdk python` / `mssdk nodejs`
  - `x-argus tiktok python`
  - `license_id 224921550` (our exact MSSDK license for TikTok Lite —
    unlikely anyone publishes this verbatim, but worth grepping)
- **Gitee** — Chinese mirror; the best TikTok/ByteDance reversing is
  historically Chinese. Same search terms.
- **GitHub code search** with the RapidAPI endpoint path
  `/mssdk_common/sign` — people who used RapidAPI first often port
  later.
- npm registry — `tiktok-signature`, `x-argus`, `mssdk`.
- PyPI — same search.

Key discriminators when evaluating a candidate:

1. **Does it support `aid=1340` (Lite)?** Most public ports target
   `aid=1233` (full TikTok). Lite uses a different `lanusk`. If the
   port hardcodes a lanusk, check whether it's plug-and-play for a
   different aid or if it needs the Lite-specific key.
2. **Does it handle Argus v5.x?** Our capture shows
   `mssdk_version = v05.01.05-alpha.5-ov-android`. Older ports target
   v04 or earlier — those won't work.
3. **Does it run standalone** (no JNI/Native calls), or is it a thin
   wrapper around a proprietary `.so`? Wrappers are fine if the `.so`
   they carry is included.

If the port works only for `aid=1233`, we have fallback paths:

- Register a device under `aid=1233` (full TikTok) instead and see if
  search returns the same results. Full TikTok has the same endpoints.
  Downside: needs a different device fingerprint + captured login.
- Use the port to sign `aid=1233` calls, but substitute the Lite
  device identity at the TikTok layer. Likely won't validate, but
  worth a 10-minute test.

### Step 2: if nothing public works, Frida-extract the lanusk

This is Option 2 in the previous session's discussion. Estimated 4-8
hours of focused work. Approach:

1. Use [reference_apk_patch_vm.md](../apk_patch_vm.md?) — the
   `x86_64 GCP VM 34.162.181.247` that already has apktool + keystore.
   We'd want a Waydroid/emulator there for Frida to attach to.
2. Install frida-server on the emulator (root required).
3. Script: hook whatever function in `libtiktok.so` takes the lanusk
   as an argument at MSSDK init. Common entry points:
   `_Z.*MSSDK.*init`, or any Java_com_bytedance_mobsec symbol that
   looks like init.
4. Dump the lanusk string (≈60 chars, base64-ish, starts with `#`).
5. Hardcode it in a minimal Python Argus implementation (they're
   generally a few hundred lines of crypto — AES-GCM + protobuf).

If this route is needed, Claude Agent SDK-style pair programming works
well; the OSS Argus implementations on GitHub are a good starting
template even if we can't run them as-is.

### Step 3: wire the signer into `scrape_keyword.py`

The scraper's only coupling to RapidAPI is in `call_signer()` in both
`replay_search.py` and `scrape_keyword.py`. Drop-in replacement:

```python
def call_signer(query: str) -> dict:
    # Return {"X-Argus": ..., "X-Ladon": ..., "X-Gorgon": ..., "X-Khronos": ...}
    ...
```

Everything else — query building, TikTok fetch, pagination loop, stop
conditions — is already done.

### Step 4: end-to-end on "mario"

Run `python3 scrape_keyword.py mario` and confirm:
- Page 0 returns 10 videos, top like counts ≈ millions.
- Page 1+ returns more videos at declining like counts.
- Loop stops when a page has all videos <1000 likes (default
  `LIKE_FLOOR`) or server says `has_more=0`.

### Step 5: plumb into the existing pipeline

Once the signer works, retire the phone/Waydroid path and replace
`mobile_scrape.py` with a worker that calls `scrape_keyword` per term.
Keep the PostgreSQL queue + db.py — those layers don't change.

## File map (new in this workstream)

| File | Purpose |
|---|---|
| `tt_capture_signed.py` | mitmproxy addon. Writes each TikTok request/response as one JSON line to `/tmp/tt_signed_capture.jsonl`. Bugfix from earlier: use `resp.raw_content` instead of `resp.content` so `ttzip`-encoded responses don't crash the addon. |
| `captured_search_likes.json` | The target request — `/aweme/v1/search/item/?cursor=0&sort_type=1&...&keyword=mario`. 100% complete including request headers (X-Argus/X-Ladon/X-Gorgon/X-Khronos) and response (status 200 + aweme_list). |
| `captured_search_relevance.json` | Same device, relevance (default) sort, `/general/search/stream/`. Reference only. |
| `captured_mssdk.json` | 4 MSSDK protocol calls. Bodies are MSSDK-encrypted and unreadable without the lanusk; useful for reference if we Frida-extract. `license_id=224921550` is visible in query strings. |
| `captured_boston_paginated.jsonl` | **Proves pagination.** 9 consecutive `/aweme/v1/search/item/?sort_type=1` calls at cursor=0,10,20,...80, all HTTP 200, all with the same `search_id`. This is the canonical shape of a paginated like-sort scrape. |
| `captured_seattle_default.jsonl` | Pagination proof for the relevance (non-filtered) flow — different `search_source` (`switch_tab`) and no `sort_type` / `is_filter_search`. Not what we want but documents the difference. |
| `full_capture.jsonl` | 98-request first capture, kept for reference. |
| `replay_search.py` | Single-page replay. Signs via RapidAPI and GETs TikTok. Defaults: `count=30`, `sort_type=1`, `is_filter_search=1`, `search_source=tab_search`. |
| `scrape_keyword.py` | **Full paginator.** Loops cursor 0→10→20→... carrying the search_id from page 0's `x-tt-logid` response header. Stops on empty response, `has_more=0`, or all-below-floor-likes. Ready to run once we have a signer. |

## Protocol reference — exactly what the phone sends

All facts below are directly verified from captures in this repo.

### Endpoint (constant across pages)

```
GET https://api19-normal-useast8.tiktokv.us/aweme/v1/search/item/?<query>
```

### Query parameters — sort-by-likes flow

Constants (carry unchanged across every page):
```
sort_type          = 1
is_filter_search   = 1
search_source      = tab_search
source             = video_search
enter_from         = homepage_hot
request_tag_from   = h5
count              = 10   (phone uses 10; we've verified 30 works too)
query_correct_type = 0
```

Per-page:
```
cursor    = 0, 10, 20, 30, 40, 50, 60, 70, 80, ...
keyword   = <search term>
_rticket  = current millis
ts        = current seconds
search_id = ""                 on page 0
          = <x-tt-logid from page 0 response>   on page 1+
                               (held constant across ALL subsequent pages)
```

Plus ~35 device-fingerprint params, all verbatim from the captured phone
(device_id, iid, openudid, cdid, etc. — see `replay_search.py:DEVICE`).

Parameter ORDER matters for the signature. `scrape_keyword.py:SEARCH_PARAM_ORDER`
has the order from the boston capture's cursor=10 request; it's the right
order for every page.

### Required request headers

Authentication-adjacent:
```
x-argus     = <from signer, per request>
x-ladon     = <from signer, per request>
x-gorgon    = <from signer, per request>
x-khronos   = <from signer, per request (unix seconds)>
x-tt-token  = <login session token — see replay_search.py>
cookie      = <login session cookies — see replay_search.py>
user-agent  = "com.tiktok.lite.go/430553 (...)"
```

Device-context-y (mostly static, copy from replay_search.py):
```
x-ss-dp             = 1340
x-tt-request-tag    = n=0
x-tt-dm-status      = login=1;ct=1;
x-tt-store-region   = us
x-tt-ultra-lite     = 1
accept-encoding     = gzip
```

### Response shape (abridged)

```
Content-Encoding: gzip
X-Tt-Logid: 20260424055257242D07FB2BEE8809FD95   <-- becomes the search_id for next page

{
  "aweme_list":    [ ...videos... ],
  "has_more":      1,          // 0 means stop
  "cursor":        <int>,      // server's suggested next cursor
  "status_code":   0,
  "extra": {
    "now": <unix millis>,
    "logid": "...",            // same as X-Tt-Logid header
    ...
  }
}
```

Each aweme has:
```
aweme_id, desc, create_time, author.{unique_id, nickname, ...},
statistics.{digg_count, play_count, share_count, comment_count, ...},
video.{download_addr, play_addr, ...}
```

### Device identity (cached from the phone — safe to reuse)

See `replay_search.py:DEVICE` and `replay_search.py:MSSDK`. Don't
regenerate — the device is logged in (`x-tt-dm-status: login=1`) and
the session survives until TikTok invalidates the token (weeks–months).

## Things that wasted time in recent sessions (don't repeat)

1. **The MSSDK lanusk blocker is GONE.** RapidAPI accepts
   `mssdk_app_id=1340 / mssdk_license_id=224921550` without us passing
   a lanusk — the service has the right key internally. Prior
   session's "lives inside libtiktok.so, needs Frida" is still true
   for *our own* signer but irrelevant to RapidAPI. This was the main
   drag on the previous session.

2. **The `ttzip` addon crash silently lost an entire capture session.**
   mitmproxy's default `flow.response.content` accessor raises on
   `Content-Encoding: ttzip`. If an uncaught exception escapes, the
   addon's `response()` hook never writes the record to disk. FIX
   landed in `tt_capture_signed.py` — use `raw_content` and wrap the
   handler in try/except. If a future capture suddenly "has no search
   requests," grep `mitmdump.log` for `Addon error` first.

3. **The sort-by-likes filter DOES paginate.** Earlier guess (based
   on `/search/item/?cursor=10&sort_type=1` returning empty) was
   wrong. The correct protocol requires carrying `search_id` from
   page 0's `x-tt-logid` response header into pages 1+. A replay with
   `search_id=""` on page 1 will get an empty result and look like
   pagination doesn't work — it does.

4. **`/general/search/single/` is not part of the Lite flow.**
   CLAUDE.md documents full TikTok. Don't waste time capturing it for
   Lite.

5. **Tried `count=50` — server returns empty.** Ceiling is `count=30`.
   Don't try again.

## Recapture (if runtime state is gone)

Phone plugged in via USB + usbipd attached to WSL. mitmproxy CA cert
already trusted on the phone (installed long ago; survives reboots).

```bash
cd /home/james/tiktok-scraper/direct_api
pgrep -af mitmdump || nohup mitmdump --listen-port 8080 -s tt_capture_signed.py > /tmp/mitmdump.log 2>&1 &
adb reverse tcp:8080 tcp:8080
adb shell settings put global http_proxy "127.0.0.1:8080"
: > /tmp/tt_signed_capture.jsonl
# --- on the phone ---
# open TikTok Lite, do whatever flow you want to capture, scroll a few times
# watch live:
grep -oE '/aweme/v1/[a-z/]+' /tmp/tt_signed_capture.jsonl | sort | uniq -c
# when done:
adb shell settings put global http_proxy :0
adb reverse --remove tcp:8080
pkill -f mitmdump
```

## RapidAPI reference (for if we ever do upgrade the plan)

```
Base URL:  https://bytedance-services.p.rapidapi.com
Auth:      x-rapidapi-host: bytedance-services.p.rapidapi.com
           x-rapidapi-key:  20dc87029dmsh5df77ebff84ece1p1c5bf2jsn86c8c4d561b6
Sign:      POST /mssdk_common/sign
             body: {method, query, mssdk_app_id=1340, mssdk_license_id=224921550,
                    mssdk_version=v05.01.05-alpha.5-ov-android,
                    mssdk_version_int=83952928, device_id, device_type,
                    channel, os_version, version_name, x_ss_stub}
             returns: {X-Argus, X-Ladon, X-Gorgon, X-Khronos}
BASIC:     20 requests / day (daily reset)
           500,000 / month hard cap (monthly reset)
```

## Quick commands

```bash
# Dry-run the replay (needs RapidAPI quota)
cd /home/james/tiktok-scraper/direct_api
python3 replay_search.py <keyword> [--cursor N] [--count 10|30]

# Full paginator (needs signer — currently plumbed to RapidAPI)
python3 scrape_keyword.py <keyword> [--floor 1000] [--max-pages 100] [--out file.jsonl]

# Inspect any capture
python3 -c "
import json
with open('captured_boston_paginated.jsonl') as f:
  for line in f:
    d = json.loads(line)
    if 'search/item' in d.get('path',''):
      q = d['query']
      print(q.get('cursor'), q.get('search_id')[:12] if q.get('search_id') else '(empty)')
"

# Probe RapidAPI quota
python3 -c "
import urllib.request, urllib.error, json
body=json.dumps({'method':'GET','query':'ts=1','x_ss_stub':'0'*32,
  'mssdk_app_id':1340,'mssdk_license_id':'224921550',
  'mssdk_version':'v05.01.05-alpha.5-ov-android','mssdk_version_int':83952928,
  'device_id':'1','device_type':'x','channel':'g','os_version':'14','version_name':'1'}).encode()
req=urllib.request.Request('https://bytedance-services.p.rapidapi.com/mssdk_common/sign',data=body,method='POST',
  headers={'content-type':'application/json','x-rapidapi-key':'20dc87029dmsh5df77ebff84ece1p1c5bf2jsn86c8c4d561b6','x-rapidapi-host':'bytedance-services.p.rapidapi.com'})
try:
  with urllib.request.urlopen(req,timeout=20) as r: print('OK', r.status)
except urllib.error.HTTPError as e: print(e.code, dict(e.headers).get('X-RateLimit-Requests-Remaining'), '/', dict(e.headers).get('X-RateLimit-Requests-Limit'), 'reset in', dict(e.headers).get('X-RateLimit-Requests-Reset'),'s')
"
```

## Cleanup (when ditching direct_api entirely)

```bash
adb shell settings put global http_proxy :0
adb reverse --remove tcp:8080
pkill -f mitmdump
```
