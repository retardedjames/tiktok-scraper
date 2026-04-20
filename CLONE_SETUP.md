# TikTok Scraper — VM Operations Guide

**This is the primary reference for all new VMs.** New VMs are created by cloning a
machine image of GCP2 (or any healthy fleet VM) in the GCP Console — not by provisioning
from scratch. See `WAYDROID_GCP2_SETUP.md` if you need to understand how the original VM
was built, but you will not follow those steps again.

---

## What This Project Does

Scrapes TikTok's **mobile API** for all-time most-liked videos by keyword. The mobile API
(intercepted via mitmproxy) returns the full historical catalog sorted by likes — the web
API only returns recent content.

**Scrape flow:**
1. mitmproxy runs on the GCP VM, listening on port 8080
2. Android proxy tunnels through mitmproxy via `adb reverse`
3. ADB UI-automates TikTok Lite: search → sort by likes → scroll
4. mitmproxy intercepts `/aweme/v1/general/search/single/` responses
5. Results are deduplicated and upserted to PostgreSQL on the Oracle VPS

Multiple VMs pull from the same shared queue (`terms` table, `FOR UPDATE SKIP LOCKED`).

---

## Infrastructure

### GCP VM Fleet

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | Active — reference/source image |

SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<VM_IP>`
VNC: `<VM_IP>:5900` (no password) — for monitoring/login only

**VM specs (all clones are identical):**
- Machine: e2-standard-2 (2 vCPU, 4 GB RAM), x86_64
- OS: Ubuntu 25.10 (Questing), kernel 6.17.x-gcp
- User: `jamescvermont` (UID 1001), passwordless sudo
- x86_64 required — ARM64 VMs cannot run Waydroid

### Oracle VPS — Database + Queue

- **IP:** `150.136.40.239`, port 5432 (publicly accessible)
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

## Creating a New VM from a Clone (~5 min)

### 1. Take a machine image in GCP Console

Stop the scraper on GCP2 first (clean state):
```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 "kill \$(pgrep -f scrape_forever) 2>/dev/null; echo done"
python3 queue.py reset   # unstick any in_progress terms
```

In GCP Console: **Compute Engine → Storage → Machine images → Create machine image**
→ select source instance → create. Use machine image (not disk snapshot) — it captures
full instance config.

Launch a new VM from the machine image. Same region/zone as source is fine.

### 2. SSH into the new VM and update `.env`

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP>
nano ~/tiktok-scraper/.env
```

Change `VM_NAME` to the new VM's name (e.g. `GCP4`). Everything else stays the same:

```
ADB_DEVICE=127.0.0.1:5556
VM_NAME=GCP4
NTFY_TOPIC=retardedjames-tiktok
NTFY_PER_TERM=0
```

### 3. Pull latest code

```bash
cd ~/tiktok-scraper && git pull
sudo cp waydroid-start.sh /usr/local/bin/waydroid-start.sh
```

### 4. Start Waydroid

```bash
sudo bash /usr/local/bin/waydroid-start.sh
```

Wait for `[*] All done!`. If VNC is blank after, run:
```bash
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 WAYLAND_DISPLAY=wayland-1 \
    waydroid show-full-ui
```

### 5. Verify

```bash
adb connect 127.0.0.1:5556
adb -s 127.0.0.1:5556 shell getprop sys.boot_completed          # must be 1
adb -s 127.0.0.1:5556 shell pm list packages | grep tiktok      # must appear
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
    ls /system/etc/security/cacerts/c8750f0d.0                   # cert must exist
```

Open VNC (`<NEW_VM_IP>:5900`) and launch TikTok — should open to the feed with no login
prompt. If it asks to log in, the session expired; see **Session Transfer** below.

### 6. Start the scraper

```bash
cd ~/tiktok-scraper
bash run.sh >> /tmp/scraper.log 2>&1 &
tail -f /tmp/scraper.log
```

---

## What the Clone Inherits (no action needed)

