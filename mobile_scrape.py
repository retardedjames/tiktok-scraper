#!/usr/bin/env python3
"""
TikTok Lite scraper via mitmproxy + adb UI automation.

Usage:
  python3 mobile_scrape.py <keyword> [--count N] [--out file.json] [--scrolls N]
"""

import sys
import os
import json
import time
import shlex
import socket
import subprocess
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DUMP_SCRIPT = os.path.join(SCRIPT_DIR, "tt_dump.py")

# Set ADB_DEVICE to target a specific device, e.g.:
#   ADB_DEVICE=127.0.0.1:5555  (on GCP2 itself, local Waydroid)
#   ADB_DEVICE=34.153.25.251:5556  (from WSL2, remote)
_ADB_DEVICE = os.environ.get("ADB_DEVICE", "")

# UI coordinates (720x1612 screen) — verified against Waydroid screenshots
SEARCH_ICON      = (651, 97)    # magnifying glass (top-right, always visible on FYP)
SEARCH_FIELD     = (325, 91)    # search input field
SEARCH_CLEAR_BTN = (533, 91)    # X button to clear search field
SEARCH_BTN       = (633, 91)    # pink "Search" button (top-right of search bar)
FILTER_ICON      = (700, 173)   # slider/filter icon (right of tab bar)
LIKE_COUNT_BTN   = (315, 1420)  # "Like count" sort button in filter popup
APPLY_BTN        = (670, 778)   # "Apply" button in filter popup header
HEART_BTN        = (668, 912)   # FYP heart/like button (right action rail)
HOME_TAB         = (72, 1498)   # Home tab in bottom nav (returns to FYP)


def safe_keyword(k: str) -> str:
    """Slash-safe keyword for use in filesystem paths.

    Must match tt_dump.safe_keyword exactly — writer (mitmproxy addon) and
    reader (this module) both consume it.
    """
    return k.replace("/", "_").replace("\\", "_")


def _adb_base() -> list:
    return ["adb", "-s", _ADB_DEVICE] if _ADB_DEVICE else ["adb"]


def adb(cmd, check=False):
    return subprocess.run(_adb_base() + cmd.split(), capture_output=True, check=check)


def adb_tap(x, y):
    adb(f"shell input tap {x} {y}")
    time.sleep(0.4)


_DEBUG_SCREENSHOTS = False


def screenshot(tag: str):
    if not _DEBUG_SCREENSHOTS:
        return
    path = f"/tmp/screen_{tag}.png"
    result = subprocess.run(_adb_base() + ["exec-out", "screencap", "-p"], capture_output=True)
    with open(path, "wb") as f:
        f.write(result.stdout)
    print(f"[dbg] screenshot → {path}")


def _foreground_app() -> str:
    """Return the package name of the current focused window."""
    r = subprocess.run(_adb_base() + ["shell", "dumpsys", "window"],
                       capture_output=True)
    out = r.stdout.decode() if r.stdout else ""
    for line in out.splitlines():
        if "mCurrentFocus" in line and "/" in line:
            # e.g. mCurrentFocus=Window{abc com.tiktok.lite.go/...}
            try:
                pkg = line.split("/")[0].split()[-1]
                if "{" in pkg:
                    pkg = pkg.split("{")[-1]
                return pkg
            except Exception:
                pass
    return ""


def start_mitmproxy():
    if not os.path.exists(DUMP_SCRIPT):
        raise RuntimeError(f"mitmproxy addon missing: {DUMP_SCRIPT}")
    subprocess.run("fuser -k 8080/tcp 2>/dev/null || true", shell=True)
    time.sleep(0.3)
    proc = subprocess.Popen(
        [sys.executable, os.path.expanduser("~/.local/bin/mitmdump"),
         "--listen-host", "127.0.0.1", "--listen-port", "8080",
         "-s", DUMP_SCRIPT,
         "-w", "/tmp/flows.mitm"],
        stdout=open("/tmp/mitmdump.log", "w"), stderr=subprocess.STDOUT,
    )
    # Poll until port is open
    opened = False
    for _ in range(30):
        try:
            socket.create_connection(("127.0.0.1", 8080), timeout=0.5).close()
            opened = True
            break
        except OSError:
            time.sleep(0.2)
    if opened:
        print("[*] mitmproxy: port 8080 open OK")
    else:
        print("[!] mitmproxy: port 8080 never opened — mitmdump may have crashed")
    return proc


