#!/usr/bin/env bash
# Per-clone init for a Waydroid VPS spun from the base image.
#
# The base image has all apt/pip packages installed but Waydroid is NOT
# initialized. This script completes the setup and makes the VM ready to
# scrape. Run it once on each new clone.
#
# Usage:
#   bash ~/tiktok-scraper/phone/waydroid/vps_clone_init.sh
#
# After this script finishes:
#   1. VNC to <this-ip>:5900 and log into TikTok with a FRESH account.
#   2. Start scraper: nohup bash run.sh >> /tmp/scraper.log 2>&1 &
# .env is generated automatically (VM_NAME derived from external IP / hostname).
# A systemd unit (waydroid-stack.service) auto-restarts the stack on reboot.

set -e
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
hdr()  { echo -e "\n${BOLD}-- $* --${NC}"; }

SELF_DIR=$(cd "$(dirname "$0")" && pwd)
ADB="adb -s 127.0.0.1:5556"

# ── 1. Kernel modules ─────────────────────────────────────────────────────────
hdr "Kernel modules"
sudo modprobe binder_linux
sudo chmod 666 /dev/udmabuf
ok "binder_linux loaded, udmabuf permissions set"

# ── 2. Waydroid init (downloads ~3 GB Android images) ────────────────────────
hdr "Waydroid init (GAPPS, ~3 GB — takes several minutes)"
if [[ ! -f /var/lib/waydroid/images/system.img ]]; then
    sudo waydroid init -s GAPPS
    ok "Waydroid initialized"
else
    ok "Waydroid already initialized — skipping"
fi

# ── 3. Patch LXC config for udmabuf ──────────────────────────────────────────
hdr "LXC config"
NODES_FILE="/var/lib/waydroid/lxc/waydroid/config_nodes"
if ! sudo grep -q udmabuf "$NODES_FILE" 2>/dev/null; then
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
    warn "CA cert not found — run 'mitmdump' once, Ctrl+C, then re-run this script"
    exit 1
fi

# ── 5. Install startup script ─────────────────────────────────────────────────
hdr "Startup script"
sudo cp "$SELF_DIR/waydroid-start.sh" /usr/local/bin/waydroid-start.sh
sudo chmod +x /usr/local/bin/waydroid-start.sh
ok "waydroid-start.sh installed"

# ── 6. Boot Waydroid ──────────────────────────────────────────────────────────
hdr "Starting Waydroid (takes 2-3 min)"
sudo bash /usr/local/bin/waydroid-start.sh
ok "Waydroid stack started"

# ── 7. Wait for Android boot ──────────────────────────────────────────────────
hdr "Waiting for Android to boot"
TIMEOUT=180; ELAPSED=0
while ! $ADB shell getprop sys.boot_completed 2>/dev/null | grep -q 1; do
    sleep 3; ELAPSED=$((ELAPSED+3))
    if [[ $ELAPSED -ge $TIMEOUT ]]; then
        warn "Timed out waiting for Android boot — check waydroid-start.sh logs"
        exit 1
    fi
done
ok "Android booted"

# ── 8. Authorize ADB key (defensive — waydroid-start.sh already did this) ────
# waydroid-start.sh installs /data/misc/adb/adb_keys before its own cert push;
# re-running here is idempotent and covers the case where the key changed.
hdr "ADB key authorization (defensive re-check)"
if [[ ! -f ~/.android/adbkey.pub ]]; then
    adb keygen ~/.android/adbkey
fi
ADBKEY=$(cat ~/.android/adbkey.pub)
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c \
    "mkdir -p /data/misc/adb && echo \"$ADBKEY\" > /data/misc/adb/adb_keys && chmod 640 /data/misc/adb/adb_keys"
ok "ADB key authorized"

# ── 9. Screen resolution — 1080×2400 @ 420dpi (Pixel 6 native) ────────────────
hdr "Screen resolution (1080×2400 @ 420dpi, matches Pixel 6 masquerade)"
$ADB shell wm size 1080x2400
$ADB shell wm density 420
ok "1080×2400 @ 420dpi set"

# ── 10. Install libhoudini (ARM translation) ─────────────────────────────────
hdr "libhoudini (ARM→x86 translation)"
if sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- ls /system/lib/libhoudini.so 2>/dev/null | grep -q libhoudini; then
    ok "libhoudini already installed — skipping"
else
    WS_DIR="/tmp/waydroid_script"
    if [[ ! -d "$WS_DIR" ]]; then
        git clone https://github.com/casualsnek/waydroid_script "$WS_DIR"
    fi
    sudo pip3 install tqdm InquirerPy --break-system-packages -q
    sudo python3 "$WS_DIR/main.py" install libhoudini
    ok "libhoudini installed"
fi

# ── 11. Masquerade as Pixel 6 ─────────────────────────────────────────────────
# Idempotent: if the overlay build.prop already reports Pixel 6 AND the
# arm64-v8a CPU ABI rewrite is in place, we skip the re-run. Both must
# match — otherwise an old snapshot baked before the ABI fix (commit
# 07cd0b2, 2026-04-22) would wrongly skip and ship with host_abi=x86_64.
hdr "Pixel 6 masquerade"
MASQ="$SELF_DIR/masquerade_buildprop.py"
OVERLAY_BP=/var/lib/waydroid/overlay_rw/system/system/build.prop
if sudo grep -q '^ro\.product\.system\.model=Pixel 6$' "$OVERLAY_BP" 2>/dev/null \
   && sudo grep -q '^ro\.product\.cpu\.abi=arm64-v8a$' "$OVERLAY_BP" 2>/dev/null; then
    ok "build.prop overlay already reports Pixel 6 + arm64-v8a — skipping"
