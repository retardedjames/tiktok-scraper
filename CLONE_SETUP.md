# TikTok Scraper — VM Operations Guide

**This is the primary reference for all new VMs and for Claude agents doing VM work.**

New VMs are created from a GCP machine image of GCP2 — not provisioned from scratch.
When the user gives you a new VM's IP (and name, e.g. "GCP4"), you run the provisioning
runbook below end-to-end without asking for help. The user should not have to do anything.

SSH key: `~/.ssh/jamescvermont`
SSH user: `jamescvermont`

---

## What This Project Does

Scrapes TikTok's **mobile API** for all-time most-liked videos by keyword. The mobile API
(intercepted via mitmproxy) returns the full historical catalog sorted by likes — the web
API only returns recent content.

**Scrape flow:**
1. mitmproxy runs on the GCP VM, listening on port 8080
2. Android proxy tunnels through mitmproxy via `adb reverse`
3. ADB UI-automates TikTok Lite: search → sort by likes → scroll
4. mitmproxy intercepts `/aweme/v1/general/search/stream/` (cursor=0, first page of ~10 top-liked results, chunked + multi-JSON) and `/aweme/v1/general/search/single/` (cursor=10, 20, … paginated). Parsed by `tt_dump.py`.
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

## Provisioning Runbook (Claude executes this, not the user)

Given: a new VM IP and name (e.g. `34.x.x.x`, `GCP4`). Execute each step in order,
verify before proceeding, and do not stop until `scrape_forever.py` is confirmed running.

### Step 1 — Confirm SSH access

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> "uname -a && echo OK"
```

Expected: Ubuntu 25.10 / kernel 6.17.x-gcp. If SSH fails, the VM isn't ready yet — wait
and retry. Do not proceed until this succeeds.

### Step 2 — Pull latest code and install startup script

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> "
  cd ~/tiktok-scraper && git pull
  sudo cp waydroid-start.sh /usr/local/bin/waydroid-start.sh
  sudo chmod +x /usr/local/bin/waydroid-start.sh
"
```

### Step 3 — Write `.env`

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> "cat > ~/tiktok-scraper/.env << 'EOF'
ADB_DEVICE=127.0.0.1:5556
VM_NAME=<NEW_VM_NAME>
NTFY_TOPIC=retardedjames-tiktok
NTFY_PER_TERM=0
EOF"
```

### Step 4 — Start Waydroid

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> \
  "sudo bash /usr/local/bin/waydroid-start.sh 2>&1"
```

Wait for `[*] All done!`. The script takes ~2 min. If it exits without that line, check
`/tmp/sway.log` and `/tmp/waydroid_session.log` on the VM and retry.

### Step 5 — Wait for Android to fully boot

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> \
  "until adb -s 127.0.0.1:5556 shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do sleep 3; done && echo booted"
```

Timeout: 3 minutes. If it never boots, check `journalctl -u waydroid-container` on the VM.

### Step 6 — Verify cert is mounted

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> \
  "sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- \
   ls /system/etc/security/cacerts/c8750f0d.0 && echo cert_ok"
```

If `cert_ok` is not printed, the bind-mount failed. Re-run `waydroid-start.sh` and retry.

### Step 7 — Wipe inherited TikTok session and log in fresh

**Do not reuse the source VM's TikTok session.** Running two VMs with the same
device fingerprint (`device_id` / `install_id` are baked into the app data and
sign every API request via `X-Gorgon`/`X-Ladon`/`X-Argus`) trips TikTok's
anti-abuse: sort=1 (most-liked) queries return a truncated body
(`{result_status, status_code, status_msg, chunk_index}` — no `data`) and the
scraper gets 0 captures indefinitely. The throttle is account+device-scoped and
persists for a long time (hours+).

Each new VM must run on **its own TikTok account** with its own freshly
generated `device_id`. The wipe below clears the inherited app data so the
next TikTok launch registers new IDs.

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> "
  adb -s 127.0.0.1:5556 shell am force-stop com.tiktok.lite.go
  adb -s 127.0.0.1:5556 shell pm clear com.tiktok.lite.go
  adb -s 127.0.0.1:5556 shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity
"
```

Then log in manually via VNC:
1. Connect VNC to `<NEW_VM_IP>:5900` (no password)
2. Log in to TikTok Lite with a **different account** than the one on GCP2
3. Expect a slider captcha on first login — click and drag (sway supports the virtual pointer protocol)

Verify the feed is reachable before proceeding:

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> "
  adb -s 127.0.0.1:5556 shell screencap -p /sdcard/screen.png
  adb -s 127.0.0.1:5556 pull /sdcard/screen.png /tmp/screen_verify.png
"
scp -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP>:/tmp/screen_verify.png /tmp/
```

Screenshot must show the For You feed. If it's still on a login screen, repeat
the login flow.

### Step 8 — Reset stuck queue terms

The source VM may have had in-progress terms at snapshot time. Unstick them:

```bash
cd ~/tiktok-scraper && python3 queue.py reset
```

### Step 9 — Start the scraper

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> \
  "cd ~/tiktok-scraper && nohup bash run.sh >> /tmp/scraper.log 2>&1 &"
```

Wait 15 seconds, then confirm it's running:

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP> \
  "tail -20 /tmp/scraper.log"
```

Expected output: scraper started line, mitmproxy launched, first term grabbed. If the log
shows errors or `SCRAPER_STATUS.txt` was written, diagnose before reporting success.

### Step 10 — Update fleet table

Update the VM fleet table in this file (`CLONE_SETUP.md`) to add the new VM:

```
| GCP4 | `34.x.x.x` | Active — scraping |
```

---

## What the Clone Inherits (no action needed)

| Item | Notes |
|---|---|
| Waydroid + Android images | Inherited |
| libhoudini (ARM translation) | Inherited |
| TikTok Lite APK | Inherited |
| TikTok session (logged in) | **Wiped in Step 7** — fresh login required on its own account |
| mitmproxy cert + private key | **Do not regenerate** — matched pair from source |
| ADB key (authorized in Android) | Inherited |
| Screen dimensions (720×1612 @ 280dpi) | Inherited |
| Python packages (mitmproxy, sqlalchemy, etc.) | Inherited |

---

## Every Boot — Starting the Waydroid Stack

Waydroid does not autostart. After any VM reboot:

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
failures, `SCRAPER_STATUS.txt` explains why and an ntfy push notification fires.

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

## ntfy Notifications

Topic: `retardedjames-tiktok` — install ntfy app, subscribe to that topic.
Browser: `https://ntfy.sh/retardedjames-tiktok`

| Event | Priority |
|---|---|
| Scraper started | Low (silent) |
| 0 results warning (1 of 2) | Default |
| Scraper stopped — 2 consecutive failures | **High (buzzes)** |
| Heartbeat every 50 terms | Low (silent) |
| Scraper stopped cleanly | Low (silent) |

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
6. mitmproxy intercepts `/aweme/v1/general/search/single/` responses
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
not 5555. Direct port 5555 is inside the container's network namespace.

**mitmproxy endpoint:**
- Path: `/aweme/v1/general/search/single/`
- Response key: `data[].aweme_info` (not `aweme_list` or `item_list`)
- `sort_type=1` = most liked

**Proxy cleanup:**
`clear_proxy()` is called automatically after each term. If mitmproxy dies mid-run and
the proxy isn't cleared, Android loses internet on next scrape.

**mitmproxy cert — do not copy or regenerate on a clone:**
The cert in Android's system CA store and the private key in `~/.mitmproxy/` are a matched
pair from the source VM. Cloning keeps them in sync. If they diverge, TikTok shows
"No internet connection" and mitmdump logs TLS handshake failures.

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
| `adb: device unauthorized` | Shouldn't happen on clones. If it does: push ADB key via lxc-attach (see WAYDROID_GCP2_SETUP.md Step 7) |
| Container keeps stopping | Re-run `sudo bash /usr/local/bin/waydroid-start.sh` |
| `socat: Address already in use` | `sudo pkill socat` then re-run startup script |
| sway socket never appears | Check `/tmp/sway.log`; `pkill sway && pkill waydroid` and retry |
| `adb connect` refused | socat proxy not running; re-run startup script |
| Scraper captures 0 videos | `tail /tmp/mitmdump.log`; confirm cert mounted; confirm sort filter applied |
| TikTok: No internet connection | Cert not mounted — re-run `waydroid-start.sh` |
| TikTok asks to log in | Expected on a fresh clone — log in via VNC with a distinct account (see Step 7) |
| ADB input text truncated | Handled in code (char-by-char); if it regresses check `mobile_scrape.py` |
| Scraper types into wrong app | `_foreground_app()` wrong — check `dumpsys window` output directly |