def set_proxy():
    r1 = adb("reverse tcp:8080 tcp:8080")
    r2 = adb("shell settings put global http_proxy 127.0.0.1:8080")
    # Verify proxy was actually set
    check = adb("shell settings get global http_proxy")
    proxy_val = check.stdout.decode().strip() if check.stdout else "?"
    print(f"[*] Proxy set: {proxy_val} (reverse exit={r1.returncode}, set exit={r2.returncode})")


def clear_proxy():
    adb("shell settings put global http_proxy :0")
    adb("shell settings delete global http_proxy")


def launch_tiktok():
    adb("shell am force-stop com.tiktok.lite.go")
    time.sleep(0.5)
    r = adb("shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity")
    out = r.stdout.decode().strip() if r.stdout else ""
    print(f"[*] TikTok launch: exit={r.returncode} {out[:80]}")
    time.sleep(7.0)
    screenshot("01_after_launch")
    # Dismiss "Allow contacts" dialog if present
    r2 = adb("shell dumpsys activity top")
    top_out = r2.stdout.decode().lower() if r2.stdout else ""
    if "permission" in top_out or "contacts" in top_out:
        print("[*] Dismissing permissions dialog...")
        adb("shell input tap 350 916")
        time.sleep(1.5)
        screenshot("02_after_dismiss_dialog")
    # Confirm TikTok is in foreground
    fg = _foreground_app()
    print(f"[*] Foreground app: {fg}")
    if "tiktok" not in fg:
        print("[!] TikTok not in foreground after launch — retrying...")
        adb("shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity")
        time.sleep(10.0)
        screenshot("03_after_launch_retry")
        fg = _foreground_app()
        print(f"[*] Foreground app after retry: {fg}")


def _ensure_tiktok_foreground():
    """Re-launch TikTok if it's no longer the foreground app."""
    fg = _foreground_app()
    if "tiktok" not in fg:
        print(f"[!] TikTok not in foreground (got: {fg}) — re-launching...")
        adb("shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity")
        time.sleep(10.0)
        fg = _foreground_app()
        print(f"[*] Foreground after re-launch: {fg}")


def browse_fyp(swipes: int = 2, likes: int = 1, dwell: float = 3.0):
    """Between-term pause. Active FYP browsing (back-to-home, swipe, heart)
    was tapping unpredictable places when the pre-search screen state
    varied — disabled for now, restore by uncommenting. For the moment
    this is just a fixed idle delay."""
    time.sleep(10)
    # try:
    #     for _ in range(3):
    #         adb("shell input keyevent KEYCODE_BACK")
    #         time.sleep(0.4)
    #     _ensure_tiktok_foreground()
    #     adb_tap(*HOME_TAB)
    #     time.sleep(1.5)
    #     for i in range(swipes):
    #         if i < likes:
    #             adb_tap(*HEART_BTN)
    #             time.sleep(1.0)
    #         adb("shell input swipe 360 1200 360 400 350")
    #         time.sleep(dwell)
    #     print(f"[*] FYP browse done ({swipes} swipes, {likes} likes)")
    # except Exception as e:
    #     print(f"[!] browse_fyp failed (ignored): {e}")


