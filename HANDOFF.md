# Handoff — TikTok Scraper Session 2026-04-20

## Current State: Ready to Scrape

Everything is configured and working. The previous session debugged the full stack
from scratch on GCP2 Waydroid and got successful mitmproxy interception confirmed.

---

## What Was Fixed This Session

### 1. mitmproxy CA cert (critical)
- `base.objection.apk` is **byte-for-byte identical** to `base.apk` — it was never patched.
- User CA store (`/data/misc/user/0/cacerts-added/`) is ignored by TikTok (SDK 35 target).
- **Fix:** bind-mount a patched cacerts dir over `/system/etc/security/cacerts/` via lxc-attach.
- This is now **automated in `waydroid-start.sh`** — runs on every Waydroid boot.
- Requires `/sdcard/mitmproxy-ca.pem` to exist on the device (already pushed, persists in /data).

### 2. TikTok launch command
- `monkey` command was launching Google Search app instead of TikTok on Waydroid.
- **Fix:** `am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity`
- Already updated in `mobile_scrape.py`.

### 3. ADB text input
- `input text 'street art'` drops characters on Waydroid (slower than real phone).
- **Fix:** type character-by-character with 0.15s delay between each.
- Already updated in `mobile_scrape.py`.

### 4. UI coordinates
- All coordinates verified by screenshot analysis. Already updated in `mobile_scrape.py`.
- See `WAYDROID_GCP2_SETUP.md` for the full verified coordinate table.

---

## To Start a New Scrape Session

### Step 1 — Connect from WSL2

```bash
adb kill-server
adb connect 34.153.25.251:5556
adb -s 34.153.25.251:5556 shell getprop sys.boot_completed  # must be 1
```

### Step 2 — Verify cert is mounted (do this every session)

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 \
  "sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- ls /system/etc/security/cacerts/c8750f0d.0"
```

If the file is missing (e.g. after a container restart that predates the `waydroid-start.sh` fix):

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 "
  sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
    mkdir -p /tmp/cacerts
    cp /system/etc/security/cacerts/* /tmp/cacerts/
    cp /sdcard/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0
    chmod 644 /tmp/cacerts/c8750f0d.0
    mount --bind /tmp/cacerts /system/etc/security/cacerts
    echo done
  '
"
```

### Step 3 — Check queue, add terms if needed

```bash
cd /home/james/tiktoks
python3 emulator/queue.py stats
python3 emulator/queue.py reset   # unstick any in_progress from a crashed session

# Import more terms if queue is low:
# python3 emulator/queue.py import emulator/search_terms.txt search
```

### Step 4 — Run the batch scraper

```bash
cd /home/james/tiktoks
python3 emulator/batch_scrape.py --n 10
```

- Uses smart auto-scroll: stops when 3 of last 10 captured videos have <7k likes.
- No `--scrolls` flag needed (auto mode is correct).
- Watch VNC at `34.153.25.251:5900` (no password) to monitor progress.

---

## Queue State at End of This Session

- `street art` — marked **done** (captured ~20 videos manually but NOT saved to DB;
  safe to re-scrape by resetting the queue if desired)
- `gym motivation`, `night cooking`, `ocean sunset` — **pending**

Run `python3 emulator/queue.py reset` first to reset any stuck states.

---

## Known Gotchas

- **"No internet connection" in TikTok** = mitmproxy is set as proxy but the cert
  bind-mount is not active. Check Step 2 above.
- **Characters dropped when typing** = the 0.15s delay in mobile_scrape.py handles this,
  but if you're doing manual adb taps use the char-by-char Python snippet.
- **Filter popup Apply button** = y≈778 (near top of the panel at y=722, NOT y=975 as
  originally coded). Tapping too high hits the search results behind the panel.
- **ADB reverse tunnel** = must be re-run each session:
  `adb -s 34.153.25.251:5556 reverse tcp:8080 tcp:8080`
  The batch scraper calls `set_proxy()` which does this automatically.
- **Contacts dialog on first TikTok launch** = tap "Don't allow" at (350, 916).
  `launch_tiktok()` in mobile_scrape.py now handles this automatically.

---

## Files Modified This Session

- `emulator/mobile_scrape.py` — coordinates, launch cmd, typing, delays
- `emulator/WAYDROID_GCP2_SETUP.md` — cert approach, coordinates, troubleshooting
- `/usr/local/bin/waydroid-start.sh` on GCP2 — auto cert bind-mount on boot
