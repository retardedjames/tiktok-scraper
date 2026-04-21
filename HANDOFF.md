# Handoff — TikTok Scraper Session 2026-04-21 (early morning)

## TL;DR

- **GCP2 is stopped.** Hit a sort=1 filter-endpoint throttle after a second VM
  came online using the same TikTok account. User decided to wait ~1 hour then
  try GCP2 alone — first thing next session should be: check whether enough
  time has passed and restart GCP2 if so.
- **GCP4 (35.184.191.10) is being deleted by the user** — its IP is likely
  flagged. Not in the fleet table anymore.
- **GCP5 (104.198.27.100) is half-provisioned.** Waydroid stack was up,
  TikTok session wiped via `pm clear`, cert mounted, but the sway/wayvnc/
  show-full-ui processes died mid-debug when I killed/restarted them through
  SSH without proper daemonization. User was logged into VNC and seeing a
  black screen when the session ended. Needs the startup script re-run, then
  user VNC-logs-in with a **different TikTok account**, then start scraper.
- **Three fixes pushed today** (commits `dcd6257`, `ab2d9e3`, `57434bc`).
  See "What Was Broken and What Was Fixed" below.
- Queue state: 47 done, 3464 pending, 0 in_progress at session close
  (approximate — run `python3 queue.py stats` to confirm).

## Critical Discovery This Session

**Do not run two VMs on the same TikTok account.** When GCP4 came online
alongside GCP2 (both had the cloned session, so identical `device_id` /
`install_id` / request-signing keys from `/data/data/com.tiktok.lite.go/`),
TikTok's anti-abuse tripped within ~1 minute and started returning
**truncated responses to `sort=1` (most-liked) queries only** — the default
`sort=rel` search still worked fine.

Throttle signature in `/tmp/mitmdump.log`:

```
sort=rel cursor=0: +19 videos (2 docs)
sort=1   cursor=0: matched but 0 videos (1 docs, keys=['result_status', 'status_code', 'status_msg', 'chunk_index'])
```

A healthy response has keys
`[result_status, status_code, data, qc, cursor, has_more, extra, log_pb]`.
The blocked shape has `status_msg` + `chunk_index` instead of `data` — nothing
to save. Both VMs hit this within 2 minutes of each other; GCP2 alone was
still blocked ~7 min later when I retried.

**Root cause is device-fingerprint-sharing, not IP.** The `device_id` is
generated on first app launch and persists in `/data/data/com.tiktok.lite.go/`.
Every API request signs with `X-Gorgon`/`X-Ladon`/`X-Argus` derived from
device keys baked into the app data. Cloning the VM copies all of that, so
TikTok sees "one device sending concurrent sort=1 streams from two IPs".
A VPN would mask the IP but the device fingerprint would still collide.

**Mitigation**: each VM runs on **its own TikTok account**, with its app
data wiped via `adb shell pm clear com.tiktok.lite.go` before first login.
Fresh login regenerates `device_id`/`install_id`. Runbook updated in
`CLONE_SETUP.md` (Step 7, commit `ab2d9e3`).

## Where GCP5 Is Stuck

IP: `104.198.27.100`. Hostname set to `gcp5`. `.env` has `VM_NAME=GCP5`.

Completed on GCP5:
- Code pulled (latest main)
- `/usr/local/bin/waydroid-start.sh` updated from repo
- Waydroid stack brought up once (container RUNNING)
- `/sdcard/mitmproxy-ca.pem` pushed via adb (missing on clone — fixed in commit `57434bc`)
- `/system/etc/security/cacerts/c8750f0d.0` bind-mounted with hash `1b0dd7e2…`
  matching host `~/.mitmproxy/mitmproxy-ca-cert.pem`
- `pm clear com.tiktok.lite.go` executed → app data wiped
- TikTok launched → "Sign up for TikTok" screen confirmed via screenshot

NOT completed:
- User could not VNC in — saw black screen. Tried to restart sway/wayvnc
  but SSH background `&` without `disown` killed them on disconnect.
  User closed session before I could re-run the full startup script.
- TikTok login with second account is NOT done yet.
- Scraper is NOT running on GCP5.

### Resume plan for GCP5

1. Re-run the startup script (cert mount will no-op since it's already bound,
   but sway/wayvnc will come back — the updated script at `57434bc` now
   survives the cert-missing case):

   ```bash
   ssh -i ~/.ssh/jamescvermont jamescvermont@104.198.27.100 \
     "sudo bash /usr/local/bin/waydroid-start.sh 2>&1 | tail -20"
   ```

   If the bind-mount complains "already mounted", unmount first:
   `sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- umount /system/etc/security/cacerts`

2. Tell user to connect VNC to `104.198.27.100:5900` — should see the
   TikTok Sign up / Log in screen (or FYP if `pm clear` state was somehow
   lost; if FYP shows, re-run `adb -s 127.0.0.1:5556 shell pm clear com.tiktok.lite.go`).