| Item | Notes |
|---|---|
| Waydroid + Android images | Inherited |
| libhoudini (ARM translation) | Inherited |
| TikTok Lite APK | Inherited |
| TikTok session (logged in) | Inherited — cert + private key are a matched pair |
| mitmproxy cert + private key | **Do not regenerate** — both come from source, they match |
| ADB key (authorized in Android) | Inherited |
| Screen dimensions (720×1612 @ 280dpi) | Inherited |
| Python packages (mitmproxy, sqlalchemy, etc.) | Inherited |

---

## Every Boot — Starting the Waydroid Stack

Waydroid does not autostart. After any VM boot (including first launch of a clone):

```bash
sudo bash /usr/local/bin/waydroid-start.sh
```

The script does:
1. Mount binderfs (`/dev/binderfs`)
2. Fix udmabuf permissions (`chmod 666`)
3. Start sway headless compositor (wayland-1 socket)
4. Start `waydroid-container` systemd service (LXC container)
5. Start `waydroid session` daemon
6. Wait up to 3 min for container RUNNING state
7. Start wayvnc on port 5900
8. Wait up to 2 min for adbd on port 5555
9. Create socat ADB proxy (port 5556 → container 5555 via nsenter)
10. Bind-mount mitmproxy cert as system CA
11. Run `waydroid show-full-ui`

**If VNC is blank after script completes**, wait ~30s and run:
```bash
sudo -u jamescvermont env XDG_RUNTIME_DIR=/run/user/1001 WAYLAND_DISPLAY=wayland-1 \
    waydroid show-full-ui
```

**Verify boot:**
```bash
adb -s 127.0.0.1:5556 shell getprop sys.boot_completed        # must be 1
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
    ls /system/etc/security/cacerts/c8750f0d.0                 # cert must exist
```

---

## Running the Scraper

### Production (continuous)

```bash
cd ~/tiktok-scraper
bash run.sh >> /tmp/scraper.log 2>&1 &
tail -f /tmp/scraper.log
```

`run.sh` does `git pull --ff-only` before each start. If the scraper stops due to
failures, `SCRAPER_STATUS.txt` explains why and you'll get an ntfy push notification.

### Test run (N terms then stop)

```bash
cd ~/tiktok-scraper
PATH=$PATH:$HOME/.local/bin python3 batch_scrape.py --n 3
```

### Queue management (from any machine with PostgreSQL access)

```bash
python3 queue.py stats
python3 queue.py reset          # unstick in_progress terms after a crash/clone
python3 queue.py reset-all      # reset ALL terms → pending (fresh start)
python3 queue.py import search_terms.txt search   # add new terms
```

---

## Session Transfer (if TikTok asks to log in)

Clone sessions can expire. Transfer a fresh session from a running VM:

```bash
# Step 1: export from source VM (pipe to host /tmp — NOT inside lxc-attach)
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 \
  "sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
   tar -czf - -C /data/data com.tiktok.lite.go | sudo tee /tmp/tiktok_session.tar.gz > /dev/null"

# Step 2: copy via WSL2
scp -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251:/tmp/tiktok_session.tar.gz /tmp/
scp -i ~/.ssh/jamescvermont /tmp/tiktok_session.tar.gz jamescvermont@<NEW_VM_IP>:/tmp/

# Step 3: restore on new VM (Waydroid must be running)
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

Verify via VNC — TikTok should open to the feed. If it still asks to log in, the session
has expired entirely; log in manually via VNC (slider captcha, click and drag).

**Note:** `/tmp` inside `lxc-attach` refers to the container's `/tmp`. Always pipe to the
host via `tee` as shown above — writing `-czf /tmp/...` directly creates the file inside
the container where scp can't reach it.

---

## ntfy Notifications

The scraper posts push notifications to ntfy.sh topic `retardedjames-tiktok`.

To receive them: install the ntfy app → tap **+** → topic: `retardedjames-tiktok`.
Or check in browser: `https://ntfy.sh/retardedjames-tiktok`

