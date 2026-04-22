#!/usr/bin/env bash
# Per-clone setup script for a Waydroid VPS spun up from the base image.
#
# Run ONCE on each new VM after spinning up from the machine snapshot.
# The base image has all packages installed but Waydroid is NOT initialized.
# This script completes the setup and makes the VM ready for TikTok scraping.
#
# Usage:
#   bash ~/tiktok-scraper/phone/vps_clone_init.sh
#
# After this script finishes:
#   1. Connect via VNC (<ip>:5900) and log into TikTok with a FRESH account
#   2. Create .env: cp ~/tiktok-scraper/phone/.env.example ~/tiktok-scraper/phone/.env
#      (edit VM_NAME to something unique, e.g. VPS1)
#   3. Start scraper: nohup bash ~/tiktok-scraper/phone/run.sh >> /tmp/scraper.log 2>&1 &

set -e
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
hdr()  { echo -e "\n${BOLD}-- $* --${NC}"; }

REPO=~/tiktok-scraper
ADB="adb -s 127.0.0.1:5556"

# ── 1. Kernel modules ─────────────────────────────────────────────────────────
hdr "Kernel modules"
sudo modprobe binder_linux
sudo chmod 666 /dev/udmabuf
ok "binder_linux loaded, udmabuf permissions set"

# ── 2. Waydroid init (downloads ~3 GB Android images) ────────────────────────
hdr "Waydroid init (GAPPS, ~3 GB — takes several minutes)"
sudo waydroid init -s GAPPS
ok "Waydroid initialized"

# ── 3. Patch LXC config for udmabuf ──────────────────────────────────────────
hdr "LXC config"
NODES_FILE="/var/lib/waydroid/lxc/waydroid/config_nodes"
if ! grep -q udmabuf "$NODES_FILE" 2>/dev/null; then
    echo "lxc.mount.entry = /dev/udmabuf dev/udmabuf none bind,create=file,optional 0 0" \
        | sudo tee -a "$NODES_FILE" > /dev/null
    ok "udmabuf mount entry added"
else
    ok "udmabuf already in LXC config"
fi

# ── 4. Generate mitmproxy CA cert ────────────────────────────────────────────
hdr "mitmproxy CA cert"
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
if [[ ! -f "$CERT" ]]; then
    warn "Generating CA cert..."
    PATH=$PATH:$HOME/.local/bin timeout 5 mitmdump --listen-port 18888 &>/dev/null || true
    sleep 2
fi
if [[ -f "$CERT" ]]; then
    ok "CA cert: $CERT"
else
    warn "CA cert not found — run 'mitmdump' once and Ctrl+C, then re-run this script"
    exit 1
fi

# ── 5. Install startup script ─────────────────────────────────────────────────
hdr "Startup script"
sudo cp "$REPO/waydroid-start.sh" /usr/local/bin/waydroid-start.sh
sudo chmod +x /usr/local/bin/waydroid-start.sh
ok "waydroid-start.sh installed"

# ── 6. Boot Waydroid stack ────────────────────────────────────────────────────
hdr "Starting Waydroid (takes 2-3 min)"
sudo bash /usr/local/bin/waydroid-start.sh
ok "Waydroid stack started"

# ── 7. Wait for Android boot ──────────────────────────────────────────────────
hdr "Waiting for Android to boot"
TIMEOUT=180
ELAPSED=0
while ! $ADB shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do
    sleep 3; ELAPSED=$((ELAPSED+3))
    if [[ $ELAPSED -ge $TIMEOUT ]]; then
        warn "Timed out waiting for Android boot — check waydroid-start.sh logs"
        exit 1
    fi
done
ok "Android booted"

# ── 8. Authorize ADB key ──────────────────────────────────────────────────────
hdr "ADB key authorization"
ADBKEY=$(cat ~/.android/adbkey.pub 2>/dev/null || adb keygen ~/.android/adbkey && cat ~/.android/adbkey.pub)
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c \
    "mkdir -p /data/misc/adb && echo \"$ADBKEY\" > /data/misc/adb/adb_keys && chmod 640 /data/misc/adb/adb_keys"
ok "ADB key authorized"

# ── 9. Set screen resolution (720x1612 @ 280dpi — matches phone/ coords) ──────
hdr "Screen resolution"
$ADB shell wm size 720x1612
$ADB shell wm density 280
ok "720x1612 @ 280dpi set"

