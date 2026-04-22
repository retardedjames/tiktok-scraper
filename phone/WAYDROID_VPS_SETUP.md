# Waydroid VPS Setup — Phone Scraper

## Goal

Run multiple independent TikTok scrapers on cheap VPS instances, each appearing to TikTok as a distinct Android phone. The `phone/` scraper (newer, better than the root-level one) is used as the automation engine. Each VPS:

- Runs Waydroid (Android 13 emulator) at **720×1612 @ 280 dpi** (matches `phone/coords.json`)
- Uses the patched TikTok Lite APK from `phone/patched/`
- Has a unique `android_id`, unique device fingerprint (Pixel 6 masquerade), and unique TikTok account
- Pulls search terms from the shared PostgreSQL queue on `150.136.40.239`
- Sends results to the same PostgreSQL DB

## Strategy: Base Image + Per-Clone Init

**Do NOT install Waydroid on every VM from scratch** — `waydroid init` downloads ~3 GB and takes ~10 minutes. Instead:

1. **Build a base image** once (packages installed, Waydroid binary present but NOT initialized)
2. **Snapshot** the VM as a machine image
3. **Spin up** new VMs from the snapshot — fast boot, no downloading
4. **Run `vps_clone_init.sh`** on each clone to complete per-VM setup (Waydroid init, TikTok install, unique fingerprint)

This approach also ensures each clone gets a fresh Android container with its own device identifiers — no fingerprint sharing.

## Current Base Image State

**VPS IP:** `136.114.251.49`  
**Status:** Base image ready — take snapshot NOW

### What's already installed on 136.114.251.49

- Ubuntu 25.10
- System packages: `sway`, `wayvnc`, `socat`, `adb`, `git`, `lxc`, `psmisc`, `curl`, `waydroid 1.6.2` (NOT initialized)
- Python packages: `mitmproxy`, `sqlalchemy`, `psycopg2-binary`
- `~/.local/bin` added to PATH in `.bashrc` and `.profile`
- `/usr/local/bin/waydroid-start.sh` installed
- Repo cloned at `~/tiktok-scraper` (main branch)
- Patched TikTok Lite APKs present at `~/tiktok-scraper/phone/patched/`

### What is NOT yet done (intentionally — done per-clone after snapshot)

- `waydroid init` (this is the device-identity step — must be unique per VM)
- mitmproxy CA cert generation
- libhoudini (ARM translation layer)
- TikTok APK install
- Pixel 6 masquerade
- `android_id` randomization
- TikTok account login

## Step 1: Take the Machine Image

Before doing anything else on 136.114.251.49, take a snapshot/image:

- **Oracle Cloud:** Compute → Instances → 136.114.251.49 → Create custom image
- **Name it something like:** `waydroid-base-YYYY-MM-DD`
- Wait for image creation to complete (~5-10 min)

## Step 2: Per-Clone Setup (run on each new VM)

SSH into the new VM and run:

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP>
bash ~/tiktok-scraper/phone/vps_clone_init.sh
```

This script handles (in order):
1. Load kernel modules (binder_linux, udmabuf)
2. `waydroid init -s GAPPS` (~3 GB download, ~10 min)
3. Patch LXC config for udmabuf
4. Generate mitmproxy CA cert
5. Boot Waydroid stack
6. Wait for Android boot
7. Authorize ADB key in container
8. Set screen to **720×1612 @ 280dpi**
9. Install libhoudini (ARM→x86 translation for arm64 APK)
10. Masquerade as Pixel 6 (hide Waydroid fingerprint)
11. Re-mount mitmproxy CA cert
12. Randomize `android_id`
13. Wipe TikTok app data
14. Install TikTok Lite (patched splits from `phone/patched/`)
15. Launch TikTok

Then manually (via VNC at `<ip>:5900`):
- Log into TikTok with a **unique account** (never used on another VM)

Then configure and start:

```bash
cp ~/tiktok-scraper/phone/.env.example ~/tiktok-scraper/phone/.env
nano ~/tiktok-scraper/phone/.env   # set VM_NAME=VPS1 (or VPS2, etc.)
# ADB_DEVICE=127.0.0.1:5556 is the right value for Waydroid
nohup bash ~/tiktok-scraper/phone/run.sh >> /tmp/scraper.log 2>&1 &
tail -f /tmp/scraper.log
```

## Scraper Entry Points

| Script | Purpose |
|--------|---------|
| `phone/run.sh` | Production: git pull + `scrape_forever.py` |
| `phone/scrape_forever.py` | Continuous loop, stops on 3 consecutive failures |
| `phone/batch_scrape.py` | Dev/test: N terms then stop |
| `phone/vps_clone_init.sh` | One-time per-clone setup |

## Why the `phone/` Scraper (not root-level)

The `phone/` version is newer with these improvements over the root-level scraper:
- `coords.py` with auto-scaling (works at any resolution without hardcoded coords)
- Cleaner ADB device targeting via `ADB_DEVICE` env var
- `calibrate.py` for adjusting UI coordinates if TikTok's layout shifts
- Designed to run with or without Waydroid (also works with a real USB phone)

For Waydroid VPS: set `ADB_DEVICE=127.0.0.1:5556` in `phone/.env`. The 720×1612 resolution matches the reference coords exactly (scale factor = 1.0), so `coords.json` is not required.

## Screen Resolution

Waydroid is set to **720×1612 @ 280dpi**. This matches the phone scraper's reference resolution exactly. UI coordinates from `coords.py` (and `coords.json.example`) apply without any scaling.

Do NOT change the resolution — the filter popup, scroll coordinates, and tap targets are calibrated for this size.

## APK Notes

The patched APK in `phone/patched/` is TikTok Lite with SSL pinning bypassed.  
The `tt.keystore` in that directory is the signing key used during patching.  
APKs are gitignored — if lost, re-patch from the apkm_extracted splits using `phone/patch_apk.sh` on the APK patching VM (`34.162.181.247`).

## Monitoring

```bash
# Live scraper log
tail -f /tmp/scraper.log

# Check if stopped due to failure
cat ~/tiktok-scraper/SCRAPER_STATUS.txt

# Queue stats (from anywhere with DB access)
cd ~/tiktok-scraper && python3 queue.py stats

# Waydroid stack health
sudo bash /usr/local/bin/waydroid-start.sh   # restart if needed

# VNC (monitoring Android screen)
# TigerVNC: <ip>:5900 — no password
```

## Infrastructure

| Resource | Details |
|---------|---------|
| SSH key | `~/.ssh/jamescvermont` — user `jamescvermont` |
| DB | `postgresql://app1_user:app1dev@150.136.40.239:5432/tiktoks` |
| VNC | `<vm-ip>:5900`, no password |
| APK patch VM | `34.162.181.247` (x86_64, has apktool + keystore) |

## Troubleshooting

**TikTok shows "No internet connection":**
- mitmproxy cert not mounted — re-run the cert bind-mount step in `vps_clone_init.sh`

**Scraper gets 0 saves every term:**
- TikTok is throttling this device+account — fingerprint collision or new account flagged
- Wipe TikTok data + re-login: `adb -s 127.0.0.1:5556 shell pm clear com.tiktok.lite.go`

**Waydroid container won't start:**
- `sudo modprobe binder_linux` then `sudo bash /usr/local/bin/waydroid-start.sh`

**ADB not connecting:**
- Check socat is running: `ps aux | grep socat`
- `waydroid-start.sh` creates the socat proxy automatically
