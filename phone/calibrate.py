#!/usr/bin/env python3
"""
Interactive coordinate calibration for the phone scraper.

For each UI state (FYP, search screen, results page, filter popup), this
script:
  1. Drives TikTok to that state with ADB
  2. Pulls a screenshot to /tmp/calibrate_*.png
  3. Prompts you for the (x, y) pixel coords of each button visible in that
     screenshot

It then writes everything to phone/coords.json so mobile_scrape.py will use
them instead of the auto-scaled reference coords.

Tip: open the screenshot in an image viewer that shows pixel coordinates
(e.g. Paint on Windows, Preview on macOS, or the VSCode image preview which
shows x,y at the bottom of the window).

Usage:
  python3 calibrate.py           # full interactive walkthrough
  python3 calibrate.py --show    # pull one screenshot + print current coords
"""
import argparse
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

import coords as coords_mod

ADB_DEVICE = os.environ.get("ADB_DEVICE", "")


def _adb_base():
    return ["adb", "-s", ADB_DEVICE] if ADB_DEVICE else ["adb"]


def adb(cmd: str):
    return subprocess.run(_adb_base() + cmd.split(), capture_output=True)


def adb_tap(x, y):
    adb(f"shell input tap {x} {y}")
    time.sleep(0.4)


def screenshot(path: str):
    r = subprocess.run(_adb_base() + ["exec-out", "screencap", "-p"], capture_output=True)
    with open(path, "wb") as f:
        f.write(r.stdout)
    print(f"    screenshot \u2192 {path}")


def prompt_xy(name: str, default: tuple | None = None) -> tuple:
    hint = f" [default {default[0]},{default[1]}]" if default else ""
    while True:
        s = input(f"  {name}{hint}: x,y > ").strip()
        if not s and default:
            return default
        parts = s.replace(" ", "").split(",")
        if len(parts) == 2:
            try:
                return (int(parts[0]), int(parts[1]))
            except ValueError:
                pass
        print("    (enter as: 540,120)")


def main():
    parser = argparse.ArgumentParser(description="Calibrate TikTok UI coordinates")
    parser.add_argument("--show", action="store_true",
                        help="Pull one screenshot and print current coords, then exit")
    args = parser.parse_args()

    w, h = coords_mod.get_device_size(_adb_base())
    print(f"[*] Device screen: {w}x{h}")
    current = coords_mod.load(_adb_base())
    print(f"[*] Current coords: {current.source}")

    if args.show:
        path = "/tmp/calibrate_current.png"
        screenshot(path)
        for k in ("SEARCH_ICON", "SEARCH_FIELD", "SEARCH_CLEAR_BTN", "SEARCH_BTN",
                  "FILTER_ICON", "LIKE_COUNT_BTN", "APPLY_BTN",
                  "SWIPE_START_Y", "SWIPE_END_Y"):
            print(f"  {k:18} {getattr(current, k)}")
        return

    print()
    print("[*] This walks through each TikTok UI state and prompts for coordinates.")
    print("    Open each screenshot in an image viewer to read pixel positions.")
    print("    Press ENTER at any prompt to keep the current/default value.")
    print()

    cur = {k: getattr(current, k) for k in ("SEARCH_ICON", "SEARCH_FIELD", "SEARCH_CLEAR_BTN",
                                             "SEARCH_BTN", "FILTER_ICON", "LIKE_COUNT_BTN",
                                             "APPLY_BTN", "SWIPE_START_Y", "SWIPE_END_Y")}

    # STEP 1: FYP — find the search icon (top right)
    print("\n--- STEP 1: For-You page ---")
    input("Launch TikTok and ensure you're on the For-You (home) feed. Press ENTER to screenshot.")
    screenshot("/tmp/calibrate_01_fyp.png")
    cur["SEARCH_ICON"] = prompt_xy("SEARCH_ICON (magnifying glass, top right)", cur["SEARCH_ICON"])

    # STEP 2: tap it, go to search screen
    print("\n--- STEP 2: Search screen ---")
    adb_tap(*cur["SEARCH_ICON"])
    time.sleep(1.8)
    screenshot("/tmp/calibrate_02_search.png")
    cur["SEARCH_FIELD"]     = prompt_xy("SEARCH_FIELD (the text input)", cur["SEARCH_FIELD"])
    cur["SEARCH_CLEAR_BTN"] = prompt_xy("SEARCH_CLEAR_BTN (X to clear field — may require typing first)",
                                        cur["SEARCH_CLEAR_BTN"])
    cur["SEARCH_BTN"]       = prompt_xy("SEARCH_BTN (pink 'Search' button top right)", cur["SEARCH_BTN"])

    # STEP 3: perform a search to reach the results page + filter icon
    print("\n--- STEP 3: Results page ---")
    adb_tap(*cur["SEARCH_FIELD"])
    time.sleep(0.3)
    subprocess.run(_adb_base() + ["shell", "input", "text", "cat"], capture_output=True)
    time.sleep(0.4)
    adb_tap(*cur["SEARCH_BTN"])
    time.sleep(4.0)
    screenshot("/tmp/calibrate_03_results.png")
    cur["FILTER_ICON"] = prompt_xy("FILTER_ICON (filter/slider icon on results page)", cur["FILTER_ICON"])

    # STEP 4: open filter popup
    print("\n--- STEP 4: Filter popup ---")
    adb_tap(*cur["FILTER_ICON"])
    time.sleep(1.5)
    screenshot("/tmp/calibrate_04_filter.png")
    cur["LIKE_COUNT_BTN"] = prompt_xy("LIKE_COUNT_BTN ('Like count' sort option)", cur["LIKE_COUNT_BTN"])
    cur["APPLY_BTN"]      = prompt_xy("APPLY_BTN (Apply button in filter popup)", cur["APPLY_BTN"])

    # STEP 5: swipe coordinates
    print("\n--- STEP 5: Scroll swipe coords ---")
    print("  Swipes go from SWIPE_START_Y (lower on screen) to SWIPE_END_Y (higher).")
    print(f"  Default is horizontal center, start=~{int(h*0.75)}, end=~{int(h*0.25)}.")
    cur["SWIPE_START_Y"] = prompt_xy("SWIPE_START_Y", (w // 2, int(h * 0.75)))
    cur["SWIPE_END_Y"]   = prompt_xy("SWIPE_END_Y",   (w // 2, int(h * 0.25)))

    # Save
    c = coords_mod.Coords(
        SEARCH_ICON      = cur["SEARCH_ICON"],
        SEARCH_FIELD     = cur["SEARCH_FIELD"],
        SEARCH_CLEAR_BTN = cur["SEARCH_CLEAR_BTN"],
        SEARCH_BTN       = cur["SEARCH_BTN"],
        FILTER_ICON      = cur["FILTER_ICON"],
        LIKE_COUNT_BTN   = cur["LIKE_COUNT_BTN"],
        APPLY_BTN        = cur["APPLY_BTN"],
        SWIPE_START_Y    = cur["SWIPE_START_Y"],
        SWIPE_END_Y      = cur["SWIPE_END_Y"],
        width            = w,
        height           = h,
        source           = "calibrated",
    )
    coords_mod.save(c)
    print(f"\n[\u2713] Saved {coords_mod.COORDS_FILE}")
    print(f"[\u2713] Re-run `python3 preflight.py` to confirm the new coords load.")


if __name__ == "__main__":
    main()
