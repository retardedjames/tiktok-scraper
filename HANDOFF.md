# Handoff — TikTok Scraper Session 2026-04-20 (late evening)

## TL;DR — Where We Are

**Four bugs fixed and pushed today.** Scraper is running on GCP2 as of mid-evening.
Current production state unknown — check before assuming it's still running.

All fixes are in `main` (latest commit `b71ef23`). GCP VMs auto-pull via
`run.sh` (`git fetch && git reset --hard origin/main`) on next restart.

## What Was Broken and What Was Fixed

### 1. Cursor=0 first page was never being captured (multi-layer bug)

Three stacked bugs each hid the next one. Root-cause summary:

| Bug | Fix | Commit |
|---|---|---|
| `mobile_scrape.py` shipped an embedded copy of `tt_dump.py` (`DUMP_SCRIPT_SRC`) and overwrote the on-disk file with that stale copy at every scraper start. Explains why the prior session's `tt_dump.py` edits appeared to have no effect. | Removed embedded copy; `start_mitmproxy` now just verifies `tt_dump.py` exists. | `eb5d1fa` |
| `tt_dump.py` did `gzip.decompress(flow.response.content)` — but mitmproxy already decodes `Content-Encoding`. Every response failed to parse as JSON silently. | Removed manual decompression; dump raw body on parse failure. | `d7db2c2` |
| `/search/stream/` (cursor=0 endpoint) returns HTTP chunked transfer encoding + two concatenated JSON docs in one body. mitmproxy does NOT strip chunked framing; `json.loads` only reads one doc. | `_dechunk()` strips framing. `_parse_json_docs()` uses `JSONDecoder.raw_decode` in a loop. | `62ac300` |

Verified locally: "spoonie" cursor=0 now yields 10 videos with top likes 2.8M, 2.3M, 2.2M, 1.8M — previously lost. Total saved jumped from ~75 to ~85 on a typical term.

### 2. Slash in keyword crashed file writes

Keywords like `1/144 scale` → `/tmp/tt_1/144 scale_1.jsonl` → `FileNotFoundError`.
Also caused a cascade: after `1/144 scale` crashed mid-UI, TikTok was left in a
weird state, so the next term (`backrooms`) captured zero → 2-consecutive-empty
circuit breaker fired.

Fix: `safe_keyword()` helper in **both** `mobile_scrape.py` and `tt_dump.py`
(they must match exactly). Applied everywhere `/tmp/tt_<keyword>_<sort>.jsonl`
is constructed. Commit `0a6909f`.

### 3. 0-save terms were being marked `done` and never retried

[scrape_forever.py](scrape_forever.py) was calling `qmod.mark_done(term_id, videos_saved=0)`
for terms that returned 0 videos. Those terms were retired permanently even
when the 0 was transient (UI glitch, cascade from a prior crash, etc.).

Fix: added `mark_retry(term_id)` to [queue.py](queue.py) that resets the term
to `status='pending', started_at=NULL, completed_at=NULL`. Both
`scrape_forever.py` and `batch_scrape.py` now call `mark_retry` when `saved==0`
and `mark_done` only when `saved>0`. Commit `b71ef23`.

The 2-consecutive-empty circuit breaker still stops the scraper after two
consecutive 0-save runs, so a persistently broken term won't retry-loop forever
— the scraper halts and writes `SCRAPER_STATUS.txt`.

## Outstanding: Historical 0-Save Terms

Terms already marked `done` with `videos_saved = 0` (or NULL) before the fix
won't be retroactively retried. To surface them:

```sql
SELECT term, completed_at FROM terms
WHERE status='done' AND (videos_saved IS NULL OR videos_saved = 0)
ORDER BY completed_at DESC;
```

User hasn't decided whether to reset those yet. If they want them retried:

```sql
UPDATE terms
SET status='pending', started_at=NULL, completed_at=NULL, videos_saved=NULL
WHERE status='done' AND (videos_saved IS NULL OR videos_saved = 0);
```

