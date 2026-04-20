# TikTok Scraper — Complete Setup Guide

This document is the single source of truth for setting up the TikTok scraper from scratch.
A fresh Claude instance handed this file, a clean Ubuntu VM (GCP2 specs), and the project
folder should be able to reach a working scraper with no external help.

---

## What This Project Does

Scrapes TikTok's **mobile API** for all-time most-liked videos by keyword. The mobile API
(intercepted via mitmproxy) returns the full historical catalog sorted by likes — the web
API only returns recent content, missing older viral videos.

**Scrape flow:**
1. mitmproxy runs on the local machine, listening on port 8080
2. Android proxy is set to tunnel through mitmproxy via `adb reverse`
3. ADB UI-automates TikTok Lite: search → sort by likes → scroll
4. mitmproxy intercepts `/aweme/v1/general/search/single/` responses
5. Results are deduplicated and batch-upserted to PostgreSQL on the Oracle VPS

---

## Two Operating Modes

| Mode | When to use | ADB target |
|---|---|---|
| **Physical phone** (original) | Phone plugged into Windows via USB + usbipd | `adb connect <local>` via usbipd |
| **GCP2 Waydroid** (primary) | Headless VM, always-on, no USB required | `adb connect 34.153.25.251:5556` |

The Waydroid mode on GCP2 is the primary production method. The physical phone setup is
documented in `setup.sh` for local/dev use.

---

## Infrastructure

### GCP2 — Waydroid VM (active)

- **Provider:** Google Cloud, us-east5
- **Machine:** e2-standard-2 (2 vCPU, 4 GB RAM), x86_64
- **OS:** Ubuntu 25.10 (Questing), kernel 6.17.0-1012-gcp
- **IP:** `34.153.25.251`
- **SSH:** `ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251`
- **User:** `jamescvermont` (UID 1001), passwordless sudo

**Why this VM works:**
- `/dev/udmabuf` present — lets SurfaceFlinger allocate DMA-BUF without a real GPU
- `binder_linux` kernel module available
- x86_64 required (ARM64 VMs cannot run Waydroid — virgl dependency)
- Ubuntu 25.10 "Questing" has the right kernel version for waydroid 1.6.x

### Oracle VPS — Database only

- **IP:** `150.136.40.239`
- **SSH:** `ssh -i ~/.ssh/id_rsa ubuntu@150.136.40.239`
- **PostgreSQL port 5432 is publicly accessible**
- DB: `tiktoks`, User: `app1_user`, Password: `app1dev`
- Connect: `PGPASSWORD=app1dev psql -U app1_user -h 150.136.40.239 -d tiktoks`

### Database Schema

```
authors        — TikTok user profiles (uid PK, unique_id, nickname, follower_count, ...)
videos         — full video metadata + stats (aweme_id PK, FK→authors, likes/views/saves,
                  video URLs, music, scraped_at, stats_updated_at)
searches       — one row per scrape run (keyword, sort_type, searched_at)
search_results — junction: search_id + video_id + position
```

`db.py` connects directly using `app1_user`/`app1dev`. Override with env var:
```bash
TIKTOKS_DATABASE_URL="postgresql://app1_user:app1dev@150.136.40.239:5432/tiktoks" python3 ...
```

---

## GCP2 From-Scratch Setup (one-time)

Only needed when provisioning a new VM. GCP2 is already set up. Document this in case
the VM is rebuilt.

### 1. Install packages

```bash
curl -s https://repo.waydro.id | sudo bash
sudo apt-get install -y waydroid sway wayvnc socat python3-pip git lxc-utils
```

**Why sway, not weston:** weston's headless backend doesn't support the virtual pointer
protocol that wayvnc needs. Without it, mouse/keyboard input doesn't reach Android — you
can't click captchas or interact with TikTok. Sway supports the protocol.

Waydroid version: **1.6.2**

### 2. Kernel modules and device permissions

```bash
sudo modprobe binder_linux
sudo chmod 666 /dev/udmabuf
```

### 3. Initialize Waydroid (downloads ~3 GB Android images)

```bash
sudo waydroid init -s GAPPS
```

Downloads to `/var/lib/waydroid/images/`:
- `system.img` — LineageOS 20, Android 13, x86_64, GAPPS build
- `vendor.img`

Creates LXC config at `/var/lib/waydroid/lxc/waydroid/`.

### 4. Patch LXC config for udmabuf

Append to `/var/lib/waydroid/lxc/waydroid/config_nodes`:

```
lxc.mount.entry = /dev/udmabuf dev/udmabuf none bind,create=file,optional 0 0
```

