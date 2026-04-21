#!/usr/bin/env python3
"""
Batch TikTok scraper (phone/USB) — grabs N terms from the Oracle queue,
runs them in one TikTok session.

Usage:
  python3 batch_scrape.py [--n 5] [--scrolls N] [--batch N]
"""
import argparse
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env if present (ADB_DEVICE, TIKTOKS_DATABASE_URL, ...)
_env = os.path.join(SCRIPT_DIR, ".env")
if os.path.exists(_env):
    for _line in open(_env):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Pull db/queue from the parent scraper directory — single source of truth.
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))

import queue as qmod
import mobile_scrape as ms
import preflight


def main():
    parser = argparse.ArgumentParser(description="Batch TikTok phone scraper")
    parser.add_argument("--n", type=int, default=5, help="Terms to grab per run")
    parser.add_argument("--scrolls", type=int, default=None,
                        help="Fixed scroll count per term (default: auto)")
    parser.add_argument("--batch", type=int, default=10,
                        help="Scrolls per batch in auto mode")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--debug", action="store_true",
                        help="Save screenshots at each UI step")
    args = parser.parse_args()

    if args.debug:
        ms._DEBUG_SCREENSHOTS = True

    if not preflight.run_all():
        sys.exit(1)

    terms = qmod.grab_batch(args.n, term_type="search")
    if not terms:
        print("No pending terms in queue.")
        return

    print(f"[*] Grabbed {len(terms)} terms: {[t for _, t in terms]}")

    t0 = time.time()
    mp = ms.start_mitmproxy()
    ms.set_proxy()

    try:
        ms.launch_tiktok()

        for i, (term_id, keyword) in enumerate(terms):
            print(f"\n{'=' * 50}")
            print(f"[*] Term {i + 1}/{len(terms)}: '{keyword}'")

            for sort_type in ["1", "rel"]:
                fname = f"/tmp/tt_{ms.safe_keyword(keyword)}_{sort_type}.jsonl"
                if os.path.exists(fname):
                    os.remove(fname)

            try:
                ms.search_and_sort(keyword, first=(i == 0))

                if args.scrolls is not None:
                    ms.scroll_results(args.scrolls)
                else:
                    ms.scroll_smart(keyword, batch=args.batch)

                raw_videos = ms.load_results(keyword)
                total = len(raw_videos)
                raw_videos = [v for v in raw_videos
                              if (v.get("statistics") or {}).get("digg_count", 0) >= 1000]
                print(f"[*] {total} captured, {total - len(raw_videos)} under 1k dropped, "
                      f"{len(raw_videos)} to save.")

                saved = 0
                if not args.no_db and raw_videos:
                    from db import save_search
                    saved = save_search(keyword, "1", raw_videos)
                    print(f"[*] Saved {saved} unique videos to DB.")

                if saved == 0:
                    qmod.mark_retry(term_id)
                    print(f"[*] 0 saved \u2192 term returned to pending for retry.")
                else:
                    qmod.mark_done(term_id, videos_saved=saved)

            except Exception as e:
                print(f"[!] Failed on '{keyword}': {e}")
                qmod.mark_failed(term_id)

    finally:
        ms.clear_proxy()
        mp.terminate()
        time.sleep(0.5)

    elapsed = int(time.time() - t0)
    print(f"\nTotal time: {elapsed} seconds for {len(terms)} terms "
          f"({elapsed // max(len(terms), 1)}s avg)")

    s = qmod.stats()
    print(f"Queue: {s.get('pending', 0)} pending, {s.get('done', 0)} done, "
          f"{s.get('failed', 0)} failed")


if __name__ == "__main__":
    main()
