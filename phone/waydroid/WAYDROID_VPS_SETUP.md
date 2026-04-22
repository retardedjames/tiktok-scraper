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

**Base snapshot:** take a snapshot of any working VM (e.g. `waydroid-base-YYYY-MM-DD`)
after the apt/pip prerequisites are installed but **before** `waydroid init`.
Future clones boot from that snapshot and run `vps_clone_init.sh`.

Once the snapshot exists, the source VM can be torn down.

**Included in the base snapshot:**

- Ubuntu 25.10
- apt packages: `sway`, `wayvnc`, `socat`, `adb`, `git`, `lxc`, `psmisc`,
  `curl`, `waydroid 1.6.2` (binary present, **NOT initialized**)
- pip packages: `mitmproxy`, `sqlalchemy`, `psycopg2-binary`
- `~/.local/bin` on PATH (via `.bashrc` + `.profile`)

**NOT in the base — happens per clone (`vps_clone_init.sh` drives this):**

- `waydroid init` (downloads ~3 GB Android image — also the device-identity
  step; must be unique per clone)
- mitmproxy CA cert (must be unique per VM)
- libhoudini (ARM→x86 translation)
- TikTok APK install
- Pixel 6 masquerade
- `android_id` randomization
- `.env` (`VM_NAME` derived from external IP)
- `waydroid-stack.service` (systemd auto-restart)
- TikTok account login (manual, via VNC)

## Deploying to a New VPS (per-clone)

The folder lives in the main repo under `phone/waydroid/`, but the VPS pulls
**only that folder** via `git sparse-checkout` — no parent scripts, no other
clutter.

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_VM_IP>

# Wipe any stale ~/tiktok-scraper left by an earlier attempt or by the
# base snapshot — git clone refuses to write into a non-empty directory.
rm -rf ~/tiktok-scraper

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

`vps_clone_init.sh` does the full setup in one pass:

 1. Modprobe `binder_linux` + chmod udmabuf
 2. `waydroid init -s GAPPS` (~3 GB download, ~10 min)
 3. Patch LXC config for udmabuf
 4. Generate mitmproxy CA cert
 5. Install `/usr/local/bin/waydroid-start.sh`
 6. Start Waydroid stack; wait for Android boot
 7. Authorize host ADB key inside the container (`/data/misc/adb/adb_keys`)
 8. `wm size 1080x2400` + `wm density 420`
 9. Install libhoudini (ARM→x86 translation)
10. Masquerade as Pixel 6 (via local `masquerade_buildprop.py`)
11. Re-mount mitmproxy cert (session restart clears it)
12. Randomize `android_id`
13. Wipe TikTok app data
14. Install TikTok Lite splits from `patched/`
15. Launch TikTok for first-run
16. **Generate `.env`** with `VM_NAME=vps-<external-ip>` (or hostname if IP lookup fails)
17. **Install `waydroid-stack.service`** — systemd unit that re-runs
    `waydroid-start.sh` on every boot (see "Reboot behaviour" below)

Manual after the script (just one thing):

1. **VNC** to `<vm-ip>:5900` (no password), log into TikTok with a FRESH
   account (never used on another VM). Then:
   ```bash
   cd ~/tiktok-scraper/phone/waydroid
   nohup bash run.sh >> /tmp/scraper.log 2>&1 &
   tail -f /tmp/scraper.log
   ```

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
| `waydroid-start.sh` | Full stack startup (installed to `/usr/local/bin/`) — sway, waydroid-container, session, wayvnc, ADB key, socat proxy, mitmproxy cert bind mount. Idempotent — safe to re-run after a dead session. |
| `vps_clone_init.sh` | One-time per-clone setup. 17 steps from `waydroid init` through TikTok launch, `.env` generation, and systemd auto-restart unit install. |
| `run.sh` | Scraper wrapper — loads `.env`, `git fetch`+`reset --hard origin/main`, then execs `scrape_forever.py`. |
| `device_fingerprint.sh` | Dumps every identifier TikTok can read off the device (build.prop, android_id, MAC, CPU, etc.). Redirect stdout to a file per VM and `diff` to find cross-VM identical values — those are the prime bot-detection signals. |
| `.env.example` | Config template (`ADB_DEVICE`, `VM_NAME`, `NTFY_TOPIC`). `vps_clone_init.sh` copies this to `.env` with `VM_NAME` auto-derived. |
| `patched/` | Patched TikTok Lite APK splits + keystore |
| `WAYDROID_VPS_SETUP.md` | This doc |

## Reboot behaviour

`waydroid-container.service` (shipped with the waydroid package) only starts
the *container daemon* — it does **not** start the session, sway, wayvnc, the
socat ADB proxy, or the mitmproxy CA bind-mount. Without help, a VM comes back
from a reboot with `waydroid-container` marked "active" but `adb devices`
reporting `offline` and UI automation broken.

`vps_clone_init.sh` step 17 installs `/etc/systemd/system/waydroid-stack.service`
to cover this: on every boot it re-runs `waydroid-start.sh`, which is
idempotent and brings up the full stack (session → sway → wayvnc → socat →
ADB key → cert mount). Check with:

```bash
systemctl status waydroid-stack.service
```

