# TikTok Scraper — GCP VM Setup Guide

This document is the single source of truth for provisioning a new GCP Waydroid VM and
getting a scraper instance running. Every GCP VM in this fleet is identical in structure
and environment. A fresh Claude instance handed this file and a clean Ubuntu VM should
be able to reach a working scraper with no external help.

---

## What This Project Does

Scrapes TikTok's **mobile API** for all-time most-liked videos by keyword. The mobile API
(intercepted via mitmproxy) returns the full historical catalog sorted by likes — the web
API only returns recent content, missing older viral videos.

**Scrape flow:**
1. mitmproxy runs on the GCP VM itself, listening on port 8080
2. Android proxy is set to tunnel through mitmproxy via `adb reverse`
3. ADB UI-automates TikTok Lite: search → sort by likes → scroll
4. mitmproxy intercepts `/aweme/v1/general/search/single/` responses, writes to `/tmp/tt_<keyword>_1.jsonl`
5. Results are deduplicated and batch-upserted to PostgreSQL on the Oracle VPS

The scraper runs entirely **on the GCP VM**. Multiple VMs pull from the same shared
PostgreSQL queue (`terms` table) concurrently — `FOR UPDATE SKIP LOCKED` ensures no two
VMs grab the same term.

---

## Infrastructure

### GCP VMs — Waydroid fleet

All VMs are provisioned identically. Current fleet:

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | Active — scraping |
| GCP3 | `34.59.191.130` | Pending provisioning — clean Ubuntu 25.10, packages not yet installed |

To provision GCP3: follow all steps in this document top to bottom, substituting `34.59.191.130` for `<VM_IP>`. Use the session transfer (Step 10 Option A) to copy TikTok login from GCP2.

**Required VM specs:**
- **Provider:** Google Cloud (any region)
- **Machine:** e2-standard-2 (2 vCPU, 4 GB RAM), x86_64
- **OS:** Ubuntu 25.10 (Questing), kernel 6.17.x-gcp
- **User:** `jamescvermont` (UID 1001), passwordless sudo

**Why these specs are required:**
- `/dev/udmabuf` present — SurfaceFlinger needs this for DMA-BUF without a real GPU
- `binder_linux` kernel module available
- x86_64 **required** — ARM64 VMs cannot run Waydroid (virgl dependency)
- Ubuntu 25.10 Questing has the right kernel version for waydroid 1.6.x

SSH access: `ssh -i ~/.ssh/jamescvermont jamescvermont@<VM_IP>`

### Oracle VPS — Database + Queue

