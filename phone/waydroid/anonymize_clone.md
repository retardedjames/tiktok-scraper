# Anonymize a Disk-Cloned VPS

> Applies when: you spun up a new VPS by **disk-cloning** a fully-set-up,
> already-running source VPS (not from the old "base snapshot + init from
> stock" flow). The clone inherits Waydroid images, LXC config, overlay
> masquerade, TikTok app + login session, and every per-clone identifier.
> None of that is re-randomized on boot, so unless you rotate it by hand,
> TikTok sees a second device that is **identical** to the source — same
> android_id, same container MAC, same bluetooth address, same device_name,
> same IMEI, same app install ID, same logged-in account. That's a free
> bot-cluster signal.
>
> The old [WAYDROID_VPS_SETUP.md](WAYDROID_VPS_SETUP.md) / `vps_clone_init.sh`
> initialized a **stock** Waydroid. Do not run those here — they assume state
> (no overlay, no TikTok install, no userdata) that the disk clone already
> has filled in.

## What to rotate and what to leave

**Fleet-wide values** (the Pixel 6 masquerade props in the build.prop overlay)
are intentionally identical across every clone — they're how we look like a
Pixel 6. Leave them alone.

**Per-clone identifiers** are what TikTok can use to link the clone back to
the source. Every one of these must be rotated:

| Identifier | Where | Why it matters |
|---|---|---|
| `lxc.net.0.hwaddr` (container eth0 MAC) | `/var/lib/waydroid/lxc/waydroid/config` on the host | Apps read `NetworkInterface.getHardwareAddress()`. Clone inherits the source's MAC — strongest cross-VM linker. Rotate **before** starting Waydroid so the first boot picks it up. |
| `Settings.Secure.android_id` | inside Android | Cloned userdata carries the source's 16-hex value. Primary TikTok device ID input. |
| `Settings.Secure.bluetooth_address` | inside Android | Often `null` on both, but if the source ran the newer init it'll have a value and the clone will inherit it verbatim. |
| `Settings.Global.device_name` | inside Android | "WayDroid x86_64 Device" on stock (both identical); after rotate-to-"Pixel 6" it's still identical. Set to something distinct. |
| `persist.radio.imei` | inside Android | Empty on both by default; if source ran newer init, clone inherits the exact IMEI. |
| TikTok app data | `/data/data/com.tiktok.lite.go/` | The clone is already **logged in as the source's account**. `pm clear` wipes install ID + session. |
| `Settings.Global.http_proxy` | inside Android | Source has this set to `127.0.0.1:8080` for its running mitmdump. Clone inherits the setting but has no mitmdump — TikTok login shows "No network connection". Clear before first launch; scraper re-sets it on start. |
| Scraper `.env` `VM_NAME` | `phone/waydroid/.env` | Keeps ntfy notifications distinguishable. |

**Boot ID** (`/proc/sys/kernel/random/boot_id`) and **uptime** diverge
naturally as soon as the clone boots — no action needed.

**What this does NOT fix:** fleet-wide Waydroid tells
(`ro.system.build.fingerprint`, `ro.board.platform = waydroid`, x86 cpuinfo,
native bridge = `libhoudini.so`, etc.). Those leak on every clone and are a
separate masquerade-depth problem — see the LEAK section of
[WAYDROID_VPS_SETUP.md](WAYDROID_VPS_SETUP.md#device-fingerprint-audit).

## Procedure

Assumes SSH works and you have the clone's external IP. Substitute
`<NEW_IP>` below.

### 1. Pre-boot: rotate the container eth0 MAC

The MAC is baked in the LXC config on the host filesystem. Must be changed
**before** Waydroid starts so the first boot uses the new value.

```bash
ssh -i ~/.ssh/jamescvermont jamescvermont@<NEW_IP>

# If Waydroid is already up from the clone, stop it first.
sudo systemctl stop waydroid-stack.service 2>/dev/null || true
sudo waydroid session stop 2>/dev/null || true
sudo systemctl stop waydroid-container.service 2>/dev/null || true

LXC_CFG=/var/lib/waydroid/lxc/waydroid/config
NEW_MAC=$(printf "00:16:3e:%02x:%02x:%02x" \
    $((RANDOM & 0x7f)) $((RANDOM & 0xff)) $((RANDOM & 0xff)))
sudo sed -i "s/^lxc.net.0.hwaddr = .*/lxc.net.0.hwaddr = $NEW_MAC/" "$LXC_CFG"
echo "new container MAC = $NEW_MAC"
```

Keep the `00:16:3e` OUI — that's the LXC default, changing it would itself
be fingerprintable. Rotate only the last 3 bytes.

### 2. Start the Waydroid stack

```bash
sudo bash /usr/local/bin/waydroid-start.sh
```

Wait for `sys.boot_completed=1` (the script handles this internally). Then
confirm ADB works:

```bash
adb -s 127.0.0.1:5556 shell getprop sys.boot_completed   # → 1
adb -s 127.0.0.1:5556 shell cat /sys/class/net/eth0/address  # → your new MAC
```

### 3. Rotate Android-side per-clone identifiers

```bash
ADB="adb -s 127.0.0.1:5556"

# android_id — 16 lowercase hex chars
NEW_ID=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 16)
$ADB shell settings put secure android_id "$NEW_ID"
echo "android_id = $NEW_ID"

# bluetooth_address — locally-administered unicast MAC
NEW_BT=$(printf '%02x:%02x:%02x:%02x:%02x:%02x' \
    $((RANDOM & 0xfe | 0x02)) $((RANDOM & 0xff)) $((RANDOM & 0xff)) \
    $((RANDOM & 0xff)) $((RANDOM & 0xff)) $((RANDOM & 0xff)))
$ADB shell settings put secure bluetooth_address "$NEW_BT"
echo "bluetooth_address = $NEW_BT"

# device_name — keep it plausible for a Pixel 6 but distinct across clones
$ADB shell "settings put global device_name 'Pixel 6'"

# IMEI — 15 digits, "35" TAC prefix
NEW_IMEI=$(printf '35%013d' $((RANDOM * RANDOM * RANDOM % 10000000000000)))
$ADB shell setprop persist.radio.imei "$NEW_IMEI"
echo "persist.radio.imei = $NEW_IMEI"
```

### 4. Wipe TikTok app data (removes install ID + logged-in account)

```bash
$ADB shell am force-stop com.tiktok.lite.go
$ADB shell pm clear com.tiktok.lite.go
```

This is the step that cuts the account-level link to the source VPS. Without
it, the clone opens TikTok already signed in as the source's account — the
two VPSes would then be posting requests from the same user ID, which is a
cluster signal on top of the device-level ones.

### 5. Refresh `.env` `VM_NAME`

The `.env` in `phone/waydroid/` is gitignored, so the clone inherits the
source's copy verbatim — including `VM_NAME`. Point it at the new IP so ntfy
notifications are distinguishable.

```bash
cd ~/tiktok-scraper/phone/waydroid

EXT_IP=$(curl -sf -m 2 -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip \
    2>/dev/null || hostname)
sed -i "s/^VM_NAME=.*/VM_NAME=vps-$EXT_IP/" .env
grep VM_NAME .env
```

### 6. Clear the inherited `http_proxy` setting

The source VPS is actively scraping, so `Settings.Global.http_proxy` is set
to `127.0.0.1:8080` (pointing at its mitmdump). The clone inherits that
setting, but has no mitmdump running — so TikTok tries to send HTTPS through
a dead proxy and shows "No network connection" on the login screen.

Clear it before launching TikTok. The scraper's `mobile_scrape.py` re-sets
the proxy itself on every run (see `adb reverse tcp:8080` + `settings put
global http_proxy 127.0.0.1:8080` in `_start_mitm`), so clearing here
doesn't break the scraper later.

```bash
$ADB shell settings put global http_proxy :0
$ADB shell settings delete global http_proxy
$ADB shell settings get global http_proxy    # should print: null
```

### 7. Launch TikTok and log in with a FRESH account

```bash
$ADB shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity
```

Then VNC to `<NEW_IP>:5900` (no password) and sign in with an account that
has never been used on another VPS. Reusing an account across clones
re-links them at the account layer even if every device identifier is now
unique.

### 8. Verify the clone diverges from the source

Before starting the scraper, dump the fingerprint and diff against the source:

```bash
# On the new clone
bash ~/tiktok-scraper/phone/waydroid/device_fingerprint.sh > /tmp/fp_clone.txt
# On the source (from your laptop)
ssh -i ~/.ssh/jamescvermont jamescvermont@<SOURCE_IP> \
    'bash ~/tiktok-scraper/phone/waydroid/device_fingerprint.sh' > /tmp/fp_source.txt
scp jamescvermont@<NEW_IP>:/tmp/fp_clone.txt /tmp/
diff /tmp/fp_source.txt /tmp/fp_clone.txt
```

Expected diffs: `android_id`, `bluetooth_address`, `device_name`,
`persist.radio.imei`, eth0 MAC, `boot_id`, uptime, TikTok
`firstInstallTime`/`lastUpdateTime`. Anything in the **per-clone** list that
still matches is something this procedure missed — fix before scraping.

Expected matches (fleet-wide, intentional): the masquerade section (Pixel 6
props) and most of the LEAK section (Waydroid tells, x86 cpuinfo, etc.).

### 9. Start the scraper

```bash
cd ~/tiktok-scraper/phone/waydroid
nohup bash run.sh >> /tmp/scraper.log 2>&1 &
tail -f /tmp/scraper.log
```

## Troubleshooting

**Waydroid won't start after MAC change:** typo in the new MAC line. Check
`sudo grep hwaddr /var/lib/waydroid/lxc/waydroid/config` — must be
`lxc.net.0.hwaddr = 00:16:3e:XX:XX:XX` with colons and lowercase hex.

**`adb devices` shows `offline` after `waydroid-start.sh`:** same as the
source VPS — re-run `sudo bash /usr/local/bin/waydroid-start.sh` (it
re-authorizes the ADB key). See
[WAYDROID_VPS_SETUP.md](WAYDROID_VPS_SETUP.md#troubleshooting) for the full
list — everything there still applies; only the *first-time* init is
different on a disk clone.

**TikTok immediately signs in as the source's account on first launch:**
step 4 was skipped or `pm clear` didn't take (race with the running app).
`am force-stop` first, then `pm clear`, then relaunch.

**TikTok login screen shows "No network connection" (clone can ping 8.8.8.8
fine):** step 6 was skipped. The inherited `http_proxy=127.0.0.1:8080`
points at a mitmdump that only runs on the source. Run step 6's three adb
commands, then relaunch TikTok (`am force-stop` + `am start`). The clone
can reach the internet — only HTTPS is broken, because the system-wide
proxy swallows it.

**Host `/etc/hostname` still shows the source's hostname:** cosmetic,
doesn't affect TikTok. Change via `sudo hostnamectl set-hostname <new>` if
you want it for your own bookkeeping.
