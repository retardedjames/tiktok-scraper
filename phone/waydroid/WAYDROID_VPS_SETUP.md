# Waydroid VPS TikTok Scraper

Self-contained fork of the `phone/` scraper adapted for Waydroid running on
a Linux VPS. Every file the VM needs lives in this folder — rclone it to a
new VPS and run `vps_clone_init.sh` to finish setup.

## Goal

Scale TikTok scraping horizontally on cheap VPS instances (no physical phone
needed, no GCP-specific setup). Each VPS appears to TikTok as a distinct
Android phone (Pixel 6 masquerade + unique `android_id` + unique account).

## Why this is a fork, not an edit

The `phone/` scraper targets a real USB-attached phone (usbipd → WSL2 → ADB).
Waydroid has a few quirks that don't belong in `phone/`:

- ADB must target `127.0.0.1:5556` (socat proxy → Waydroid container)
- `dumpsys window` emits **two** `mCurrentFocus` lines (one per display) —
  `_foreground_app` has to prefer the TikTok-focused one
- Preflight has no usbipd, but needs to check the mitmproxy cert is
  bind-mounted as a system CA inside the Waydroid container

Everything else (auto-scaling `coords.py`, `calibrate.py`, Videos-tab-first
search flow, `scroll_smart`, `.env` loading) is copied verbatim from `phone/`.

## Resolution

Waydroid is set to **1080×2400 @ 420dpi** — the native Pixel 6 resolution,
matching the `masquerade_buildprop.py` identity. `coords.py` auto-scales from
the 720×1612 reference coords; scale factor ≈ 1.5× in each dimension. If the
UI taps miss anything in practice, run `calibrate.py --show` via VNC and
dial in exact coords per screen.

## Base Image Plan

**VPS:** `136.114.251.49` — base image ready (as of 2026-04-21).

**Already installed on 136.114.251.49:**

- Ubuntu 25.10
- apt packages: `sway`, `wayvnc`, `socat`, `adb`, `git`, `lxc`, `psmisc`,
  `curl`, `waydroid 1.6.2` (binary present, **NOT initialized**)
- pip packages: `mitmproxy`, `sqlalchemy`, `psycopg2-binary`
- `~/.local/bin` on PATH (via `.bashrc` + `.profile`)

**NOT yet done on 136.114.251.49 (intentionally — happens per clone):**

- `waydroid init` (downloads ~3 GB Android image — also the device-identity
  step; must be unique per clone)
- mitmproxy CA cert (must be unique per VM)
- libhoudini (ARM→x86 translation)
- TikTok APK install
- Pixel 6 masquerade
- `android_id` randomization
- TikTok account login

**Next step:** take a snapshot of `136.114.251.49` in your cloud console and
name it `waydroid-base-YYYY-MM-DD`. Future clones boot from the snapshot and
complete setup via `vps_clone_init.sh`.

## Deploying to a New VPS (per-clone)

The folder lives in the main repo under `phone/waydroid/`, but the VPS pulls
**only that folder** via `git sparse-checkout` — no parent scripts, no other
clutter.

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP>

# Sparse clone — blob:none skips file downloads until checkout,
# --no-cone + the two patterns check out ONLY phone/waydroid/**.
cd ~
git clone --filter=blob:none --no-checkout https://github.com/retardedjames/tiktok-scraper.git
cd tiktok-scraper
git sparse-checkout set --no-cone '/phone/waydroid/*' '/phone/waydroid/patched/*'
git checkout main

