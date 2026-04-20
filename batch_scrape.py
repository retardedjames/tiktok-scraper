#!/usr/bin/env python3
"""
Batch TikTok scraper — grabs N terms from queue, runs them in one session.

Usage:
  python3 batch_scrape.py [--n 5] [--scrolls N] [--batch 30] [--min-likes 7000]
"""
import argparse
import os
import sys
import time

import queue as qmod
import mobile_scrape as ms
import preflight


def main():
    parser = argparse.ArgumentParser(description="Batch TikTok scraper")
    parser.add_argument("--n", type=int, default=5, help="Terms to grab per run")
    parser.add_argument("--scrolls", type=int, default=None,
                        help="Fixed scroll count per term (default: auto)")
    parser.add_argument("--batch", type=int, default=30,
                        help="Scrolls per batch in auto mode")
    parser.add_argument("--min-likes", type=int, default=7_000,
                        help="Auto-scroll stop threshold (default: 7000)")
    parser.add_argument("--no-db", action="store_true")
    args = parser.parse_args()

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
            print(f"\n{'='*50}")
            print(f"[*] Term {i+1}/{len(terms)}: '{keyword}'")

            # Clear previous capture file
            for sort_type in ["1", "rel"]:
                fname = f"/tmp/tt_{keyword}_{sort_type}.jsonl"
                if os.path.exists(fname):
                    os.remove(fname)

            try:
                ms.search_and_sort(keyword, first=(i == 0))

                if args.scrolls is not None:
                    ms.scroll_results(args.scrolls)
                else:
                    ms.scroll_smart(keyword, batch=args.batch, min_likes=args.min_likes)

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
          f"({elapsed // len(terms)}s avg)")

    s = qmod.stats()
    print(f"Queue: {s.get('pending', 0)} pending, {s.get('done', 0)} done, "
          f"{s.get('failed', 0)} failed")


if __name__ == "__main__":
    main()