Run this only with user confirmation.

## Current GCP2 State

- IP: `34.153.25.251`
- Scraper: restarted earlier this evening after all 4 fixes pushed. Verify
  before assuming it's still running:
  ```bash
  ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 \
    "tail -30 /tmp/scraper.log && echo --- && cat ~/tiktok-scraper/SCRAPER_STATUS.txt | tail -20"
  ```
- Last observed run: Path of Exile → 46 saved, then numerology → 0 saved,
  then circuit breaker fired (that was the stop that prompted the `mark_retry`
  fix). With `mark_retry` in place, numerology should now re-enter the pool on
  next run.

## Doc Changes (Also in b71ef23)

- [CLAUDE.md](CLAUDE.md) — added `tt_dump.py` to Key Files, fixed ADB port 5555→5556,
  added cursor=0 + slash-keyword quirks, added **Workflow** section that says
  "always commit+push after any change, no confirmation."
- [CLONE_SETUP.md](CLONE_SETUP.md) — one architecture line mentions both
  `/search/stream/` and `/search/single/`. Rest of the file has known-stale
  sections (see below).

## What I Did NOT Finish

CLONE_SETUP.md still has stale content I began editing but didn't complete:

1. **Line ~272**: describes `run.sh` as `git pull --ff-only` — actually
   `git fetch && git reset --hard origin/main` (commit `cdca963`).
2. **Lines ~336-337**: describes `scroll_smart` as stopping when ≥3-of-10
   videos have <5000 likes — that logic was removed in `3a12e38`. Now always
   scrolls to `max_scrolls` (default 33) unless TikTok stops returning new content.
3. **Line ~338** ("How the Scraper Works" step 6): says mitmproxy intercepts
   `/search/single/` only — should also mention `/search/stream/` for cursor=0.
4. **Lines ~389-391**: says `clear_proxy()` runs after each term — actually
   runs once at shutdown (in the `finally:` block of both runners).
5. **Line ~329** ("How the Scraper Works" step 1): says the scraper "writes
   tt_dump.py to disk" — no longer true as of `eb5d1fa`. The file is on disk
   and under version control; scraper only verifies it exists.
6. **"Key Files" table** (line ~308): missing `tt_dump.py` row.
7. **"Critical Quirks"**: could use the cursor=0 chunked + slash-keyword
   quirks, matching what's already in CLAUDE.md.

The user told me "(only if needed)" for doc rewrites, so these are genuine
staleness fixes, not stylistic rewrites. Safe to proceed without asking.

## Key Files to Read First

- [tt_dump.py](tt_dump.py) — mitmproxy addon; `_dechunk` and `_parse_json_docs`
- [mobile_scrape.py](mobile_scrape.py) — `safe_keyword`, `start_mitmproxy` (no
  longer writes the addon), `scroll_smart` (always 33 scrolls)
- [scrape_forever.py:162-170](scrape_forever.py#L162-L170) — new 0-save retry branch
- [queue.py:139-170](queue.py#L139-L170) — `mark_failed`, new `mark_retry`
- [CLAUDE.md](CLAUDE.md) — project-wide notes (kept current)

## Infrastructure (unchanged)

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | Scraping (verify) |
| GCP3 | `34.59.191.130` | Pending — clean install, not yet provisioned |
| GCP4 | TBD | Not yet cloned |

- Oracle VPS: `150.136.40.239` — PostgreSQL `tiktoks` DB, user `app1_user`, pw `app1dev`
- Queue: `terms` table, check `python3 queue.py stats`
- ntfy topic: `retardedjames-tiktok` (VM_NAME-tagged, NTFY_PER_TERM=1 on GCP2)
- SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<IP>`

## Workflow Preferences (from CLAUDE.md)

- **Always commit+push after any code change** — no confirmation. Production
  VMs pull from `main` via `run.sh`; unpushed changes never reach production.
- User is on Opus and closes sessions aggressively to manage cost — expect
  frequent context handoffs via this file.