Note: do NOT try to bind-mount the mitmproxy CA cert into `/system/etc/security/cacerts/`
here — Android's init remounts `/system` from system.img after LXC sets up the rootfs,
hiding the bind mount. Install the cert via lxc-attach instead (see First Boot).

### 5. Install libhoudini (ARM translation layer)

Required to run arm64 APKs (like TikTok Lite) on an x86_64 Android image.

```bash
cd /tmp
git clone https://github.com/casualsnek/waydroid_script
cd waydroid_script
sudo pip3 install tqdm InquirerPy --break-system-packages -q
sudo python3 main.py install libhoudini
```

After install, Android reports: `x86_64,x86,arm64-v8a,armeabi-v7a,armeabi`

### 6. Install the startup script

The script lives locally at `emulator/waydroid-start.sh`. Copy it to the VM:

```bash
scp -i ~/.ssh/jamescvermont emulator/waydroid-start.sh jamescvermont@34.153.25.251:/tmp/
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 \
    "sudo cp /tmp/waydroid-start.sh /usr/local/bin/waydroid-start.sh && sudo chmod +x /usr/local/bin/waydroid-start.sh"
```

### 7. First boot — push mitmproxy CA cert and authorize ADB

Run the startup script first (see "Every Boot" below), then once ADB is connected:

**Push mitmproxy CA cert as a SYSTEM cert via bind mount:**

The user CA approach (`/data/misc/user/0/cacerts-added/`) does NOT work. TikTok targets
SDK 35 and its APK (including `base.objection.apk`, which is byte-for-byte identical to
`base.apk`) only trusts system CAs. The fix is to bind-mount a patched cacerts directory.

This is now automated in `waydroid-start.sh` — it runs on every boot. But you must first
push the cert to `/sdcard/`:

```bash
# Generate cert if it doesn't exist yet
mitmdump --listen-port 18888 &; sleep 3; kill %1

# Push cert to Android sdcard (persists in /data across reboots)
adb -s 34.153.25.251:5556 push ~/.mitmproxy/mitmproxy-ca-cert.pem /sdcard/mitmproxy-ca.pem
```

`waydroid-start.sh` then does this automatically on every boot:
```bash
lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
  mkdir -p /tmp/cacerts
  cp /system/etc/security/cacerts/* /tmp/cacerts/
  cp /sdcard/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0
  chmod 644 /tmp/cacerts/c8750f0d.0
  mount --bind /tmp/cacerts /system/etc/security/cacerts
'
```

**NOTE:** `base.objection.apk` is NOT actually patched — it is byte-for-byte identical to
`base.apk`. The system CA bind-mount approach is the correct method. The bind mount
survives until the Waydroid container stops; `waydroid-start.sh` re-applies it on every
start.

**Authorize ADB key** (only needed once per fresh container data directory):

```bash
ADBKEY=$(cat ~/.android/adbkey.pub)
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 "
  sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c \
    'mkdir -p /data/misc/adb && echo \"$ADBKEY\" > /data/misc/adb/adb_keys && chmod 640 /data/misc/adb/adb_keys'
"
```

### 8. Set phone-like screen dimensions (one-time, persists in Android data)

```bash
adb -s 34.153.25.251:5556 shell wm size 720x1612
adb -s 34.153.25.251:5556 shell wm density 280
```

### 9. Install TikTok Lite (objection-patched APK)

APKs are in `apkm_extracted/` in the project root. `base.objection.apk` has SSL pinning
removed and trusts user CAs.

```bash
cd /path/to/project/apkm_extracted
adb -s 34.153.25.251:5556 install-multiple \
  base.objection.apk \
  split_config.arm64_v8a.apk \
  split_config.xhdpi.apk \
  split_config.en.apk \
  split_df_edit_effects.apk \
  split_df_edit_filter.apk \
  split_df_edit_sticker.apk \
  split_df_record_prop.apk \
  split_df_fusing.apk \
  split_post_video.apk
```

Package name: `com.tiktok.lite.go` (not `com.zhiliaoapp.musically.go`)

### 10. Log in to TikTok via VNC

Connect VNC to `34.153.25.251:5900` (no password). Open TikTok Lite on the home screen
and log in. Expect a slider captcha — click and drag. Mouse input works because sway
supports the virtual pointer protocol (weston does not).

---

## Every Boot — Starting the Stack

