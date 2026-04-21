# Phone / USB TikTok Scraper

Physical-phone variant of the scraper in the parent directory. Same database,
same queue, same mitmproxy-interception strategy — but driving a real Android
phone plugged into the PC via USB (usbipd → WSL2 → ADB).

Use this when you want to run against your own TikTok account on your own
phone instead of a headless Waydroid emulator on a GCP VM.

## What it does

1. Pulls a search term from the Oracle Postgres queue (`terms` table,
   `FOR UPDATE SKIP LOCKED` — safe alongside GCP scrapers).
2. Drives TikTok Lite on the phone with ADB: search → "Like count" sort → scroll.
3. mitmproxy intercepts the `/aweme/v1/general/search/{stream,single}/` responses.
4. Deduplicates + upserts videos/authors to the same Oracle Postgres DB.

## Files

| File | Purpose |
|---|---|
| `mobile_scrape.py` | Core scraper — ADB automation for a single keyword |
| `scrape_forever.py` | Continuous runner (production) |
| `batch_scrape.py` | Batch runner (N terms then stop) |
| `preflight.py` | USB/ADB/usbipd/mitmproxy/TikTok/cert checks |
| `coords.py` | UI coord loader (`coords.json` override, else auto-scale) |
| `calibrate.py` | Interactive helper to dial in coords for your phone |
| `tt_dump.py` | mitmproxy addon (parses `/search/stream/` + `/search/single/`) |
| `.env.example` | Copy to `.env`, set `ADB_DEVICE` if needed |
| `coords.json.example` | Reference coords (720×1612, old Moto g) |
| `setup.sh` | One-shot system setup |

`db.py` and `queue.py` are imported from the parent directory — the phone
scraper shares the exact same database and queue as the GCP scrapers.

## Setup

```bash
cd phone/
bash setup.sh                 # installs python deps + mitmproxy
cp .env.example .env          # edit if needed

# one-time Windows steps (see setup.sh output for details):
#   usbipd bind --busid <id>  (PowerShell Admin)
#   install mitmproxy-ca-cert.pem on phone
#   enable USB debugging on phone
#   install TikTok Lite (com.tiktok.lite.go)

python3 preflight.py          # confirms everything is wired up
python3 calibrate.py          # dial in UI coords for your specific phone
```

## Calibrating for a new phone

The reference coords are for a 720×1612 Moto g 5G 2024. `coords.py` auto-scales
them linearly to your phone's resolution on first load — that usually gets the
buttons close enough to test. If a tap lands in the wrong place:

```bash
python3 calibrate.py --show   # pulls a screenshot + prints current coords
python3 calibrate.py          # full interactive walkthrough, one screen at a time
```

The walkthrough drives TikTok through every UI state (FYP → search → results →
filter popup), pulls a screenshot at each, and prompts for the pixel coords of
each button. Saves the result to `phone/coords.json`, which then overrides the
auto-scaled defaults.

To read coords off the screenshots, open them in any image viewer that shows
pixel position on hover (Paint on Windows, Preview on macOS, VSCode image
preview in the bottom status bar).

## Running

```bash
# one keyword
python3 mobile_scrape.py "street art"

# N terms from the queue
python3 batch_scrape.py --n 5

# continuous (stops + writes SCRAPER_STATUS.txt after 3 consecutive 0-results)
python3 scrape_forever.py
```

## Key differences vs the parent GCP/Waydroid scraper

- **No `ADB_DEVICE` required by default** — `adb` targets the single attached
  USB device. Override with `ADB_DEVICE=<serial>` in `.env` if you have more
  than one device.
- **Coords are calibratable** — `coords.py` auto-scales or loads `coords.json`.
  The Waydroid scraper has hardcoded 720×1612 coords because every GCP VM runs
  an identical emulator.
- **Uses `dumpsys window` foreground check** without the Waydroid dual-focus
  workaround (which only matters when a launcher display is present alongside
  the Waydroid display).
- **Does usbipd auto-attach** — if no ADB device is found, preflight tries to
  attach your phone via `usbipd attach --wsl` using the cached busid/VID:PID.

## Notes

- mitmproxy CA cert must be installed on the phone as a user CA (Settings →
  Security → Install certificate). On Android 7+ TikTok Lite might pin certs
  on certain endpoints; the scraper relies on the fact that search endpoints
  don't, which has held so far.
- `clear_proxy()` is called on every exit — if the scraper crashes, run
  `python3 preflight.py` to clear a leftover proxy (otherwise the phone loses
  internet outside the scraper).
