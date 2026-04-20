# Handoff — TikTok Scraper Session 2026-04-20

## Current State

GCP2 is fully operational running the scraper natively. End-to-end test confirmed:
results are saving to Oracle VPS PostgreSQL. Queue has 3,503 pending terms.

**Next task for a new session:** Provision GCP3 (`34.59.191.130`) — clean Ubuntu 25.10,
nothing installed yet. Follow `WAYDROID_GCP2_SETUP.md` top to bottom.

---

## Architecture (as of this session)

- **Scraper runs ON each GCP VM** — not from WSL2. WSL2 is monitoring/debugging only.
- **mitmproxy runs on the GCP VM** alongside Waydroid.
- **Queue is PostgreSQL** on Oracle VPS (`terms` table, `FOR UPDATE SKIP LOCKED`).
- **ADB device on VM:** `127.0.0.1:5556` (socat proxy — use 5556, not 5555 directly).
- **Code deployment:** rsync from WSL2 (git clone doesn't work — no GitHub auth on VMs).
- **Notifications:** ntfy.sh topic `retardedjames-tiktok` — install app on phone, subscribe.

---

## GCP VM Fleet

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | Active — fully set up, tested, ready to run `scrape_forever.py` |
| GCP3 | `34.59.191.130` | Pending — clean Ubuntu 25.10, nothing installed |

SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<IP>`

---

## What Changed This Session

### 1. Scraper runs on GCP VM (not WSL2)
- `ADB_DEVICE=127.0.0.1:5556` in `.env` on each VM
- mitmproxy cert must be generated on the VM (`mitmdump` run once to create `~/.mitmproxy/`)
- Cert pushed to `/sdcard/mitmproxy-ca.pem` on Android; `waydroid-start.sh` bind-mounts it

### 2. Queue migrated SQLite → PostgreSQL
- `terms` table on Oracle VPS (same DB as videos/authors)
- `FOR UPDATE SKIP LOCKED` — concurrent-safe for multiple VMs
- 3,503 pending, 10 done

### 3. `scrape_forever.py` — production continuous runner
- Pulls 1 term at a time, loops forever
- Stops + writes `SCRAPER_STATUS.txt` on 2 consecutive failures
- Sends ntfy.sh push notifications on start/stop/failure/heartbeat-every-50-terms
- Per-term notifications: set `NTFY_PER_TERM=1` in `.env` to enable

### 4. `run.sh` — start wrapper
- Loads `.env`, does `git pull --ff-only` (once git auth is set up), runs `scrape_forever.py`
- For now: rsync to deploy, then `bash run.sh` or `PATH=$PATH:$HOME/.local/bin python3 scrape_forever.py`

### 5. Timing optimizations (~2× faster per term)
- Screenshots opt-in via `--debug` flag (saves ~10s/term)
- Various delays reduced; scroll batch 30→10; min-likes default 7k→5k

### 6. Foreground detection fixed
- Uses `dumpsys window | grep mCurrentFocus` — NOT `dumpsys activity top`
- Old method always returned `com.android.launcher3`, causing constant spurious relaunches

### 7. ntfy.sh notifications
- Topic: `retardedjames-tiktok`
- Install ntfy app on phone → subscribe to that topic
- No account needed

---

## To Start Scraping on GCP2 (already set up)

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251
# If Waydroid isn't running after a reboot:
sudo bash /usr/local/bin/waydroid-start.sh

# Run the scraper
cd ~/tiktok-scraper
PATH=$PATH:$HOME/.local/bin python3 scrape_forever.py
# Or to test first:
PATH=$PATH:$HOME/.local/bin python3 batch_scrape.py --n 2
```

---

## To Provision GCP3 (next task)

Follow `WAYDROID_GCP2_SETUP.md` in order. Key GCP3-specific steps:

1. All steps are identical to GCP2 — same OS, same kernel, same packages
2. **Step 7** — generate mitmproxy cert ON GCP3 (cannot copy from GCP2)
3. **Step 10** — use Option A (session transfer from GCP2) to avoid manual TikTok login
4. **Step 12** — rsync code from WSL2, set `VM_NAME=GCP3` in `.env`
5. Run `batch_scrape.py --n 2` to confirm end-to-end before starting `scrape_forever.py`

---

## Known Gotchas

- **ADB port is 5556, not 5555** — socat proxy listens on 5556 even locally on the VM
- **Each VM needs its own mitmproxy cert** — cert + private key are a matched pair;
  copying a cert from another VM won't work (TLS handshake fails, TikTok shows "No internet")
- **mitmdump PATH** — installed at `~/.local/bin/mitmdump`; scripts handle this internally
  but shell commands need `PATH=$PATH:$HOME/.local/bin`
- **SCRAPER_STATUS.txt** — written in `~/tiktok-scraper/` on the VM when scraper stops;
  you'll also get an ntfy push notification so you don't need to check manually
- **Cert bind-mount** — survives until Waydroid container restarts; `waydroid-start.sh`
  re-applies on every boot
- **Filter popup Apply button** — y≈778 (top of panel), NOT y≈975
- **Proxy must be cleared** — `clear_proxy()` called automatically; if mitmproxy dies
  mid-run and proxy isn't cleared, Android loses internet on next boot
