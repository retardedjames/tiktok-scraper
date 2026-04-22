# TikTok Scraper

Scrapes TikTok's mobile API for all-time most-liked videos by keyword using
mitmproxy interception on Waydroid Android emulators running on GCP VMs.

## Architecture

- **GCP VMs** (one or more, identical setup): each runs Waydroid + TikTok Lite + mitmproxy + scraper
- **mitmproxy** (runs ON the GCP VM): intercepts `/aweme/v1/general/search/stream/` (cursor=0, first page) and `/aweme/v1/general/search/single/` (cursor=10, 20, … paginated). See `tt_dump.py`.
- **Oracle VPS** (`150.136.40.239`): PostgreSQL database (`tiktoks`) — shared across all VMs
- **Queue**: PostgreSQL `terms` table on Oracle VPS — `FOR UPDATE SKIP LOCKED` ensures no two VMs grab the same term
- **ADB**: local on each VM (`ADB_DEVICE=127.0.0.1:5556`, socat proxy into the Waydroid container), set via `.env`

The scraper runs entirely on the GCP VM. WSL2 is only used for monitoring/debugging.

## Key Files

| File | Purpose |
|---|---|
| `scrape_forever.py` | **Production entry point** — runs continuously, 1 term at a time, stops + writes SCRAPER_STATUS.txt on 2 consecutive failures |
| `batch_scrape.py` | Test/dev entry point — pulls N terms and stops |
| `mobile_scrape.py` | Core scraper: mitmproxy, ADB automation, scroll logic |
| `tt_dump.py` | mitmproxy addon — parses captured search responses (incl. chunked cursor=0 stream) and writes `/tmp/tt_<keyword>_<sort>.jsonl` |
| `queue.py` | PostgreSQL queue — `grab_batch` uses `FOR UPDATE SKIP LOCKED` |
| `db.py` | PostgreSQL models and upserts |
| `preflight.py` | Pre-run checks (ADB connected, mitmproxy available, proxy clear) |
| `run.sh` | Shell wrapper: loads `.env`, `git fetch && git reset --hard origin/main`, then runs `scrape_forever.py` |
| `.env.example` | Template — copy to `.env` on each VM, set `ADB_DEVICE` |
| `search_terms.txt` | ~3,524 search terms across 35+ categories |
| `waydroid-start.sh` | Full Waydroid stack startup (lives at `/usr/local/bin/` on each GCP VM) |
| `WAYDROID_GCP2_SETUP.md` | Complete VM provisioning + daily operation guide |
| `HANDOFF.md` | Session handoff notes |
| `SCRAPER_STATUS.txt` | Written by `scrape_forever.py` when it stops due to failures — check this |

## Running on a GCP VM

```bash
# One-time: copy and edit .env
cp .env.example .env
nano .env   # set ADB_DEVICE=127.0.0.1:5556

# Production (continuous, auto-restart via run.sh)
bash run.sh

# Test run (N terms then stop)
python3 batch_scrape.py --n 3

# Queue management (run from anywhere with DB access)
python3 queue.py stats
python3 queue.py reset        # unstick crashed in_progress terms
python3 queue.py reset-all    # reset ALL terms → pending (fresh start)
python3 queue.py import search_terms.txt search  # add new terms
```

## Infrastructure

- SSH to a GCP VM: `ssh -i ~/.ssh/jamescvermont jamescvermont@<VM_IP>`
- VNC: `<VM_IP>:5900` (no password) — for monitoring only, not required
- DB: `PGPASSWORD=app1dev psql -U app1_user -h 150.136.40.239 -d tiktoks`

## Current GCP VMs

| Name | IP | Status |
|---|---|---|
| JC1 | `34.30.234.222` | Active |

SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<IP>`

## Monitoring a Running Scraper

```bash
# On the GCP VM — tail live output
tail -f /tmp/scraper.log     # if started with: bash run.sh >> /tmp/scraper.log 2>&1

# Check if it stopped due to failures
cat ~/tiktok-scraper/SCRAPER_STATUS.txt

# Queue state (from anywhere)
python3 queue.py stats
```

## Workflow

- After making any code changes, commit and push without asking. The GCP VMs pull from `main` via `run.sh`, so unpushed changes never reach production.

## Important Quirks

- mitmproxy CA cert is bind-mounted as a system cert on every Waydroid boot
  (`waydroid-start.sh` handles this automatically)
- ADB text input typed character-by-character (0.15s delay) — Waydroid drops chars if sent all at once
- TikTok launches via `am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity`
- Foreground app detected via `dumpsys window | grep mCurrentFocus` — NOT `dumpsys activity top` (that returns background entries)
- `scrape_forever.py` writes to `SCRAPER_STATUS.txt` on stop — check that file if the scraper appears to have died
- `scroll_smart` always scrolls to `max_scrolls` (default 33); only stops early if TikTok stops returning new content; like-count threshold removed because TikTok's sort is imprecise
- Cursor=0 (first page of ~10 top-liked videos) is served by `/search/stream/` with HTTP chunked transfer encoding and two concatenated JSON docs in one body — `tt_dump.py:_dechunk` + `_parse_json_docs` handle both. Paginated pages (cursor=10, 20, …) come via `/search/single/` as normal JSON.
- Keywords can contain `/` (e.g. `1/144 scale`) — all `/tmp/tt_<keyword>_…jsonl` paths go through `safe_keyword()` (in both `mobile_scrape.py` and `tt_dump.py`; the two must stay in sync) to avoid `FileNotFoundError`
