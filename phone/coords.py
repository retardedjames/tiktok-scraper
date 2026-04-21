"""
UI coordinate config for the phone scraper.

Two-tier strategy:

  1. If `coords.json` exists next to this file, use those absolute pixel coords
     (written by `calibrate.py` after the user dials them in on their device).

  2. Otherwise, auto-scale the known-good 720x1612 reference coords (from the
     old Moto g 5G 2024 phone) by the current device's screen dimensions.
     TikTok's layout mostly tracks screen aspect, so linear scaling gets you
     close enough for search/filter/scroll for most phones.

Query the live device with `adb shell wm size`; falls back to reference dims.

UI elements tracked:
  SEARCH_ICON      magnifying glass on FYP
  SEARCH_FIELD     search text input
  SEARCH_CLEAR_BTN X to clear the search field
  SEARCH_BTN       pink "Search" button top-right
  FILTER_ICON      filter/slider icon on results page
  LIKE_COUNT_BTN   "Like count" sort option in filter popup
  APPLY_BTN        Apply button in filter popup
  SWIPE_START_Y    y-coord where a scroll swipe begins
  SWIPE_END_Y      y-coord where a scroll swipe ends
  SWIPE_X          x-coord for scroll swipes (horizontal center)
"""
import json
import os
import subprocess
from dataclasses import dataclass, asdict, field

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COORDS_FILE = os.path.join(SCRIPT_DIR, "coords.json")

REFERENCE_WIDTH = 720
REFERENCE_HEIGHT = 1612

REFERENCE_COORDS = {
    "SEARCH_ICON":      (660, 115),
    "SEARCH_FIELD":     (336, 116),
    "SEARCH_CLEAR_BTN": (571, 116),
    "SEARCH_BTN":       (638, 113),
    "FILTER_ICON":      (700, 173),
    "LIKE_COUNT_BTN":   (260, 1480),
    "APPLY_BTN":        (670, 975),
    "SWIPE_START_Y":    (360, 1200),
    "SWIPE_END_Y":      (360, 400),
}


@dataclass
class Coords:
    SEARCH_ICON: tuple
    SEARCH_FIELD: tuple
    SEARCH_CLEAR_BTN: tuple
    SEARCH_BTN: tuple
    FILTER_ICON: tuple
    LIKE_COUNT_BTN: tuple
    APPLY_BTN: tuple
    SWIPE_START_Y: tuple
    SWIPE_END_Y: tuple
    width: int = REFERENCE_WIDTH
    height: int = REFERENCE_HEIGHT
    source: str = "reference"


def get_device_size(adb_base: list = None) -> tuple:
    """Return (width, height) from `adb shell wm size`, or reference if unavailable."""
    base = adb_base or ["adb"]
    try:
        r = subprocess.run(base + ["shell", "wm", "size"], capture_output=True, text=True, timeout=5)
        # Output: "Physical size: 1080x2400"  (and optionally "Override size: ...")
        for line in r.stdout.splitlines():
            if "size:" in line.lower():
                dims = line.split(":")[-1].strip()
                w, h = dims.split("x")
                return int(w), int(h)
    except Exception:
        pass
    return REFERENCE_WIDTH, REFERENCE_HEIGHT


def _scale(coord: tuple, sx: float, sy: float) -> tuple:
    x, y = coord
    return (int(round(x * sx)), int(round(y * sy)))


def load(adb_base: list = None) -> Coords:
    """Load coords — from coords.json if present, else auto-scale from reference."""
    # 1. Explicit coords.json override
    if os.path.exists(COORDS_FILE):
        with open(COORDS_FILE) as f:
            data = json.load(f)
        return Coords(
            SEARCH_ICON      = tuple(data["SEARCH_ICON"]),
            SEARCH_FIELD     = tuple(data["SEARCH_FIELD"]),
            SEARCH_CLEAR_BTN = tuple(data["SEARCH_CLEAR_BTN"]),
            SEARCH_BTN       = tuple(data["SEARCH_BTN"]),
            FILTER_ICON      = tuple(data["FILTER_ICON"]),
            LIKE_COUNT_BTN   = tuple(data["LIKE_COUNT_BTN"]),
            APPLY_BTN        = tuple(data["APPLY_BTN"]),
            SWIPE_START_Y    = tuple(data["SWIPE_START_Y"]),
            SWIPE_END_Y      = tuple(data["SWIPE_END_Y"]),
            width            = data.get("width", REFERENCE_WIDTH),
            height           = data.get("height", REFERENCE_HEIGHT),
            source           = f"coords.json ({data.get('width', '?')}x{data.get('height', '?')})",
        )

    # 2. Auto-scale from reference
    w, h = get_device_size(adb_base)
    sx, sy = w / REFERENCE_WIDTH, h / REFERENCE_HEIGHT
    return Coords(
        SEARCH_ICON      = _scale(REFERENCE_COORDS["SEARCH_ICON"], sx, sy),
        SEARCH_FIELD     = _scale(REFERENCE_COORDS["SEARCH_FIELD"], sx, sy),
        SEARCH_CLEAR_BTN = _scale(REFERENCE_COORDS["SEARCH_CLEAR_BTN"], sx, sy),
        SEARCH_BTN       = _scale(REFERENCE_COORDS["SEARCH_BTN"], sx, sy),
        FILTER_ICON      = _scale(REFERENCE_COORDS["FILTER_ICON"], sx, sy),
        LIKE_COUNT_BTN   = _scale(REFERENCE_COORDS["LIKE_COUNT_BTN"], sx, sy),
        APPLY_BTN        = _scale(REFERENCE_COORDS["APPLY_BTN"], sx, sy),
        SWIPE_START_Y    = _scale(REFERENCE_COORDS["SWIPE_START_Y"], sx, sy),
        SWIPE_END_Y      = _scale(REFERENCE_COORDS["SWIPE_END_Y"], sx, sy),
        width            = w,
        height           = h,
        source           = f"auto-scaled from {REFERENCE_WIDTH}x{REFERENCE_HEIGHT} "
                           f"to {w}x{h} (sx={sx:.3f}, sy={sy:.3f})",
    )


def save(c: Coords):
    """Write a Coords to coords.json."""
    data = {
        k: list(v) if isinstance(v, tuple) else v
        for k, v in asdict(c).items()
        if k != "source"
    }
    with open(COORDS_FILE, "w") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    c = load()
    print(f"Source: {c.source}")
    print(f"Screen: {c.width}x{c.height}")
    for k, v in asdict(c).items():
        if k not in ("width", "height", "source"):
            print(f"  {k:18} {v}")
