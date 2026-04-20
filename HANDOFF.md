# Handoff — TikTok Scraper Session 2026-04-20 (evening)

## Where We're Stuck: First-Page (cursor=0) Bug

**The problem.** TikTok's search API returns results in pages of ~10 videos.
mitmproxy captures pages at `cursor=10, 20, 30, …` but the very first page
(`cursor=0`) is NEVER captured. This consistently loses the top ~10
highest-liked videos for every search term.

**Concrete evidence.** For "Deadpool Wolverine" (run at ~19:45 UTC):
- 8 API captures in `/tmp/mitmdump.log`: cursors 10, 20, 30, 40, 50, 60, 70, 80
- 77 videos total captured, all 77 over 1k likes — so the data is otherwise clean
- `/tmp/mitmdump.log` shows **18** requests to `.../aweme/v1/search…` (truncated)
  but we only capture 8 responses → 10 unmatched requests, at least one of which
  is the cursor=0 page we want

**What we tried that didn't work:**
1. Added brotli decompression to `tt_dump.py` — no effect, no brotli failures logged
2. Added a `request` hook in `tt_dump.py` to log full URLs → **the hook never fired**
   (0 `[req]` lines despite plenty of requests going through mitmproxy). Unclear why —
   may need class-based addon, may be a mitmproxy version quirk.
3. Added a `[path]` print inside the `response` hook before any filtering → **never
   logged** despite the `[mitmproxy]` print later in the same function firing fine.
   Same code path, same hook, but the earlier print is missing. Strongly suggests
   mitmproxy is loading a **cached/stale version** of `tt_dump.py`.
4. Added a debug file write to `/tmp/tt_paths.log` → **file never gets created**.
   Confirms the early-in-function code isn't running.

**Current hypothesis.** mitmdump is running an outdated copy of `tt_dump.py` from
a `.pyc` cache, or from memory from a prior run. Verified repeatedly:
- `cat ~/tiktok-scraper/tt_dump.py` on GCP2 shows the **new** code on disk
- but mitmdump's log shows **only** the old behavior (no `[path]`, no `[req]`, no
  `/tmp/tt_paths.log` creation)
- and mitmdump's startup line logs: `Loading script /home/jamescvermont/tiktok-scraper/tt_dump.py`

**Secondary git-drift issue we hit.** On GCP2, `git pull` kept failing because
`tt_dump.py` had uncommitted local changes every time. Had to force `git reset
--hard origin/main`. Updated `run.sh` (commit cdca963) to use fetch + reset
instead of pull --ff-only. The working tree kept getting downgraded to an older
version between commands — root cause unknown.

## What to Try First (New Session)

1. **Clear Python cache on GCP2 before running mitmdump.** Delete
   `~/tiktok-scraper/__pycache__/` and any `.pyc` files. mitmproxy may be using
   a stale bytecode copy.
2. **Verify the addon is actually loaded.** Add an import-time `print()` at the top
   of `tt_dump.py` (e.g. `print("[tt_dump loaded vXX]")`). If that appears in
   `/tmp/mitmdump.log`, the file is loaded; if not, the loading itself is broken.
3. **Try class-based addon.** Rewrite `tt_dump.py` as a class with `request`/`response`
   methods (mitmproxy's canonical addon pattern) — the module-level `request`
   function hook never fired for us.
4. **Check mitmproxy version.** Run `mitmdump --version` on GCP2. If it's very old,
   the addon API may have diverged.
5. **Consider that cursor=0 may not be HTTP.** We saw WebSocket traffic to
   `frontier.tiktokv.us` in the log. It is possible the initial sort=1 results
   are pushed over WebSocket rather than fetched via HTTP/2, in which case mitmproxy's
   HTTP response hook will never fire for them. If so, either (a) write a WebSocket
   message handler, or (b) scroll back to the top of search results once to force a
   fresh HTTP fetch.

## What Also Changed This Session (Already Committed)

1. **`scroll_smart`: hard cap of 33 scrolls, no threshold early-stop.** TikTok's
   like-count sort is imprecise — high-liked videos appear scattered. Early-stop
   logic cut scrapes short. Now always runs 33 scrolls unless TikTok runs out of
   content. (`mobile_scrape.py`)
2. **33 scrolls ≠ 330 videos.** Each API call serves ~10 videos covering ~4 scrolls.
   33 scrolls yields 8–9 API calls ≈ 80 videos. For more, increase `max_scrolls`.
3. **DB insertion filter: `digg_count >= 1000`** (already in `scrape_forever.py`,
   unchanged).
4. **SIGTERM/SIGINT handler** in `scrape_forever.py` now logs external kills
   distinctly from failure stops in `SCRAPER_STATUS.txt`, plus an ntfy ping
   tagged as external stop.
5. **`NTFY_PER_TERM=1`** added to GCP2's `.env` — ntfy ping fires after every term.
6. **`run.sh` uses `git fetch && git reset --hard origin/main`** instead of
   `git pull --ff-only` to survive local-file drift on VMs.

## Current State of GCP2 (34.153.25.251)

- `scrape_forever.py` is **stopped** (manually killed during debugging)
- mitmdump is **stopped**
- Latest commit pulled: `7fd278f` (debug path logging — didn't help)
- `.env` includes `NTFY_PER_TERM=1`, `VM_NAME=GCP2`, `ADB_DEVICE=127.0.0.1:5556`
- ~8 test runs of "Deadpool Wolverine" in `/tmp/dw_test*.log` — all show 76–77
  videos captured, all confirming cursor=0 missing
- Queue: check `python3 queue.py stats` on GCP2 for current progress

## Key Files to Read First

- `tt_dump.py` — the mitmproxy addon; where the bug lives
- `mobile_scrape.py:305` — `scroll_smart` (new 33-scroll cap)
- `mobile_scrape.py:128` — `start_mitmproxy` (where the addon is loaded)
- `/tmp/mitmdump.log` on GCP2 — raw mitmproxy traffic log
- `/tmp/dw_test*.log` on GCP2 — last several test-run outputs for comparison

## GCP VM Fleet

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | **Stopped** — mid-debugging |
| GCP4 | TBD | Not yet cloned |

SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<IP>`

## Infrastructure (unchanged)

- Oracle VPS: `150.136.40.239` — PostgreSQL `tiktoks` DB, user `app1_user`, pw `app1dev`
- Queue: `terms` table, ~3,500 pending terms
- ntfy topic: `retardedjames-tiktok`