| Event | Priority |
|---|---|
| Scraper started | Low (silent) |
| 0 results warning (1 of 2) | Default |
| Scraper stopped — 2 consecutive failures | **High (buzzes)** |
| Heartbeat every 50 terms | Low (silent) |
| Scraper stopped cleanly | Low (silent) |

Set `NTFY_PER_TERM=1` in `.env` to get a notification on every term (noisy — off by default).

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
| `run.sh` | Boot wrapper: load .env, git pull, start scrape_forever.py |
| `.env.example` | Config template |
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
# Or: python3 batch_scrape.py --n 1 --debug
```

---

## Critical Quirks

**ADB text input — character-by-character:**
`adb shell input text` drops characters if sent all at once on Waydroid. The scraper
types one character at a time with 0.15s delay — do not change this.

**Foreground app detection:**
Use `dumpsys window | grep mCurrentFocus` — NOT `dumpsys activity top`. The activity
command always returns `com.android.launcher3` as foreground on this Waydroid setup,
causing constant spurious TikTok relaunches.

**ADB port is always 5556:**
Even when running ADB commands locally on the VM, use `127.0.0.1:5556` (the socat proxy),
not 5555. Direct port 5555 is inside the container's network namespace and not reachable
without nsenter. `.env` sets `ADB_DEVICE=127.0.0.1:5556`.

**mitmproxy endpoint:**
- Path: `/aweme/v1/general/search/single/`
- Response key: `data[].aweme_info` (not `aweme_list` or `item_list`)
- `sort_type=1` = most liked

**Proxy cleanup:**
`clear_proxy()` is called automatically after each term. If mitmproxy dies mid-run and
the proxy isn't cleared, Android loses internet on next scrape.

**mitmproxy cert — do not copy or regenerate on a clone:**
The cert in Android's system CA store and the private key in `~/.mitmproxy/` are a matched
pair from the source VM. If they're out of sync, TikTok shows "No internet connection"
and mitmdump logs TLS handshake failures. Cloning keeps them in sync automatically.

---

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

---

## Key Logs

| File | Contents |
|---|---|
| `/tmp/scraper.log` | Main scraper output |
| `~/tiktok-scraper/SCRAPER_STATUS.txt` | Written by `scrape_forever.py` on start/stop/failure |
| `/tmp/mitmdump.log` | mitmproxy intercept log |
| `/tmp/sway.log` | Wayland compositor |
| `/tmp/waydroid_session.log` | Session daemon (look for "Android with user 0 is ready") |
| `/tmp/wayvnc.log` | VNC server |
| `/tmp/socat_adb.log` | ADB proxy |
| `journalctl -u waydroid-container` | LXC container lifecycle |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| VNC blank / black screen | `waydroid show-full-ui` (RC 0 = success) |
| `Failed to get service waydroidplatform` | Android not fully booted — wait for `boot_completed=1`, retry show-full-ui |
| `adb: device unauthorized` | Shouldn't happen on clones — ADB key is inherited. If it does: push ADB key via lxc-attach (see WAYDROID_GCP2_SETUP.md Step 7) |
| Container keeps stopping | Re-run `sudo bash /usr/local/bin/waydroid-start.sh` |
| `socat: Address already in use` | `sudo pkill socat` then re-run startup script |
| sway socket never appears | Check `/tmp/sway.log`; `pkill sway && pkill waydroid` and retry |
| `adb connect` refused | socat proxy not running; re-run startup script |
| Scraper captures 0 videos | `tail /tmp/mitmdump.log`; confirm cert mounted; confirm sort filter applied |
| TikTok: No internet connection | Cert not mounted — re-run `waydroid-start.sh` |
| TikTok asks to log in | Session expired — do a session transfer (see above) |
| ADB input text truncated | This is handled in code (char-by-char); if it regresses check `mobile_scrape.py` |
| Scraper types into wrong app | `_foreground_app()` wrong — check `dumpsys window` output directly |