def search_and_sort(keyword: str, first: bool = True):
    tag = keyword.replace(" ", "_")[:20]

    # Press Back a few times to dismiss any dialogs/videos
    print("[*] Pressing Back to clear any overlays...")
    for _ in range(3):
        adb("shell input keyevent KEYCODE_BACK")
        time.sleep(0.4)

    # Re-launch if Back presses exited TikTok entirely
    _ensure_tiktok_foreground()
    screenshot(f"{tag}_A_before_search_tap")

    print("[*] Tapping search icon...")
    adb_tap(*SEARCH_ICON)
    time.sleep(2.0)
    screenshot(f"{tag}_B_after_search_tap")

    # Verify we're still in TikTok before typing
    fg = _foreground_app()
    if "tiktok" not in fg:
        print(f"[!] Lost TikTok foreground after search tap (got: {fg}) — aborting term")
        raise RuntimeError(f"TikTok lost foreground: {fg}")

    # Clear field and focus
    adb_tap(*SEARCH_FIELD)
    time.sleep(0.15)
    adb_tap(*SEARCH_CLEAR_BTN)
    time.sleep(0.15)
    adb_tap(*SEARCH_FIELD)
    time.sleep(0.15)

    print(f"[*] Typing '{keyword}'...")
    for ch in keyword:
        if ch == ' ':
            subprocess.run(_adb_base() + ["shell", "input", "keyevent", "KEYCODE_SPACE"], capture_output=True)
        else:
            subprocess.run(_adb_base() + ["shell", "input", "text", ch], capture_output=True)
        time.sleep(0.15)
    time.sleep(0.4)
    screenshot(f"{tag}_C_after_typing")

    adb_tap(*SEARCH_BTN)
    time.sleep(4.5)
    screenshot(f"{tag}_D_after_search_btn")

    print("[*] Opening filter popup...")
    adb_tap(*FILTER_ICON)
    time.sleep(2.0)
    screenshot(f"{tag}_E_filter_popup")

    print("[*] Selecting 'Like count' sort...")
    adb_tap(*LIKE_COUNT_BTN)
    time.sleep(0.4)

    print("[*] Applying filter...")
    adb_tap(*APPLY_BTN)
    time.sleep(4.5)
    screenshot(f"{tag}_F_after_apply")


def scroll_results(scrolls: int):
    print(f"[*] Scrolling {scrolls} times...")
    for i in range(scrolls):
        adb("shell input swipe 360 1200 360 400 500")
        time.sleep(1.2)
        print(f"    scroll {i+1}/{scrolls}")


def _jsonl_line_count(fname: str) -> int:
    if not os.path.exists(fname):
        return 0
    with open(fname) as f:
        return sum(1 for _ in f)


def _recent_likes(fname: str, n: int = 10) -> list[int]:
    """Return likes for the last n unique videos in capture order (lowest-ranked by TikTok)."""
    if not os.path.exists(fname):
        return []
    with open(fname) as f:
        lines = f.readlines()
    seen = set()
    unique = []
    for line in reversed(lines):
        try:
            v = json.loads(line)
            aid = v.get("aweme_id", "")
            if aid and aid not in seen:
                seen.add(aid)
                unique.append((v.get("statistics") or {}).get("digg_count", 0))
                if len(unique) == n:
                    break
        except Exception:
            pass
    return unique


def scroll_smart(keyword: str, sort_type: str = "1", batch: int = 10,
                 max_scrolls: int = 33) -> int:
    """
    Scroll in batches up to max_scrolls total, stopping early only if
    TikTok stops loading new content.
    Returns total scroll count.
    """
    fname = f"/tmp/tt_{safe_keyword(keyword)}_{sort_type}.jsonl"
    total = 0

    while True:
        prev_count = _jsonl_line_count(fname)

        this_batch = min(batch, max_scrolls - total)
        for _ in range(this_batch):
            adb("shell input swipe 360 1200 360 400 500")
            time.sleep(1.2)
        total += this_batch

        new_count = _jsonl_line_count(fname)
        if new_count - prev_count == 0:
            print(f"[*] No new content after {total} scrolls — TikTok limit reached.")
            print(f"[dbg] JSONL file: {fname} exists={os.path.exists(fname)} lines={new_count}")
            print(f"[dbg] mitmdump log tail:")
            try:
                with open("/tmp/mitmdump.log") as _f:
                    _lines = _f.readlines()
                    for _l in _lines[-10:]:
                        print(f"       {_l.rstrip()}")
            except Exception as _e:
                print(f"       (could not read: {_e})")
            break

        print(f"[*] {total} scrolls — {new_count} lines captured")

        if total >= max_scrolls:
            print(f"[*] Reached max {max_scrolls} scrolls.")
            break

    return total