3. User logs in with a **different TikTok account** than the one on GCP2.
   Expect slider captcha.

4. Verify logged-in state by screenshotting:
   ```bash
   ssh -i ~/.ssh/jamescvermont jamescvermont@104.198.27.100 \
     "adb -s 127.0.0.1:5556 shell screencap -p /sdcard/s.png && \
      adb -s 127.0.0.1:5556 pull /sdcard/s.png /tmp/s.png"
   scp -i ~/.ssh/jamescvermont jamescvermont@104.198.27.100:/tmp/s.png /tmp/gcp5.png
   ```
   Should show For You feed.

5. Reset stuck terms and start scraper:
   ```bash
   ssh -i ~/.ssh/jamescvermont jamescvermont@104.198.27.100 \
     "cd ~/tiktok-scraper && python3 queue.py reset && \
      nohup bash run.sh >> /tmp/scraper.log 2>&1 &"
   ```

6. Add GCP5 to the fleet table in `CLONE_SETUP.md` once it's confirmed saving.

### Resume plan for GCP2

If user says > 1 hour has passed since 01:08 on 2026-04-21:

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@34.153.25.251 \
  "cd ~/tiktok-scraper && python3 queue.py reset && \
   nohup bash run.sh >> /tmp/scraper.log 2>&1 &"
```

Watch first 2-3 terms. If sort=1 returns normal `+N videos` shape → throttle
cleared, resume normal ops. If still blocked → longer cooldown needed, or
the account itself is flagged and needs a fresh login / new account.

## What Was Broken and What Was Fixed

### 1. Repo `waydroid-start.sh` was missing the cert bind-mount (`dcd6257`)

The clone runbook does `sudo cp waydroid-start.sh /usr/local/bin/` which
overwrote GCP2's richer inherited copy with a stale repo version. That stale
version had no mitmproxy cert bind-mount at all — the Android container's
`/system/etc/security/cacerts/c8750f0d.0` stayed as the stock Android CA and
every TikTok TLS handshake failed. Restored the bind-mount block. Also added
`modprobe binder_linux` at the top of the script so clones where the module
isn't autoloaded self-heal.

### 2. `MAX_CONSECUTIVE_EMPTY` bumped 2 → 3 (`dcd6257`)

User requested. Tolerates occasional transient UI glitches without stopping.
Docstring + log messages updated to use the constant instead of a hard-coded 2.

### 3. CLONE_SETUP runbook: wipe session, fresh login per VM (`ab2d9e3`)

Step 7 rewritten to `pm clear com.tiktok.lite.go` before login. "What the
Clone Inherits" table: TikTok session row changed from "Inherited" to
"Wiped in Step 7 — fresh login required on its own account". Session
Transfer section removed entirely. Troubleshooting row for "TikTok asks to
log in" updated to reflect that this is now the expected state on a fresh
clone. GCP4 dropped from the fleet table.

### 4. `waydroid-start.sh` self-heals missing `/sdcard/mitmproxy-ca.pem` (`57434bc`)

GCP5's machine image didn't carry `/sdcard/mitmproxy-ca.pem`, so the
bind-mount's `cp /sdcard/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0` silently
failed (the old `2>/dev/null` was masking it), and the bind-mount completed
on a directory without the intended override — stock Android CA stayed in
place, TLS failed. Fix: script now `adb push`es the cert from
`~jamescvermont/.mitmproxy/mitmproxy-ca-cert.pem` if `/sdcard/` doesn't
have it, and the `2>/dev/null` on `cp` was removed so future failures
surface loudly.

## Infrastructure

| Name | IP | Status |
|---|---|---|
| GCP2 | `34.153.25.251` | Stopped — throttle cooldown. Resume after ~1h from 2026-04-21 01:08. |
| GCP5 | `104.198.27.100` | Half-provisioned — needs sway/wayvnc restart, then user login with second account. |

- Oracle VPS (DB): `150.136.40.239`, `PGPASSWORD=app1dev psql -U app1_user -h 150.136.40.239 -d tiktoks`
- ntfy: `retardedjames-tiktok`
- SSH key: `~/.ssh/jamescvermont`

## Workflow Preferences (from CLAUDE.md)

- Commit + push after every code change, no confirmation needed.
- User closes sessions aggressively to manage cost — expect frequent handoffs via this file.
- User-requested threshold: 3 consecutive empty runs (was 2) before circuit breaker.

## Key Files to Read First

- [CLONE_SETUP.md](CLONE_SETUP.md) — primary runbook; Step 7 now wipes the session
- [waydroid-start.sh](waydroid-start.sh) — self-healing cert push + bind-mount (lines 88-104)
- [scrape_forever.py:40](scrape_forever.py#L40) — MAX_CONSECUTIVE_EMPTY = 3
- [queue.py](queue.py) — `mark_done`, `mark_retry`, `mark_failed`, `reset_all`