After any VM reboot, run:

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251
sudo bash /usr/local/bin/waydroid-start.sh
```

The script does:
1. Mount binderfs (`/dev/binderfs`)
2. Fix udmabuf permissions
3. Start sway headless compositor (wayland-1 socket)
4. Start `waydroid-container` systemd service (LXC container)
5. Start `waydroid session` daemon (with correct `WAYLAND_DISPLAY=wayland-1`)
6. Wait up to 3 min for Container RUNNING
7. Start wayvnc on port 5900
8. Wait up to 2 min for adbd on port 5555
9. Create socat ADB proxy (port 5556 → container 5555 via nsenter)
10. Run `waydroid show-full-ui` — **after** adbd is ready, so Android userspace is fully
    booted and the `waydroidplatform` binder service is registered

**Critical timing note:** show-full-ui must run AFTER Android is fully booted. If it runs
too early, it fails with "Failed to get service waydroidplatform". The script handles this
by waiting for adbd first. If show-full-ui still fails (VNC blank after script completes),
wait ~30s and run it manually:

```bash
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 WAYLAND_DISPLAY=wayland-1 \
    waydroid show-full-ui
```

This exits with RC 0 (no output) when it succeeds — it's a one-shot command, not a daemon.
Verify with `swaymsg -t get_tree` that the Waydroid window is present.

---

## Connecting from WSL2

After the startup script completes:

```bash
# Connect ADB
adb kill-server
adb connect 34.153.25.251:5556
adb -s 34.153.25.251:5556 shell getprop sys.boot_completed   # must be 1
adb -s 34.153.25.251:5556 shell getprop ro.product.cpu.abilist  # must include arm64-v8a

# Set up mitmproxy tunnel
adb -s 34.153.25.251:5556 reverse tcp:8080 tcp:8080
```

**VNC:** Connect TigerVNC or RealVNC to `34.153.25.251:5900`, no password.

---

## Running the Scraper

All scraper scripts live in `emulator/`. Run them from the project root:

```bash
# Single keyword
python3 emulator/mobile_scrape.py "gym motivation" --scrolls 30

# Batch from queue (recommended — one TikTok session for N terms)
python3 emulator/batch_scrape.py --n 10

# Queue management
python3 emulator/queue.py stats
python3 emulator/queue.py import emulator/search_terms.txt search
python3 emulator/queue.py reset    # unstick crashed in_progress terms
```

The scraper uses `adb -s 34.153.25.251:5556` as the device target (configured in
`mobile_scrape.py`). Adjust if using a different ADB target.

---

## Key Files

| File | Location | Purpose |
|---|---|---|
| `waydroid-start.sh` | `emulator/` (local), `/usr/local/bin/` (GCP2) | Full stack startup |
| `mobile_scrape.py` | `emulator/` | Single keyword scrape |
| `batch_scrape.py` | `emulator/` | Batch scrape from queue |
| `queue.py` | `emulator/` | SQLite queue manager |
| `db.py` | `emulator/` | PostgreSQL models + upserts |
| `tt_dump.py` | `emulator/` | mitmproxy addon (written to /tmp at runtime) |
| `search_terms.txt` | `emulator/` | ~3,524 terms across 35+ categories |
| `base.objection.apk` | `apkm_extracted/` | Patched TikTok Lite (SSL pinning off) |

---

## How the Scraper Works (mobile_scrape.py)

1. Writes `tt_dump.py` mitmproxy addon to disk, starts `mitmdump` on port 8080
2. Sets Android proxy: `adb shell settings put global http_proxy 127.0.0.1:8080`
   (tunneled into Android via `adb reverse tcp:8080 tcp:8080`)
3. Force-stops and relaunches `com.tiktok.lite.go`
4. UI automation tap sequence:
   - Search icon → search field → type keyword → Search button
   - Filter icon → "Like count" → Apply
5. Scrolls in batches of 30; stops when ≥3 of last 10 captured videos have <7k likes,
   or no new content loads
6. mitmproxy intercepts `/aweme/v1/general/search/single/` responses, writes to
   `/tmp/tt_{keyword}_1.jsonl`
7. Deduplicates, drops videos under 1k likes, batch-upserts to DB

`batch_scrape.py` reuses one mitmproxy + TikTok launch across all terms (only the search
field is re-used, not the full app launch).

---

## ADB Screen Coordinates (720×1612 @ 280dpi)

Verified by screenshot analysis on 2026-04-20:

```python
SEARCH_ICON      = (651, 97)    # magnifying glass top-right (FYP screen)
SEARCH_FIELD     = (325, 91)    # search input field
SEARCH_CLEAR_BTN = (533, 91)    # X to clear field
SEARCH_BTN       = (633, 91)    # pink Search button
FILTER_ICON      = (700, 173)   # filter/slider icon (right of tab bar)
LIKE_COUNT_BTN   = (315, 1420)  # "Like count" in filter popup Sort by row
APPLY_BTN        = (670, 778)   # Apply button in filter popup header
```

**Filter popup layout** (panel bounds `[0,722][720,1528]`):
- Header (Cancel / Filters / Apply): y ≈ 722–790
- Category buttons: y ≈ 820–890
- Date posted buttons: y ≈ 920–1220
- Sort by label + buttons: y ≈ 1360–1470

If layout shifts (TikTok update): `adb shell screencap -p /sdcard/screen.png && adb pull /sdcard/screen.png`

---

## Critical Quirks

**Multi-word keywords via ADB input:**
`adb shell input text` drops characters if the string is sent all at once on Waydroid
(emulator is slower than a real phone). Must type character-by-character with a delay:
```python
for ch in keyword:
    if ch == ' ':
        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_SPACE"], capture_output=True)
    else:
        subprocess.run(["adb", "shell", "input", "text", ch], capture_output=True)
    time.sleep(0.15)