def load_results(keyword: str, sort_type: str = "1") -> list[dict]:
    fname = f"/tmp/tt_{safe_keyword(keyword)}_{sort_type}.jsonl"
    if not os.path.exists(fname):
        return []
    seen = set()
    results = []
    with open(fname) as f:
        for line in f:
            try:
                v = json.loads(line)
                aweme_id = v.get("aweme_id", "")
                if aweme_id and aweme_id not in seen:
                    seen.add(aweme_id)
                    results.append(v)
            except Exception:
                pass
    results.sort(key=lambda v: (v.get("statistics") or {}).get("digg_count", 0), reverse=True)
    return results


def main():
    parser = argparse.ArgumentParser(description="TikTok Lite scraper via phone automation")
    parser.add_argument("phrase", help="Search phrase")
    parser.add_argument("--count", type=int, default=50,
                        help="Max videos to show in terminal output")
    parser.add_argument("--scrolls", type=int, default=None,
                        help="Fixed scroll count (default: auto-scroll)")
    parser.add_argument("--batch", type=int, default=30,
                        help="Scrolls per batch when auto-scrolling (default: 30)")
    parser.add_argument("--out", default=None)
    parser.add_argument("--no-db", action="store_true", help="Skip saving to database")
    args = parser.parse_args()

    import preflight
    if not preflight.run_all():
        sys.exit(1)

    keyword = args.phrase

    for sort_type in ["1", "rel"]:
        fname = f"/tmp/tt_{safe_keyword(keyword)}_{sort_type}.jsonl"
        if os.path.exists(fname):
            os.remove(fname)

    print("[*] Starting mitmproxy...")
    mp = start_mitmproxy()
    set_proxy()

    try:
        print("[*] Launching TikTok Lite...")
        launch_tiktok()
        search_and_sort(keyword, first=True)
        if args.scrolls is not None:
            scroll_results(args.scrolls)
        else:
            scroll_smart(keyword, batch=args.batch)
    finally:
        clear_proxy()
        mp.terminate()
        time.sleep(0.5)

    raw_videos = load_results(keyword)
    if not raw_videos:
        print("No videos captured. The app may not have sent traffic through the proxy.")
        return

    total = len(raw_videos)
    raw_videos = [v for v in raw_videos
                  if (v.get("statistics") or {}).get("digg_count", 0) >= 1000]
    print(f"[*] {total} captured, {total - len(raw_videos)} under 1k dropped, "
          f"{len(raw_videos)} to save.")

    if not args.no_db:
        print(f"[*] Saving {len(raw_videos)} videos to database...")
        from db import save_search
        saved = save_search(keyword, "1", raw_videos)
        print(f"[*] Saved {saved} unique videos to DB.")

    display = raw_videos[:args.count]
    if args.out:
        out_list = []
        for v in display:
            stats = v.get("statistics") or {}
            author = v.get("author") or {}
            uname = author.get("unique_id", "")
            vid = v.get("aweme_id", "")
            out_list.append({
                "id": vid, "author": uname,
                "description": v.get("desc", ""),
                "likes": stats.get("digg_count", 0),
                "views": stats.get("play_count", 0),
                "url": f"https://www.tiktok.com/@{uname}/video/{vid}",
            })
        with open(args.out, "w") as f:
            json.dump(out_list, f, indent=2, ensure_ascii=False)
        print(f"Saved {len(out_list)} videos to {args.out}")
    else:
        print(f"\nTop {len(display)} videos for '{keyword}' (sorted by likes):\n")
        for i, v in enumerate(display, 1):
            stats = v.get("statistics") or {}
            author = v.get("author") or {}
            uname = author.get("unique_id", "")
            vid = v.get("aweme_id", "")
            likes = stats.get("digg_count", 0)
            print(f"{i:3}. {likes:>10,} likes  @{uname}")
            print(f"      {v.get('desc', '')[:75]}")
            print(f"      https://www.tiktok.com/@{uname}/video/{vid}\n")


if __name__ == "__main__":
    main()