If this unit is missing or failed, the manual recovery is the same one-liner:

```bash
sudo bash /usr/local/bin/waydroid-start.sh
```

## Device fingerprint audit

`device_fingerprint.sh` surfaces every prop / setting / file a TikTok SDK is
likely to read when deciding "is this a real phone?". Two sections:

- **MASQUERADE** — props that should look like a real Pixel 6. If these are
  wrong, the basic Pixel 6 masquerade failed — re-run
  `phone/waydroid/masquerade_buildprop.py` with `sudo`.
- **LEAK** — props/files that expose the Waydroid + x86 reality even after
  masquerade. The standout leaks today are:

  | Leak | Example value | Why it's a tell |
  |---|---|---|
  | `ro.product.cpu.abi` | `x86_64` | Real Pixel 6 is `arm64-v8a` |
  | `ro.system.build.fingerprint` | `waydroid/lineage_waydroid_x86_64/...` | Sibling fingerprint props bypass the masquerade |
  | `ro.board.platform` | `waydroid` | Real Pixel 6: `gs101` |
  | `ro.hardware` / `ro.bootloader` | `unknown` / `unknown` | Real device has concrete values |
  | `ro.serialno` | empty | Real device has a serial |
  | `ro.dalvik.vm.native.bridge` | `libhoudini.so` | Signals an x86 host running ARM apps |
  | `ro.modversion` / `ro.lineage.*` | LineageOS version | No real Pixel ships with LineageOS props |
  | `/proc/cpuinfo` `vendor_id` | `AuthenticAMD` | Real Pixel has ARM Tensor (no x86 cpuinfo) |
  | `/proc/cpuinfo` `flags` | contains `hypervisor` | VM tell |
  | Settings `device_name` | `WayDroid x86_64 Device` | Leaks the brand name |
  | NIC MAC prefix | `00:16:3e:...` | Xen virt-machine OUI |

Use it per VM and diff the outputs to find identifiers that are accidentally
identical across clones (those are the bot-linking signals):

```bash
# On VPS A
bash phone/waydroid/device_fingerprint.sh > /tmp/fp_A.txt
# On VPS B
bash phone/waydroid/device_fingerprint.sh > /tmp/fp_B.txt
# On your laptop
scp jamescvermont@A:/tmp/fp_A.txt ./ ; scp jamescvermont@B:/tmp/fp_B.txt ./
diff fp_A.txt fp_B.txt
```

Anything that **matches** is fleet-wide — that's your bot signal. Anything
marked "LEAK" that's identifiable as emulator/VM is also a candidate
regardless of whether it differs across clones.

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
Clear app data, restart, log in with a different account. If it keeps happening
on fresh accounts, inspect `device_fingerprint.sh` output vs a previously-healthy
VM — a LEAK prop may be too obvious.

**Session died — `adb devices` shows `offline` or empty, `adb shell` hangs:**
this is the most common failure mode. `waydroid-container.service` stays
"active" but the `lxc-start` child (Android's init) has exited. Fix:

```bash
sudo bash /usr/local/bin/waydroid-start.sh
```

The script stops any half-alive session, restarts everything, and waits for
`sys.boot_completed=1`. Do **not** `kill` adbd directly from the host — init
inside the container won't respawn a process killed by a host PID, so adbd
goes `<defunct>` and only a full session restart recovers it.

**`mCurrentFocus` shows "Android System" even with TikTok foregrounded:**
Waydroid exposes two displays; `dumpsys window | grep mCurrentFocus` picks
the wrong one. Use `dumpsys activity activities | grep topResumedActivity`
to see the actual foreground app. The `phone/` scraper's `_foreground_app`
already handles this dual-window case.

**Waydroid container won't start:** `sudo modprobe binder_linux` then
`sudo bash /usr/local/bin/waydroid-start.sh`.

**Masquerade prints "SKIP (missing)" for every build.prop:** `masquerade_buildprop.py`
needs to read the images it's patching, but img-based Waydroid only mounts
`/var/lib/waydroid/rootfs/` while a session is **running**. The script therefore
loop-mounts `system.img` and `vendor.img` read-only at `/mnt/waydroid_masq_*`
itself and reads from there. If you see "SKIP (missing)" after the fix, check
that `/var/lib/waydroid/images/{system,vendor}.img` exist and are ext4
(`head -c 2048 … | od -An -c` should show magic `S \357` at offset 0x438).

**ADB can't connect:** check socat proxy is up (`ss -tlnp | grep 5556`);
`waydroid-start.sh` creates it automatically. If the proxy is up but ADB
still shows `offline`, the `/data/misc/adb/adb_keys` file was likely written
*after* adbd started, so adbd has no authorized keys cached. Same fix —
restart via `waydroid-start.sh` (it writes the key before adbd listens).

**After `sudo reboot`:** if `vps_clone_init.sh` was run on this VM, the
`waydroid-stack.service` unit should bring the stack back on its own.
Verify with `systemctl status waydroid-stack.service` and `adb devices`. If
the unit is missing (old clone predating this change), install it by
re-running `vps_clone_init.sh` or by enabling the unit manually.

**UI taps miss after a TikTok update:** VNC in and run `calibrate.py` to
dial in new coords, committed as `coords.json` (overrides the reference).