# ── 10. Install libhoudini (ARM translation) ──────────────────────────────────
hdr "libhoudini (ARM→x86 translation)"
WAYDROID_SCRIPT_DIR="/tmp/waydroid_script"
if [[ ! -d "$WAYDROID_SCRIPT_DIR" ]]; then
    git clone https://github.com/casualsnek/waydroid_script "$WAYDROID_SCRIPT_DIR"
fi
sudo pip3 install tqdm InquirerPy --break-system-packages -q
sudo python3 "$WAYDROID_SCRIPT_DIR/main.py" install libhoudini
ok "libhoudini installed"

# ── 11. Masquerade as Pixel 6 ─────────────────────────────────────────────────
hdr "Pixel 6 masquerade"
sudo waydroid session stop
sudo python3 "$REPO/emulator/masquerade_buildprop.py"
ok "Build props patched — restarting Waydroid"
sudo bash /usr/local/bin/waydroid-start.sh > /tmp/waydroid_session.log 2>&1 &
sleep 30
BRAND=$(sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- getprop ro.product.brand 2>/dev/null)
MODEL=$(sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- getprop ro.product.model 2>/dev/null)
ok "Device reports: brand=$BRAND model=$MODEL"

# ── 12. Re-mount mitmproxy cert (session stop cleared it) ────────────────────
hdr "Re-mounting mitmproxy CA cert"
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
    umount /system/etc/security/cacerts 2>/dev/null || true
    mkdir -p /tmp/cacerts
    cp /system/etc/security/cacerts/* /tmp/cacerts/
    cp /sdcard/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0
    chmod 644 /tmp/cacerts/c8750f0d.0
    mount --bind /tmp/cacerts /system/etc/security/cacerts
    echo cert_mounted
' && ok "CA cert mounted" || warn "CA cert mount failed — check /sdcard/mitmproxy-ca.pem"

# ── 13. Randomize android_id ──────────────────────────────────────────────────
hdr "Randomize android_id"
NEW_ID=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 16)
$ADB shell settings put secure android_id "$NEW_ID"
ok "android_id = $NEW_ID"

# ── 14. Wipe TikTok app data ──────────────────────────────────────────────────
hdr "Wipe TikTok session"
$ADB shell am force-stop com.tiktok.lite.go 2>/dev/null || true
$ADB shell pm clear com.tiktok.lite.go 2>/dev/null || true
ok "TikTok app data cleared"

# ── 15. Install TikTok Lite APK ───────────────────────────────────────────────
hdr "Install TikTok Lite (patched splits)"
APK_DIR="$REPO/phone/patched"
$ADB install-multiple \
    "$APK_DIR/base_patched.apk" \
    "$APK_DIR/config.arm64_v8a.apk" \
    "$APK_DIR/config.en.apk" \
    "$APK_DIR/config.mdpi.apk" \
    "$APK_DIR/df_edit_effects.apk" \
    "$APK_DIR/df_edit_filter.apk" \
    "$APK_DIR/df_edit_sticker.apk" \
    "$APK_DIR/df_fusing.apk" \
    "$APK_DIR/df_record_prop.apk" \
    "$APK_DIR/post_video.apk"
ok "TikTok Lite installed"

# ── 16. Launch TikTok for first-run ──────────────────────────────────────────
hdr "Launch TikTok"
$ADB shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity
sleep 5
ok "TikTok launched — proceed to VNC for login"

echo ""
echo -e "${BOLD}=======================================================${NC}"
echo -e "${BOLD} Initialization complete — manual steps remaining${NC}"
echo -e "${BOLD}=======================================================${NC}"
echo ""
echo "1. Connect via VNC: <this-ip>:5900 (no password)"
echo "   If blank screen: sudo bash /usr/local/bin/waydroid-start.sh"
echo ""
echo "2. Log into TikTok with a FRESH account (not used on any other VM)."
echo "   If 'No internet': the mitmproxy cert may be wrong — check step 12 above."
echo ""
echo "3. Create .env:"
echo "   cp ~/tiktok-scraper/phone/.env.example ~/tiktok-scraper/phone/.env"
echo "   nano ~/tiktok-scraper/phone/.env   # set VM_NAME to something unique"
echo ""
echo "4. Start scraper:"
echo "   nohup bash ~/tiktok-scraper/phone/run.sh >> /tmp/scraper.log 2>&1 &"
echo "   tail -f /tmp/scraper.log"
echo ""
echo "5. Set ADB_DEVICE in .env if needed:"
echo "   ADB_DEVICE=127.0.0.1:5556"