else
    sudo waydroid session stop
    sudo python3 "$MASQ"
    ok "Build props patched — restarting Waydroid"
    # waydroid-start.sh writes /tmp/waydroid_session.log itself (as root, owned 0644).
    # Don't re-redirect to that path from this (user) shell — it fails with
    # "Permission denied" when the file already exists from step 6. waydroid-start.sh
    # also internally waits for sys.boot_completed, so we don't need a separate sleep.
    sudo bash /usr/local/bin/waydroid-start.sh
fi
BRAND=$(sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- getprop ro.product.brand 2>/dev/null)
MODEL=$(sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- getprop ro.product.model 2>/dev/null)
ok "Device reports: brand=$BRAND model=$MODEL"

# Re-mount mitmproxy cert (session stop cleared the tmpfs bind mount)
hdr "Re-mounting mitmproxy CA cert"
sudo lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
    umount /system/etc/security/cacerts 2>/dev/null || true
    mkdir -p /tmp/cacerts
    cp /system/etc/security/cacerts/* /tmp/cacerts/
    cp /data/local/tmp/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0
    chmod 644 /tmp/cacerts/c8750f0d.0
    mount --bind /tmp/cacerts /system/etc/security/cacerts
    echo cert_mounted
' && ok "CA cert mounted" || warn "CA cert mount failed — check /data/local/tmp/mitmproxy-ca.pem"

# ── 12. Randomize android_id ──────────────────────────────────────────────────
hdr "Randomize android_id"
NEW_ID=$(cat /dev/urandom | tr -dc 'a-f0-9' | head -c 16)
$ADB shell settings put secure android_id "$NEW_ID"
ok "android_id = $NEW_ID"

# ── 13. Wipe TikTok app data ──────────────────────────────────────────────────
hdr "Wipe TikTok session"
$ADB shell am force-stop com.tiktok.lite.go 2>/dev/null || true
$ADB shell pm clear com.tiktok.lite.go 2>/dev/null || true
ok "TikTok app data cleared"

# ── 14. Install TikTok Lite ──────────────────────────────────────────────────
# Idempotent: if a base snapshot already baked the APK, skip the re-install
# (adb install-multiple would otherwise re-push ~150 MB of splits).
hdr "Install TikTok Lite (patched splits)"
APK_DIR="$SELF_DIR/patched"
if $ADB shell pm list packages 2>/dev/null | grep -q '^package:com.tiktok.lite.go$'; then
    ok "TikTok Lite already installed — skipping"
else
    if [[ ! -f "$APK_DIR/base_patched.apk" ]]; then
        warn "Patched APKs not found in $APK_DIR"
        warn "rclone them in and re-run: bash vps_clone_init.sh --skip-to-tiktok-install"
        exit 1
    fi
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
fi

# ── 15. Launch TikTok for first-run ──────────────────────────────────────────
hdr "Launch TikTok"
$ADB shell am start -n com.tiktok.lite.go/com.ss.android.ugc.aweme.main.homepage.MainActivity
sleep 5
ok "TikTok launched — proceed to VNC for login"

# ── 16. Generate .env ────────────────────────────────────────────────────────
# VM_NAME uniqueness is what ntfy messages key on. Derive it from the GCP
# external IP (falls back to hostname) so clones get distinct names with no
# manual editing. Always rewrite VM_NAME — a clone booted from a machine image
# inherits the source VM's .env, so "leave alone if exists" would ship the
# wrong name.
hdr "Generate .env"
EXT_IP=$(curl -sf -m 2 -H 'Metadata-Flavor: Google' \
    http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip \
    2>/dev/null || true)
VM_NAME_VAL="vps-${EXT_IP:-$(hostname)}"
if [[ ! -f "$SELF_DIR/.env" ]]; then
    sed "s/^VM_NAME=.*/VM_NAME=$VM_NAME_VAL/" "$SELF_DIR/.env.example" > "$SELF_DIR/.env"
    ok ".env written with VM_NAME=$VM_NAME_VAL"
else
    sed -i "s/^VM_NAME=.*/VM_NAME=$VM_NAME_VAL/" "$SELF_DIR/.env"
    ok ".env VM_NAME updated to $VM_NAME_VAL (other settings preserved)"
fi

# ── 17. Install systemd unit so the stack survives reboots ───────────────────
# waydroid-container.service alone is not enough: the session/sway/wayvnc/socat
# layers need waydroid-start.sh. Without this unit the VPS comes back from a
# reboot with "waydroid-container active" but adb offline — see WAYDROID_VPS_SETUP.md.
hdr "systemd auto-restart unit"
sudo tee /etc/systemd/system/waydroid-stack.service > /dev/null <<EOF
[Unit]
Description=Waydroid full stack (session + sway + wayvnc + adb proxy + cert)
After=network-online.target waydroid-container.service
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/waydroid-start.sh
# If Android userspace fails to boot, waydroid-start.sh exits 1 and the whole
# unit fails — which is what we want (no silent adb-offline state).
TimeoutStartSec=420

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable waydroid-stack.service
ok "waydroid-stack.service enabled — stack auto-starts on boot"

echo ""
echo -e "${BOLD}=======================================================${NC}"
echo -e "${BOLD} Initialization complete${NC}"
echo -e "${BOLD}=======================================================${NC}"
echo ""
echo "Only ONE manual step remains (TikTok login):"
echo ""
echo "  1. VNC to <this-ip>:5900 (no password), log into TikTok with a"
echo "     FRESH account (never used on another VM)."
echo ""
echo "  2. Start scraper:"
echo "       cd $SELF_DIR"
echo "       nohup bash run.sh >> /tmp/scraper.log 2>&1 &"
echo "       tail -f /tmp/scraper.log"
echo ""
echo "Fingerprint this VM for bot-detection audits:"
echo "  bash $SELF_DIR/device_fingerprint.sh"
