#!/usr/bin/env python3
"""
Continuous Waydroid TikTok scraper — runs forever, one term at a time.

Stops and appends to SCRAPER_STATUS.txt if:
  - MAX_CONSECUTIVE_EMPTY (3) consecutive terms return 0 saves, OR
  - MAX_CONSECUTIVE_EMPTY consecutive terms raise an exception.

Usage:
  python3 scrape_forever.py [--debug]

.env (optional):
  ADB_DEVICE=127.0.0.1:5556          Waydroid socat proxy (default)
  NTFY_TOPIC=<topic>                 ntfy.sh topic for push notifications
  VM_NAME=<name>                     human-readable name shown in ntfy
  NTFY_PER_TERM=1                    ping after every scraped term
  TIKTOKS_DATABASE_URL=...           override PostgreSQL connection
"""
import argparse
import os
import signal
import sys
import time
import traceback
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env if present
_env = os.path.join(SCRIPT_DIR, ".env")
if os.path.exists(_env):
    for _line in open(_env):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

sys.path.insert(0, SCRIPT_DIR)

STATUS_FILE       = os.path.join(SCRIPT_DIR, "SCRAPER_STATUS.txt")
NTFY_TOPIC        = os.environ.get("NTFY_TOPIC", "retardedjames-tiktok")
VM_NAME           = os.environ.get("VM_NAME", "waydroid-vps")
NTFY_PER_TERM     = os.environ.get("NTFY_PER_TERM", "0") == "1"

MAX_CONSECUTIVE_EMPTY = 3
IDLE_SLEEP = 30
HEARTBEAT_EVERY = 50

_current_term: str = "(none)"


def _handle_signal(signum, frame):
    sig_name = signal.Signals(signum).name
    msg = f"KILLED by {sig_name} (was scraping: '{_current_term}') — external stop, not a scraper failure."
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(STATUS_FILE, "a") as f:
        f.write(line + "\n")
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode(),
            headers={"Title": f"TikTok Scraper [{VM_NAME}]",
                     "Priority": "high", "Tags": "warning,skull"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass
    sys.exit(128 + signum)


def _notify(msg: str, priority: str = "default", tags: str = ""):
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode(),
            headers={"Title": f"TikTok Scraper [{VM_NAME}]",
                     "Priority": priority, "Tags": tags},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[!] ntfy failed: {e}", flush=True)


def _log(msg: str, also_status: bool = False):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if also_status:
        with open(STATUS_FILE, "a") as f:
            f.write(line + "\n")


def main():
    global _current_term

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGHUP,  _handle_signal)

    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Save screenshots at each UI step")
    parser.add_argument("--no-videos-tab-first", dest="videos_tab_first",
                        action="store_false",
                        help="Skip the Videos-tab tap; use the old Top-tab filter flow")
    parser.set_defaults(videos_tab_first=True)
    args = parser.parse_args()

    import queue as qmod
    import mobile_scrape as ms
    import preflight

    if args.debug:
        ms._DEBUG_SCREENSHOTS = True

    if not preflight.run_all():
        _log("PREFLIGHT FAILED — stopping.", also_status=True)
        _notify("Preflight failed — scraper not starting.", priority="high", tags="x")
        sys.exit(1)

    consecutive_empty = 0
    consecutive_fail = 0
    terms_scraped = 0

    _log(f"scraper starting on {VM_NAME}")

    mp = ms.start_mitmproxy()
    ms.set_proxy()

    try:
        while True:
            batch = qmod.grab_batch(1, term_type="search")
            if not batch:
                time.sleep(IDLE_SLEEP)
                continue

            term_id, keyword = batch[0]
            _current_term = keyword

            for sort_type in ["1", "rel"]:
                fname = f"/tmp/tt_{ms.safe_keyword(keyword)}_{sort_type}.jsonl"
                if os.path.exists(fname):
                    os.remove(fname)

            try:
                ms.launch_tiktok()
                ms.search_and_sort(keyword, videos_tab_first=args.videos_tab_first)
                ms.scroll_smart(keyword, batch=5)

                raw = ms.load_results(keyword)
                total = len(raw)
                raw = [v for v in raw
                       if (v.get("statistics") or {}).get("digg_count", 0) >= 1000]
                _log(f"'{keyword}': {total} captured, {len(raw)} >= 1k likes")

                saved = 0
                if raw:
                    from db import save_search
                    saved = save_search(keyword, "1", raw)

                if saved == 0:
                    qmod.mark_retry(term_id)
                    consecutive_empty += 1
                    _log(f"'{keyword}': 0 saved → retry ({consecutive_empty}/{MAX_CONSECUTIVE_EMPTY} empty)")
                    if NTFY_PER_TERM:
                        _notify(f"'{keyword}': 0 saved (retry)", tags="warning")
                else:
                    qmod.mark_done(term_id, videos_saved=saved)
                    consecutive_empty = 0
                    _log(f"'{keyword}': saved {saved} videos")
                    if NTFY_PER_TERM:
                        _notify(f"'{keyword}': {saved} videos", tags="floppy_disk")

                consecutive_fail = 0
                terms_scraped += 1

                if HEARTBEAT_EVERY and terms_scraped % HEARTBEAT_EVERY == 0:
                    _notify(f"heartbeat — {terms_scraped} terms scraped",
                            priority="low", tags="heart")

                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    msg = f"{MAX_CONSECUTIVE_EMPTY} consecutive terms saved 0 videos — stopping."
                    _log(msg, also_status=True)
                    _notify(msg, priority="high", tags="x,skull")
                    break

            except Exception as e:
                tb = traceback.format_exc()
                consecutive_fail += 1
                _log(f"'{keyword}': exception: {e}\n{tb}")
                qmod.mark_failed(term_id)
                _notify(f"'{keyword}': failed ({e})",
                        priority="high" if consecutive_fail >= 2 else "default",
                        tags="x")
                if consecutive_fail >= MAX_CONSECUTIVE_EMPTY:
                    msg = f"{MAX_CONSECUTIVE_EMPTY} consecutive exceptions — stopping."
                    _log(msg, also_status=True)
                    _notify(msg, priority="high", tags="x,skull")
                    break

            finally:
                ms.adb("shell am force-stop com.tiktok.lite.go")
                time.sleep(1.5)

    finally:
        ms.clear_proxy()
        mp.terminate()
        time.sleep(0.5)

    _log(f"scraper stopped after {terms_scraped} terms.")


if __name__ == "__main__":
    main()
