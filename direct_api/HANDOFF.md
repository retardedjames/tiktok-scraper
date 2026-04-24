# direct_api handoff

Session goal: replace the phone+mitmproxy+ADB scraper with pure HTTP calls to
TikTok's mobile search API, using RapidAPI's `bytedance-services` as a signer.

## Status at end of last session

**Replay works end-to-end on `/aweme/v1/search/item/` (cursor=0).** The
RapidAPI MCP signer accepts `aid=1340 / license_id=224921550` and returns
valid X-Argus / X-Ladon / X-Gorgon / X-Khronos — the lanusk is handled
server-side. The `lanusk` blocker from the prior session is resolved: we do
NOT need to extract it, RapidAPI has the matching key baked in.

Verified working:
- `python3 replay_search.py mario` → HTTP 200, 675 KB, 10 aweme,
  first result 4.6M likes / 24.8M plays
- `python3 replay_search.py "kawaii desk"` → HTTP 200, 632 KB, 10 aweme
  (confirms it's not a replay-cache artefact)

**Per-keyword ceiling on `/search/item/`: exactly 30 videos.**
Tested `count=10,20,30,35,40,45,50`: `count≤30` returns that many; `count≥35`
returns `aweme_list: null`. `cursor=10/20/30` all return empty with an
advancing `cursor:` hint — the cursor field is cosmetic on this path.

So `/aweme/v1/search/item/` = top-30 ranker only. For deeper scraping we
still need the phone-app paginated path `/aweme/v1/general/search/single/`
(see CLAUDE.md). We captured only `/search/item/`; the other path's exact
query shape was never recorded on this device. Next session must recapture
on the phone while scrolling to get `/general/search/single/?cursor=30&...`.

Note: 30 per keyword × 3,524 keywords = ~106k videos with zero phone/Waydroid
involvement. That's already a viable tier-1 corpus if pagination proves
harder than expected.

Runtime state that may still be live when you return:

- mitmdump still running: pid 5284, listening on `0.0.0.0:8080`, addon
  `direct_api/tt_capture_signed.py`, writing to `/tmp/tt_signed_capture.jsonl`
- `adb reverse tcp:8080 tcp:8080` still set on phone `ZL8326JB79`
- Phone `http_proxy` still set to `127.0.0.1:8080`
- Device is logged into TikTok on this capture (`x-tt-dm-status: login=1`)

If those are stale and you want to recapture, see "Recapture" below.

## What lives where

| File | What it is |
|---|---|
| `tt_capture_signed.py` | mitmproxy addon — dumps every TikTok request (url, query, headers, body) to `/tmp/tt_signed_capture.jsonl`. Used for the capture that produced the files below. |
| `captured_search_likes.json` | **The target request.** `GET /aweme/v1/search/item/?cursor=0&sort_type=1&keyword=mario&...` — the exact sort-by-likes request we want to replay. Includes all request headers (X-Argus/X-Ladon/X-Gorgon/X-Khronos) + response (status 200). |
| `captured_search_relevance.json` | Same device, relevance sort, `/aweme/v1/general/search/stream/` path. For reference. |
| `captured_mssdk.json` | Four MSSDK calls (`/ms/dyn/task`, `/ms/get_seed`, `/sdi/get_token`, `/ri/report`) on `mssdk16-normal-useast5.tiktokv.us`. Bodies are encrypted protobuf — unreadable without the lanusk — but the **query strings contain `lc_id=224921550`** which is our license_id. |
| `full_capture.jsonl` | All 98 TikTok requests from the session — auth, feed, analytics, CDN, etc. Keep for reference. |

## Device identity (extracted from captures — reuse these verbatim)

```
aid              = 1340                       (TikTok Lite / musically_go)
app_name         = musically_go
version_code     = 430553
version_name     = 43.5.53
device_id        = 7630963143929628173
iid              = 7631123284638189325
openudid         = 28dcd4741ad9b0e6
cdid             = 56f0a19c-154f-487b-949b-ad6a7464c06d
device_brand     = motorola
device_type      = moto g power - 2025
os               = android
os_version       = 16
os_api           = 36
resolution       = 1080*2226
dpi              = 330
channel          = googleplay
region           = GB
sys_region       = US
op_region        = US
timezone_name    = America/New_York
timezone_offset  = -18000
locale           = en-GB
app_language     = en
ac / ac2         = wifi
host_abi         = arm64-v8a
build_number     = 43.5.53
ssmix            = a
```

MSSDK sub-SDK:
```
license_id (lc_id)   = 224921550
sdk_ver              = v05.01.05-alpha.5-ov-android
sdk_ver_code         = 83952928
client_type          = inhouse
region_type          = ov
mode                 = 2
```

User-Agent: `com.tiktok.lite.go/430553 (Linux; U; Android 16; en_US; moto g power - 2025; Build/W1VE36H.10-12-9-8;tt-ok/3.12.13.51.lite-ul)`

Cookie (from capture, login-bound — may need refresh):
```
store-idc=useast5; store-country-code=us; install_id=7631123284638189325;
ttreq=1$c79d0dd3442f7d7d04daf3e7457da7e036700c43; d_ticket=2e8cd293a152e6a3736cdd4e5009d5800cafd;
odin_tt=bb932ab14417cebea801...  [truncated — full value in captured_search_likes.json req_headers.cookie]
```

`x-tt-token` is present and non-empty in the logged-in capture; unclear if
search requires it.

## The target request in full

`captured_search_likes.json` is the canonical replay target. URL structure:

```
GET https://api19-normal-useast8.tiktokv.us/aweme/v1/search/item/?
    cursor=0
    sort_type=1                     # 1 = sort by likes
    enter_from=homepage_hot
    count=10
    source=video_search
    keyword=<TARGET>                # <-- the only thing we'd vary
    query_correct_type=0
    is_filter_search=1
    search_source=normal_search
    + all 50 device/version params above
    + _rticket=<ms since epoch>
    + ts=<sec since epoch>
```

Pagination: subsequent pages are `cursor=10`, `20`, ... on the same path.

## Known unknowns (the real blockers)

### 1. `lanusk` — the MSSDK signing key

The RapidAPI signer endpoint `/mssdk_common/sign` requires a `lanusk` param
(e.g. `#HIYHgj5hkXejNc1+PKP2Q2Dhry8D7aqRDMOfkhQq9Y4U8x+WWpoM0YwPvBR9x4laXnn2EUdQp0QkgG40`).

Where it is **not**:
- Not in query strings (checked)
- Not in plaintext in `libtiktok.so` (checked via `strings` — libtiktok.so is
  a packed ELF, `file` reports "data", so static grep finds nothing)
- Not in MSSDK request bodies on the wire (they are encrypted *using* the
  lanusk, but don't contain it)

Where it might be:
- Compiled into `libtiktok.so` (packed), decrypted at runtime into memory.
  **Extractable via Frida** — hook MSSDK init function, dump arg. ~2-3 hrs.
- Potentially provided by RapidAPI if you ask — their signer for `aid=1340`
  must have the matching lanusk internally. The `2142840551` license_id in
  their public sample is for `aid=1233` (full TikTok), not Lite.

**First thing to try in the new session:** with the MCP server loaded, ask the
signer what it accepts for `aid=1340 / license_id=224921550`. The MCP tools
may be more forthcoming about error cases than curl.

### 2. Login requirement for search

This capture was from a logged-in session. Unknown whether TikTok's
`/search/item/` works without `x-tt-token` + login cookies. If yes: perfect,
the replay just needs the 4 signed headers + device params. If no: either
capture a logged-out session (sign out on phone, re-run the capture) or find
a way to mint a guest token.

### 3. `x-ss-stub` / `x-ss-dp` / `x-tt-request-tag` / `x-tt-token`

The capture shows these additional headers. `x-ss-stub` is an MD5 over the
request body (irrelevant for GET search). `x-ss-dp` is just `1340` (= aid).
`x-tt-request-tag` is probably stateless (`n=0`). `x-tt-token` is the login
session. Worth attempting the replay without all of these first, then adding
them back if TikTok rejects.

## RapidAPI signer — what's known

Base URL: `https://bytedance-services.p.rapidapi.com`

Auth headers:
```
x-rapidapi-host: bytedance-services.p.rapidapi.com
x-rapidapi-key:  20dc87029dmsh5df77ebff84ece1p1c5bf2jsn86c8c4d561b6
```

Endpoints observed to exist:

| Path | Purpose |
|---|---|
| `POST /mssdk_common/sign` | Returns `{X-Argus, X-Ladon, X-Gorgon, X-Khronos}` for a given query + license + lanusk. **This is the one we need.** |
| `POST /cylons/encrypt` | Returns `{X-Cylons}` — passport/login flow, probably not needed for search |
| `POST /risk_report/generate_field220` | Returns risk payload `{data: [int]}` — unclear if search needs this |
| `POST /ladon/encrypt`, `/ladon/decrypt` | Primitives |

Full list per the UI screenshot: Generate Risk Report Field 66, Generate
X-Cylons, Generate Signature, Encrypt (MSSDK / TTEncrypt / Report String /
Ladon), Decrypt (MSSDK / TT / Ladon / Argus / Report String). **No
device-registration endpoint** is offered by this service — device_id/iid/
openudid must come from elsewhere (we already have a set from the phone).

Sample body for `/mssdk_common/sign` (from user's earlier message — note
this is for `aid=1233` / full TikTok, not Lite):
```json
{
  "method": "POST",
  "query": "aid=1233&device_id=...&iid=...&[full query string]",
  "session_id": "cec776cfea464c1ea371235e57d99bb3",
  "payload": "account_sdk_source=app&...",
  "lanusk": "#HIYHgj5hkXejNc1+PKP2Q2Dhry8D7aqRDMOfkhQq9Y4U8x+WWpoM0YwPvBR9x4laXnn2EUdQp0QkgG40",
  "lanusv": "0",
  "device_token": "AwJPA542UkWNXRexuZLdUsNI",
  "seed_token": "MDGlG5zbpHMEIDZ8yWtixIC3meNySiJyATd99/hdHk3RLBaLRzY5QQN6SEqzNQSm2ekGJAHBxxeTuQ7OUKf9BlPBkjDdVuNRLohnmkUXvzQx/sVGFruxo3FPNn2OutWaBvE=",
  "seed_algorithm": 5,
  "version_name": "34.0.1",
  "channel": "googleplay",
  "os_version": "14",
  "device_id": "7336346932518127136",
  "device_type": "sdk_gphone64_x86_64",
  "mssdk_version": "v05.00.06-ov-android",
  "mssdk_version_int": 83887648,
  "mssdk_app_id": 1233,
  "mssdk_license_id": "2142840551"
}
```

Real response it returned:
```json
{
  "X-Argus": "hwUxXBxt...",
  "X-Ladon": "Z3bNGNcVSX...",
  "X-Gorgon": "8404f0d40000b4ee...",
  "X-Khronos": "1777007381"
}
```

So the endpoint works. The open question is whether it accepts our TikTok
Lite `aid=1340 / license_id=224921550` values without us providing a valid
matching `lanusk` / `device_token` / `seed_token`.

`device_token` and `seed_token` are probably also required. We **may** be
able to extract them by decrypting one of the captured MSSDK response bodies
(`/sdi/get_token`, `/ms/get_seed`) using RapidAPI's `/mssdk_common/decrypt`
endpoint — circular but potentially viable.

## RapidAPI MCP server

Added to user config this session:
```bash
claude mcp list | grep rapidapi_bytedance
# rapidapi_bytedance: npx mcp-remote https://mcp.rapidapi.com ... - ✓ Connected
```

**The MCP tools are not loaded into the session that added them** — they only
appear in a new session. When you start fresh, you should see
`mcp__rapidapi_bytedance__*` tools available. Use them to probe the signer
endpoints properly instead of blind curl.

## First things to do in the new session

1. **Recapture with scrolls** to get the paginated endpoint. Start the
   mitmdump + `adb reverse` + proxy as in "Recapture" below, then open
   TikTok, search "mario", sort by Like count, **scroll 4-5 times**. We
   already have the cursor=0 call; we specifically need cursor=10, 20 via
   `/aweme/v1/general/search/single/`. Grep the capture for
   `general/search/single` to confirm it landed.
2. **Mirror `replay_search.py` into `replay_search_page.py`** (or merge):
   same signing flow, but target
   `https://api19-normal-useast8.tiktokv.us/aweme/v1/general/search/single/`
   with the captured param order for that path. Key extra params to watch
   for vs `/search/item/`: `search_id` becomes non-empty after page 0, and
   there's usually a `search_type`/`from_group_id`/`last_search_id` hint.
2. **Keyword sweep**: once pagination works, loop over the 3,524 terms in
   `../search_terms.txt`, upsert into the existing Postgres schema on Oracle
   VPS, and retire the Waydroid+ADB stack.

## Reference: first-page-only replay (already working)

```bash
cd /home/james/tiktok-scraper/direct_api
python3 replay_search.py "<keyword>"          # cursor=0, 10 results
python3 replay_search.py "<keyword>" --cursor 10   # currently returns empty
```
Trace written to `/tmp/replay_<keyword>_<cursor>.json`.

## Recapture (if runtime state is gone)

Phone must still be plugged in via USB and have usbipd attached to WSL.
mitmproxy CA must already be installed on phone (it is — the existing
scraper relies on it).

```bash
cd /home/james/tiktok-scraper/direct_api
mitmdump --listen-port 8080 -s tt_capture_signed.py > /tmp/mitmdump.log 2>&1 &
adb reverse tcp:8080 tcp:8080
adb shell settings put global http_proxy "127.0.0.1:8080"
# do a search on the phone with sort_type=1 (filter → Like count)
# tail /tmp/tt_signed_capture.jsonl to confirm lines land
# when done:
adb shell settings put global http_proxy :0
```

## Cleanup (when direct_api work is abandoned or done)

```bash
adb shell settings put global http_proxy :0
adb reverse --remove tcp:8080
kill 5284  # or: pkill -f mitmdump
```

## Things I got wrong in the last session (so you don't retrace them)

- Claimed lanusk was "bakeable from APK" — only partially true. The
  license_id is findable (in MSSDK request query strings on the wire), the
  lanusk is not (packed in libtiktok.so).
- Initially missed the MSSDK traffic in the capture because the monitor
  printed request paths by URL prefix and I didn't filter for `mssdk` host.
  The `full_capture.jsonl` has always had it.
- Said "launching the app will leak license over the wire" — only the
  `license_id` leaks that way. `lanusk` does not.
