# Handoff — TikTok Scraper Session 2026-04-20

## Current State

- **GCP2** (`34.153.25.251`) — active, scraping, started at 18:47 UTC
- **GCP4** — being provisioned by cloning GCP2's machine image (user is doing this now)
- **GCP3** — abandoned, was shut off; ignore it

## Next Task for a New Session

User will give you the IP for GCP4. Follow **`CLONE_SETUP.md`** — the Provisioning
Runbook section is written for you to execute autonomously. Just substitute the IP and
"GCP4" wherever the runbook says `<NEW_VM_IP>` / `<NEW_VM_NAME>`. No user steps needed.

After GCP4 is running, update the fleet table in CLONE_SETUP.md.

---

## What Changed This Session

### 1. VM strategy changed: provisioning → cloning
- New VMs are now created from a GCP machine image of GCP2
- `CLONE_SETUP.md` is the new primary ops doc (Claude-executable runbook)
- `WAYDROID_GCP2_SETUP.md` is marked deprecated (historical reference only)

### 2. Repo is now public
- `https://github.com/retardedjames/tiktok-scraper` — public
- VMs can `git pull` directly; no rsync needed
- `run.sh` already does `git pull --ff-only` before starting

### 3. `waydroid-start.sh` fixed
- Was hardcoded to GCP2's IP in the final output lines
- Now reads external IP from GCP instance metadata at runtime

### 4. Session export bug fixed in docs
- `tar -czf /tmp/...` inside `lxc-attach` writes to the container's `/tmp`, not the host's
- Correct method: pipe to `sudo tee /tmp/...` on the host (documented in CLONE_SETUP.md)

---

## GCP VM Fleet

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | Active — scraping |
| GCP4 | TBD | Pending — user cloning GCP2 image now |

SSH: `ssh -i ~/.ssh/jamescvermont jamescvermont@<IP>`

---

## Infrastructure (unchanged)

- Oracle VPS: `150.136.40.239` — PostgreSQL `tiktoks` DB, user `app1_user`, pw `app1dev`
- Queue: `terms` table, ~3,500 pending terms
- ntfy topic: `retardedjames-tiktok`
