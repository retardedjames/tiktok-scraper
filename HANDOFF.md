# Handoff — TikTok Scraper

## Active fleet

| Name | IP | Status |
|---|---|---|
| JC1 | `34.30.234.222` | Active |

SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@34.30.234.222`

## Infrastructure

- Oracle VPS (DB): `150.136.40.239`, `PGPASSWORD=app1dev psql -U app1_user -h 150.136.40.239 -d tiktoks`
- ntfy topic: `retardedjames-tiktok`
- SSH key: `~/.ssh/jamescvermont`

## Operating notes

- **Do not run two VMs on the same TikTok account.** Identical `device_id` /
  `install_id` / request-signing keys trip TikTok's anti-abuse — `sort=1`
  responses start coming back truncated (no `data` key). Each VM must have
  its own account and wipe app data (`adb shell pm clear com.tiktok.lite.go`)
  before first login. See `CLONE_SETUP.md` Step 7.
- `scrape_forever.py` writes `SCRAPER_STATUS.txt` on stop — check it first
  when a VM goes quiet.
- Commit + push after any code change, no confirmation needed.

## Key files

- [CLAUDE.md](CLAUDE.md) — project overview
- [CLONE_SETUP.md](CLONE_SETUP.md) — current provisioning runbook
- [scrape_forever.py](scrape_forever.py) — production entry point
- [queue.py](queue.py) — shared PostgreSQL queue