```
Do NOT use `shlex.quote()` or `%s` — both cause character drops on Waydroid.

**mitmproxy endpoint:**
- Path: `/aweme/v1/general/search/single/`
- Response key: `data[].aweme_info` (not `aweme_list` or `item_list`)
- `sort_type=1` = most liked, `sort_type=rel` = relevance

**Proxy cleanup:**
Always call `clear_proxy()` after scraping. If proxy isn't cleared and mitmproxy isn't
running, Android loses internet connectivity.

---

## Local WSL2 Setup (Python dependencies)

Run `setup.sh` from the project root, or manually:

```bash
sudo apt-get install -y adb psmisc curl wget unzip
pip3 install --break-system-packages mitmproxy sqlalchemy psycopg2-binary yt-dlp
```

Verify mitmproxy cert exists (generate by running mitmdump once):
```bash
mitmdump --listen-port 18888 &; sleep 3; kill %1
ls ~/.mitmproxy/mitmproxy-ca-cert.pem
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| VNC blank / black screen | `waydroid show-full-ui` (RC 0 = success, Waydroid window appears in sway) |
| `Failed to get service waydroidplatform` | Android not fully booted — wait for `adb shell getprop sys.boot_completed` = 1, then retry show-full-ui |
| `adb: device unauthorized` | Push ADB key via lxc-attach (Step 7 above) |
| Container keeps stopping | Re-run `sudo bash /usr/local/bin/waydroid-start.sh` |
| `socat: Address already in use` | `sudo pkill socat; sleep 1` then re-run startup script |
| sway socket never appears | Check `/tmp/sway.log`; `pkill sway && pkill waydroid` and retry |
| wayvnc: `Virtual Pointer not supported` | You're using weston — switch to sway |
| `adb connect` refused | socat proxy not running; re-run startup script |
| Scraper sees no videos | Check mitmproxy is capturing: run `mitmdump` in terminal and open TikTok manually; confirm path is `/aweme/v1/general/search/single/` |
| ADB input text truncated | Use char-by-char typing with 0.15s delay — see Critical Quirks above |
| TikTok: No internet connection | mitmproxy cert not mounted — run cert bind-mount command (Step 7), or re-run `waydroid-start.sh` |
| TikTok opens Google Search or wrong app | `monkey` command misfires on Waydroid — use `am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity` |
| Cert bind-mount lost after reboot | `waydroid-start.sh` re-applies it automatically; or run Step 7 bind-mount manually |

## Key Logs on GCP2

| File | Contents |
|---|---|
| `/tmp/sway.log` | Wayland compositor (check if sway crashes) |
| `/tmp/waydroid_session.log` | Session daemon (look for "Android with user 0 is ready") |
| `/tmp/waydroid_ui.log` | show-full-ui ("Failed to get service" = too early, RC 0 exit = success) |
| `/tmp/wayvnc.log` | VNC server |
| `/tmp/socat_adb.log` | ADB proxy |
| `journalctl -u waydroid-container` | LXC container lifecycle |

## Checking State Without VNC

```bash
# Is Android up?
adb -s 34.153.25.251:5556 shell getprop sys.boot_completed   # 1 = booted

# Is the Waydroid window in sway?
SWAYSOCK=$(ls /run/user/1001/sway-ipc.* 2>/dev/null | head -1)
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 WAYLAND_DISPLAY=wayland-1 \
    SWAYSOCK=$SWAYSOCK swaymsg -t get_tree | python3 -c \
    "import json,sys; t=json.load(sys.stdin); print([n.get('name') for n in t['nodes'][0]['nodes'][0]['nodes']])"

# Full status
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 waydroid status
```