- **IP:** `150.136.40.239`
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
terms          — scrape queue (term, type, status, videos_saved, ...) — shared across all VMs
```

---

## Provisioning a New GCP VM (one-time, ~30 min)

### 1. Install packages

```bash
curl -s https://repo.waydro.id | sudo bash
sudo apt-get install -y waydroid sway wayvnc socat python3-pip git lxc adb psmisc
pip3 install --break-system-packages mitmproxy sqlalchemy psycopg2-binary
```

**Note:** On Ubuntu 25.10 the package is `lxc` (not `lxc-utils` — that name was retired).

**Why sway, not weston:** weston's headless backend doesn't support the virtual pointer
protocol that wayvnc needs. Without it, mouse/keyboard input doesn't reach Android.
Sway supports the protocol.

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

**Note:** do NOT try to bind-mount the mitmproxy CA cert here — Android's init remounts
`/system` from system.img after LXC sets up the rootfs, hiding the bind mount. Install
the cert via lxc-attach instead (see Step 7).

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

The script lives in the repo at `waydroid-start.sh`. Copy it to the system:

```bash
sudo cp ~/tiktok-scraper/waydroid-start.sh /usr/local/bin/waydroid-start.sh
sudo chmod +x /usr/local/bin/waydroid-start.sh
```

### 7. First boot — push mitmproxy CA cert and authorize ADB

Run the startup script first (see "Every Boot" section below), then once ADB is available:

**Generate mitmproxy cert if not already done:**
```bash
mitmdump --listen-port 18888 &; sleep 3; kill %1
# Creates ~/.mitmproxy/mitmproxy-ca-cert.pem
```

**Generate mitmproxy's CA cert on this VM** (each VM must generate its own — certs are not transferable):
```bash
PATH=$PATH:$HOME/.local/bin
mitmdump --listen-port 18888 &; sleep 4; kill %1
ls ~/.mitmproxy/mitmproxy-ca-cert.pem   # confirm it exists
```

**Push cert to Android sdcard (persists in /data across reboots):**
```bash
adb -s 127.0.0.1:5556 push ~/.mitmproxy/mitmproxy-ca-cert.pem /sdcard/mitmproxy-ca.pem
```

`waydroid-start.sh` then bind-mounts this cert as a system CA on every boot:
```bash
lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
  mkdir -p /tmp/cacerts
  cp /system/etc/security/cacerts/* /tmp/cacerts/
  cp /sdcard/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0
  chmod 644 /tmp/cacerts/c8750f0d.0
  mount --bind /tmp/cacerts /system/etc/security/cacerts
'
```

**Why system CA is required:** TikTok targets SDK 35 and ignores user CA store
(`/data/misc/user/0/cacerts-added/`). The `base.objection.apk` in the repo is NOT
actually patched — it is byte-for-byte identical to `base.apk`. The bind-mount is
the only method that works.

**Authorize ADB key** (only needed once per fresh container data directory):
```bash
ADBKEY=$(cat ~/.android/adbkey.pub)
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c \
    "mkdir -p /data/misc/adb && echo \"$ADBKEY\" > /data/misc/adb/adb_keys && chmod 640 /data/misc/adb/adb_keys"
```

### 8. Set phone-like screen dimensions (one-time, persists in Android data)

```bash
adb -s 127.0.0.1:5556 shell wm size 720x1612
adb -s 127.0.0.1:5556 shell wm density 280
```

**Note on ADB port:** even when running commands on the VM itself, use port **5556** (the
socat proxy), not 5555. The socat proxy (started by `waydroid-start.sh`) listens on 5556
and forwards into the container. Direct port 5555 may not be accessible without nsenter.
Set `ADB_DEVICE=127.0.0.1:5556` in `.env`.

All UI coordinates in `mobile_scrape.py` are calibrated for 720×1612 @ 280dpi.

### 9. Install TikTok Lite

APKs are in `apkm_extracted/` in the repo root:

```bash
cd ~/tiktok-scraper/apkm_extracted
adb -s 127.0.0.1:5556 install-multiple \
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

Package name: `com.tiktok.lite.go`

### 10. Log in to TikTok

**Option A — Transfer session from an existing VM (preferred, no captcha)**

GCP2 is the reference VM with a working logged-in TikTok session. Copy its app data
to the new VM's Android container. Do this AFTER Waydroid is fully booted on the new VM.

```bash
# On GCP2: export TikTok's app data directory
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 \
  "sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
   tar -czf /tmp/tiktok_session.tar.gz -C /data/data com.tiktok.lite.go"

# Copy the archive from GCP2 to local, then to the new VM
scp -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251:/tmp/tiktok_session.tar.gz /tmp/
scp -i ~/.ssh/jamescvermont /tmp/tiktok_session.tar.gz jamescvermont@<NEW_VM_IP>:/tmp/

# On the new VM: force-stop TikTok, restore app data, fix permissions
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> "
  sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
    am force-stop com.tiktok.lite.go
    rm -rf /data/data/com.tiktok.lite.go
    tar -xzf /tmp/tiktok_session.tar.gz -C /data/data
    chown -R 10145:10145 /data/data/com.tiktok.lite.go
    chmod -R 771 /data/data/com.tiktok.lite.go
  '
"
```

Launch TikTok via VNC to confirm it opens directly to the feed (no login prompt).
If it asks to log in again, the session expired — fall back to Option B.

**Option B — Manual login via VNC (fallback)**

Connect VNC to `<VM_IP>:5900` (no password). Open TikTok Lite and log in manually.
Expect a slider captcha — click and drag. Mouse input works because sway supports
the virtual pointer protocol (weston does not).

### 11. Set up ntfy notifications (one-time, on your phone)

ntfy.sh is a free push notification service — no account required. The scraper
posts to it when it stops, fails, or sends a heartbeat.

**On your phone:**
1. Install the ntfy app — [Android (Play Store)](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [iOS (App Store)](https://apps.apple.com/app/ntfy/id1625396347)
2. Open the app → tap **+** → enter topic: `retardedjames-tiktok` → Subscribe
3. That's it. No login, no account.

You can also check notifications in a browser at: `https://ntfy.sh/retardedjames-tiktok`

**Notification events:**
| Event | Priority |
|---|---|
| Scraper started | Low (silent) |
| 0 results warning (1 of 2) | Default |
| Scraper stopped — 2 consecutive failures | **High (buzzes)** |
| Heartbeat every 50 terms | Low (silent) |
| Scraper stopped cleanly | Low (silent) |

The topic name and VM label are set in `.env` — see Step 12.

### 12. Deploy the repo and configure .env

GitHub auth is not configured on GCP VMs — use rsync from WSL2 to push the code:

```bash
# Run from WSL2, not on the GCP VM
rsync -av -e "ssh -i ~/.ssh/jamescvermont" \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='queue.db' --exclude='.env' \
  /home/james/tiktok-scraper/ jamescvermont@<VM_IP>:~/tiktok-scraper/
```

Then on the VM, create `.env`:
```bash
cat > ~/tiktok-scraper/.env << 'EOF'
ADB_DEVICE=127.0.0.1:5556
VM_NAME=GCP3
NTFY_TOPIC=retardedjames-tiktok
NTFY_PER_TERM=0
EOF
```

Change `VM_NAME` to match this VM. To push code updates later, re-run the same rsync.

---

## Every Boot — Starting the Waydroid Stack

After any VM reboot:

```bash
sudo bash /usr/local/bin/waydroid-start.sh
```

The script does:
1. Mount binderfs (`/dev/binderfs`)
2. Fix udmabuf permissions (`chmod 666`)
3. Start sway headless compositor (wayland-1 socket)
4. Start `waydroid-container` systemd service (LXC container)
5. Start `waydroid session` daemon (with `WAYLAND_DISPLAY=wayland-1`)
6. Wait up to 3 min for container RUNNING state
7. Start wayvnc on port 5900
8. Wait up to 2 min for adbd on port 5555
9. Create socat ADB proxy (port 5556 → container 5555 via nsenter)
10. Run `waydroid show-full-ui` — after adbd is ready, so Android userspace is fully booted

**If VNC is blank after script completes**, wait ~30s and run:
```bash
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 WAYLAND_DISPLAY=wayland-1 \
    waydroid show-full-ui
```
RC 0 + no output = success. Verify with: `swaymsg -t get_tree`

**Verify boot:**
```bash
adb -s 127.0.0.1:5556 shell getprop sys.boot_completed        # must be 1
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
    ls /system/etc/security/cacerts/c8750f0d.0                 # cert must exist
```

**Important — cert is VM-specific:** `waydroid-start.sh` bind-mounts whatever cert is at
`/sdcard/mitmproxy-ca.pem`. Each VM generates its own mitmproxy cert (Step 7) and pushes
it there. You cannot copy a cert from another VM — mitmproxy uses the matching private key
from `~/.mitmproxy/mitmproxy-ca.pem` to decrypt traffic. If cert and key don't match,
TikTok shows "No internet connection" and mitmdump logs show TLS handshake failures.

---

## Running the Scraper

### Production (continuous)

```bash
cd ~/tiktok-scraper
bash run.sh >> /tmp/scraper.log 2>&1 &
tail -f /tmp/scraper.log
```

`run.sh` does `git pull --ff-only` before starting, so VMs always run the latest code
on each restart. If the scraper stops due to failures, `SCRAPER_STATUS.txt` will explain why.

### Test run (N terms then stop)

```bash
cd ~/tiktok-scraper
python3 batch_scrape.py --n 3
```

### Queue management (from any machine with PostgreSQL access)

```bash
python3 queue.py stats
python3 queue.py reset          # unstick in_progress terms after a crash
python3 queue.py reset-all      # reset ALL terms → pending (fresh start)
python3 queue.py import search_terms.txt search   # add new terms
```

---

## Key Files

| File | Purpose |
|---|---|
| `scrape_forever.py` | Production continuous runner |
| `batch_scrape.py` | Test/dev: runs N terms then stops |
| `mobile_scrape.py` | Core scraper: mitmproxy, ADB automation, scroll logic |
| `queue.py` | PostgreSQL queue (FOR UPDATE SKIP LOCKED) |
| `db.py` | PostgreSQL models + upserts |
| `preflight.py` | Pre-run checks (ADB, mitmproxy, proxy) |
| `run.sh` | Boot script: load .env, git pull, start scrape_forever.py |
| `.env.example` | Config template — copy to `.env` on each VM |
| `waydroid-start.sh` | Full Waydroid stack startup (lives at `/usr/local/bin/`) |
| `search_terms.txt` | ~3,524 terms across 35+ categories |
| `apkm_extracted/` | TikTok Lite APK splits |
| `SCRAPER_STATUS.txt` | Written on failure — check this if scraper stopped |

---

## How the Scraper Works

1. Writes `tt_dump.py` mitmproxy addon to disk, starts `mitmdump` on port 8080
2. Sets Android proxy: `adb shell settings put global http_proxy 127.0.0.1:8080`
   (tunneled into Android via `adb reverse tcp:8080 tcp:8080`)
3. Force-stops and relaunches `com.tiktok.lite.go`
4. UI automation tap sequence:
   - Search icon → search field → type keyword → Search button
   - Filter icon → "Like count" → Apply
5. `scroll_smart` scrolls in batches of 10; stops when ≥3 of the last 10 captured
   videos have <5,000 likes, or no new content loads
6. mitmproxy intercepts `/aweme/v1/general/search/single/` responses, writes to
   `/tmp/tt_{keyword}_1.jsonl`
7. Deduplicates, drops videos under 1k likes, batch-upserts to PostgreSQL

`scrape_forever.py` reuses one mitmproxy + TikTok launch for the entire run — only the
search field is re-navigated between terms.

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

If layout shifts after a TikTok update:
```bash
adb -s 127.0.0.1:5556 shell screencap -p /sdcard/screen.png
adb -s 127.0.0.1:5556 pull /sdcard/screen.png /tmp/screen.png
# Or use --debug flag: python3 batch_scrape.py --n 1 --debug
```

---

## Critical Quirks

**ADB text input — character-by-character:**
`adb shell input text` drops characters if sent all at once on Waydroid. Must type
one character at a time with 0.15s delay:
```python
for ch in keyword:
    if ch == ' ':
        subprocess.run(["adb", "shell", "input", "keyevent", "KEYCODE_SPACE"], ...)
    else:
        subprocess.run(["adb", "shell", "input", "text", ch], ...)
    time.sleep(0.15)
```

**Foreground app detection:**
Use `dumpsys window | grep mCurrentFocus` — NOT `dumpsys activity top`. The activity
command returns background entries and always shows `com.android.launcher3` as
"foreground" on this Waydroid setup, causing constant spurious TikTok relaunches.

**ADB device address on the VM:**
- Local (on the VM): `ADB_DEVICE=127.0.0.1:5555`
- Remote (from WSL2 for debugging): `ADB_DEVICE=<VM_IP>:5556`
- The socat proxy on :5556 is only needed for external access.

**mitmproxy endpoint:**
- Path: `/aweme/v1/general/search/single/`
- Response key: `data[].aweme_info` (not `aweme_list` or `item_list`)
- `sort_type=1` = most liked

**Proxy cleanup:**
Always call `clear_proxy()` after scraping. If proxy isn't cleared and mitmproxy isn't
running, Android loses internet connectivity.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| VNC blank / black screen | `waydroid show-full-ui` (RC 0 = success) |
| `Failed to get service waydroidplatform` | Android not fully booted — wait for `boot_completed=1`, retry show-full-ui |
| `adb: device unauthorized` | Push ADB key via lxc-attach (Step 7 above) |
| Container keeps stopping | Re-run `sudo bash /usr/local/bin/waydroid-start.sh` |
| `socat: Address already in use` | `sudo pkill socat; sleep 1` then re-run startup script |
| sway socket never appears | Check `/tmp/sway.log`; `pkill sway && pkill waydroid` and retry |
| wayvnc: `Virtual Pointer not supported` | You're using weston — switch to sway |
| `adb connect` refused | socat proxy not running; re-run startup script |
| Scraper captures 0 videos | Check mitmproxy: `tail /tmp/mitmdump.log`; confirm cert mounted; confirm sort filter applied |
| ADB input text truncated | Char-by-char typing — see Critical Quirks above |
| TikTok: No internet connection | Cert not mounted — re-run `waydroid-start.sh` or run bind-mount manually (Step 7) |
| TikTok app never reaches search screen | Check `SCRAPER_STATUS.txt`; run with `--debug` to get screenshots |
| Scraper types into wrong app | `_foreground_app()` is returning wrong value — check `dumpsys window` output |

---

## Key Logs

| File | Contents |
|---|---|
| `/tmp/scraper.log` | Main scraper output (if started with `bash run.sh >> /tmp/scraper.log 2>&1`) |
| `SCRAPER_STATUS.txt` | Written by `scrape_forever.py` on start/stop/failure |
| `/tmp/mitmdump.log` | mitmproxy intercept log (confirm traffic is being captured) |
| `/tmp/sway.log` | Wayland compositor |
| `/tmp/waydroid_session.log` | Session daemon (look for "Android with user 0 is ready") |
| `/tmp/wayvnc.log` | VNC server |
| `/tmp/socat_adb.log` | ADB proxy |
| `journalctl -u waydroid-container` | LXC container lifecycle |

## Checking State Without VNC

```bash
# Is Android up?
adb -s 127.0.0.1:5556 shell getprop sys.boot_completed         # 1 = booted

# Is TikTok in the foreground?
adb -s 127.0.0.1:5556 shell dumpsys window | grep mCurrentFocus

# Is the cert mounted?
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
    ls /system/etc/security/cacerts/c8750f0d.0

# Full Waydroid status
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 waydroid status

# Queue state
PGPASSWORD=app1dev psql -U app1_user -h 150.136.40.239 -d tiktoks \
    -c "SELECT status, COUNT(*) FROM terms GROUP BY status;"
```
