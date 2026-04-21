#!/usr/bin/env python3
"""
Continuous phone/USB TikTok scraper — runs forever, one term at a time.

Stops and appends to SCRAPER_STATUS.txt if:
  - MAX_CONSECUTIVE_EMPTY (3) consecutive terms return 0 saves, OR
  - MAX_CONSECUTIVE_EMPTY consecutive terms raise an exception.

Usage:
  python3 scrape_forever.py [--debug]

.env (optional):
  ADB_DEVICE=<serial-or-host:port>   target a specific ADB device
  NTFY_TOPIC=<topic>                 ntfy.sh topic for push notifications
  VM_NAME=<name>                     human-readable name shown in ntfy
  NTFY_PER_TERM=1                    ping after every scraped term
"""
import argparse
import os
import signal
import sys
import time
import traceback
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

_env = os.path.join(SCRIPT_DIR, ".env")
if os.path.exists(_env):
    for _line in open(_env):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Pull db/queue from the parent scraper directory
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))

STATUS_FILE       = os.path.join(SCRIPT_DIR, "SCRAPER_STATUS.txt")
NTFY_TOPIC        = os.environ.get("NTFY_TOPIC", "retardedjames-tiktok")
VM_NAME           = os.environ.get("VM_NAME", "phone")
NTFY_PER_TERM     = os.environ.get("NTFY_PER_TERM", "0") == "1"

MAX_CONSECUTIVE_EMPTY = 3
IDLE_SLEEP = 30
HEARTBEAT_EVERY = 50

_current_term: str = "(none)"


def _handle_signal(signum, frame):
    sig_name = signal.Signals(signum).name
    msg = f"KILLED by {sig_name} (was scraping: '{_current_term}') \u2014 external stop, not a scraper failure."
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
            headers={"Title": f"TikTok Scraper [{VM_NAME}]", "Priority": "low", "Tags": "stop_sign"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass
    sys.exit(0)


def _notify(msg: str, priority: str = "default", tags: str = ""):
    try:
        import urllib.request
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=msg.encode(),
            headers={
                "Title": f"TikTok Scraper [{VM_NAME}]",
                "Priority": priority,
                "Tags": tags,
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _log(msg: str, also_status: bool = False, notify: bool = False,
         priority: str = "default", tags: str = ""):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if also_status:
        with open(STATUS_FILE, "a") as f:
            f.write(line + "\n")
    if notify:
        _notify(msg, priority=priority, tags=tags)


def main():
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Save screenshots at each UI step")
    args = parser.parse_args()

    import queue as qmod
    import mobile_scrape as ms
    import preflight

    if args.debug:
        ms._DEBUG_SCREENSHOTS = True

    if not preflight.run_all():
        _log("PREFLIGHT FAILED \u2014 stopping.", also_status=True,
             notify=True, priority="high", tags="x,rotating_light")
        sys.exit(1)

    _log("Scraper started.", also_status=True,
         notify=True, priority="low", tags="white_check_mark")

    consecutive_empty = 0
    terms_done = 0
    mp = ms.start_mitmproxy()
    ms.set_proxy()
    tiktok_launched = False

    try:
        while True:
            terms = qmod.grab_batch(1, term_type="search")
            if not terms:
                _log(f"Queue empty \u2014 sleeping {IDLE_SLEEP}s")
                time.sleep(IDLE_SLEEP)
                continue

            term_id, keyword = terms[0]
            global _current_term
            _current_term = keyword
            _log(f"[>] '{keyword}'")

            for sort_type in ["1", "rel"]:
                fname = f"/tmp/tt_{ms.safe_keyword(keyword)}_{sort_type}.jsonl"
                if os.path.exists(fname):
                    os.remove(fname)

            try:
                if not tiktok_launched:
                    ms.launch_tiktok()
                    tiktok_launched = True

                ms.search_and_sort(keyword)
                ms.scroll_smart(keyword)

                raw_videos = ms.load_results(keyword)
                total = len(raw_videos)
                raw_videos = [v for v in raw_videos
                              if (v.get("statistics") or {}).get("digg_count", 0) >= 1000]

                saved = 0
                if raw_videos:
                    from db import save_search
                    saved = save_search(keyword, "1", raw_videos)

                if saved == 0:
                    qmod.mark_retry(term_id)
                else:
                    qmod.mark_done(term_id, videos_saved=saved)
                terms_done += 1
                _log(f"    '{keyword}' \u2192 {saved} saved ({total} captured)")
                if NTFY_PER_TERM:
                    _notify(f"'{keyword}' \u2014 {saved} videos saved [{VM_NAME}]",
                            priority="low", tags="mag")

                ms.browse_fyp()

                if saved == 0:
                    consecutive_empty += 1
                    _log(
                        f"    WARNING: 0 saved ({consecutive_empty}/{MAX_CONSECUTIVE_EMPTY})",
                        also_status=True, notify=True, priority="default", tags="warning"
                    )
                    if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                        _log(
                            f"STOPPING: {MAX_CONSECUTIVE_EMPTY} consecutive terms returned 0 results \u2014 needs investigation.",
                            also_status=True, notify=True, priority="high", tags="x,rotating_light"
                        )
                        break
                else:
                    consecutive_empty = 0
                    if terms_done % HEARTBEAT_EVERY == 0:
                        s = qmod.stats()
                        _log(
                            f"Heartbeat: {terms_done} terms done, {s.get('pending', 0)} pending.",
                            notify=True, priority="low", tags="green_heart"
                        )

            except Exception as e:
                _log(f"    ERROR on '{keyword}': {e}", also_status=True,
                     notify=True, priority="default", tags="warning")
                print(traceback.format_exc(), flush=True)
                qmod.mark_failed(term_id)
                consecutive_empty += 1
                tiktok_launched = False
                if consecutive_empty >= MAX_CONSECUTIVE_EMPTY:
                    _log(
                        f"STOPPING: {MAX_CONSECUTIVE_EMPTY} consecutive failures \u2014 needs investigation.",
                        also_status=True, notify=True, priority="high", tags="x,rotating_light"
                    )
                    break

    finally:
        ms.clear_proxy()
        mp.terminate()
        time.sleep(0.5)
        _log("Scraper stopped.", also_status=True, notify=True, priority="low", tags="stop_sign")


if __name__ == "__main__":
    main()
