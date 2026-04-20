# TikTok Scraper

Scrapes TikTok's mobile API for all-time most-liked videos by keyword using
mitmproxy interception on a Waydroid Android emulator (GCP2 VM).

## Architecture

- **GCP2 VM** (`34.153.25.251`): Waydroid running TikTok Lite, VNC on :5900, ADB on :5556
- **mitmproxy** (runs locally on WSL2): intercepts `/aweme/v1/general/search/single/`
- **Oracle VPS** (`150.136.40.239`): PostgreSQL database (`tiktoks`)
- **ADB UI automation**: searches TikTok, applies Like count sort, scrolls results

## Key Files

| File | Purpose |
|---|---|
| `batch_scrape.py` | Main entry point — pulls N terms from queue, scrapes in one session |
| `mobile_scrape.py` | Core scraper: mitmproxy, ADB automation, scroll logic |
| `queue.py` | SQLite queue for search terms |
| `db.py` | PostgreSQL models and upserts |
| `preflight.py` | Pre-run checks (ADB connected, mitmproxy available, proxy clear) |
| `search_terms.txt` | ~3,524 search terms across 35+ categories |
| `waydroid-start.sh` | Full Waydroid stack startup script (lives at `/usr/local/bin/` on GCP2) |
| `WAYDROID_GCP2_SETUP.md` | Complete setup guide — read this first |
| `HANDOFF.md` | Session handoff notes — current state and what was last fixed |

## Running

```bash
# Add terms to queue (first time)
python3 queue.py import search_terms.txt search

# Run batch scrape (smart auto-scroll, stops at <7k likes)
python3 batch_scrape.py --n 10

# Check queue
python3 queue.py stats
python3 queue.py reset   # unstick crashed in_progress terms
```

## Infrastructure

- SSH to GCP2: `ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251`
- ADB: `adb connect 34.153.25.251:5556`
- VNC: `34.153.25.251:5900` (no password)
- DB: `PGPASSWORD=app1dev psql -U app1_user -h 150.136.40.239 -d tiktoks`

## Important Quirks

- mitmproxy CA cert must be bind-mounted as a system cert on every Waydroid boot
  (`waydroid-start.sh` handles this automatically — see `WAYDROID_GCP2_SETUP.md`)
- ADB text input must be typed character-by-character (0.15s delay) on Waydroid
- TikTok launches via `am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity`