# Run the one-time init
bash phone/waydroid/vps_clone_init.sh
```

Working tree after checkout contains only `phone/waydroid/` — no root-level
scripts, no `phone/*.py` clutter. `phone/waydroid/run.sh` does
`git fetch && git reset --hard origin/main` on each start, and sparse-checkout
keeps the working tree confined to `phone/waydroid/`.

This script does in one pass:

1. Modprobe `binder_linux` + chmod udmabuf
2. `waydroid init -s GAPPS` (~3 GB download, ~10 min)
3. Patch LXC config for udmabuf
4. Generate mitmproxy CA cert
5. Install `/usr/local/bin/waydroid-start.sh`
6. Start Waydroid stack; wait for Android boot
7. Authorize ADB key in container
8. `wm size 1080x2400` + `wm density 420`
9. Install libhoudini
10. Masquerade as Pixel 6 (via local `masquerade_buildprop.py`)
11. Re-mount mitmproxy cert (session restart clears it)
12. Randomize `android_id`
13. Wipe TikTok app data
14. Install TikTok Lite splits from `patched/`
15. Launch TikTok for first-run

Manual after the script:

1. **VNC** to `<vm-ip>:5900` (no password), log into TikTok with a FRESH
   account (never used on another VM).
2. `cp .env.example .env && nano .env` — set `VM_NAME` uniquely (e.g. VPS1).
3. Start scraper: `nohup bash run.sh >> /tmp/scraper.log 2>&1 &`

## What's in this folder

| File | Purpose |
|---|---|
| `scrape_forever.py` | **Production entry** — continuous loop, stops on 3 failures |
| `batch_scrape.py` | Dev/test — N terms then stop |
| `mobile_scrape.py` | Core scraper (ADB + mitmproxy + UI automation) |
| `tt_dump.py` | mitmproxy addon — parses search responses |
| `preflight.py` | Pre-run checks (ADB, cert mount, proxy state) |
| `coords.py` | Screen coord loader (auto-scaling from 720×1612 reference) |
| `calibrate.py` | Walk-through to dial in coords per screen |
| `coords.json.example` | Reference 720×1612 coords |
| `queue.py` | PostgreSQL queue — `FOR UPDATE SKIP LOCKED` |
| `db.py` | SQLAlchemy models + `save_search` |
| `search_terms.txt` | ~3,524 keywords (used only for `queue.py import`) |
| `masquerade_buildprop.py` | Rewrites build.prop → Pixel 6 |
| `waydroid-start.sh` | Full stack startup (installed to `/usr/local/bin/`) |
| `vps_clone_init.sh` | One-time per-clone setup (run once after rclone) |
| `run.sh` | Scraper wrapper — loads `.env` then execs `scrape_forever.py` |
| `.env.example` | Config template (`ADB_DEVICE`, `VM_NAME`, `NTFY_TOPIC`) |
| `patched/` | Patched TikTok Lite APK splits + keystore |
| `WAYDROID_VPS_SETUP.md` | This doc |

## Monitoring

```bash
# On the VM
tail -f /tmp/scraper.log                          # live scraper output
cat ~/tiktok-scraper/waydroid/SCRAPER_STATUS.txt  # why it stopped

# From anywhere with DB access
python3 queue.py stats
```

## Infrastructure

- SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<IP>`
- DB:  `postgresql://app1_user:app1dev@150.136.40.239:5432/tiktoks`
- VNC: `<IP>:5900` (no password)
- APK patch VM: `34.162.181.247` (x86_64, apktool + keystore)

## Troubleshooting

**"No internet" in TikTok:** cert not mounted — re-run cert bind-mount from
`vps_clone_init.sh` step 11 (or `sudo bash /usr/local/bin/waydroid-start.sh`).
The cert lives at `/data/local/tmp/mitmproxy-ca.pem` inside the container
(not `/sdcard/` — adb shell can't write there on LineageOS-GAPPS).

**0 saves every term:** TikTok anti-abuse flagged this device+account.
Clear app data, restart, log in with a different account.

**Waydroid container won't start:** `sudo modprobe binder_linux` then
`sudo bash /usr/local/bin/waydroid-start.sh`.

**ADB can't connect:** check socat proxy is up (`ss -tlnp | grep 5556`);
`waydroid-start.sh` creates it automatically.

**UI taps miss after a TikTok update:** VNC in and run `calibrate.py` to
dial in new coords, committed as `coords.json` (overrides the reference).
