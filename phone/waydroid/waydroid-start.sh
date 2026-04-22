#!/bin/bash
# Waydroid full-stack startup script.
# Run as root: sudo bash /usr/local/bin/waydroid-start.sh
# This file lives locally at emulator/waydroid-start.sh — copy to GCP2 at /usr/local/bin/waydroid-start.sh
set -e

WAYDROID_USER=jamescvermont
XDG=/run/user/1001

echo "[*] Ensuring binder_linux module is loaded..."
modprobe binder_linux 2>/dev/null || true

echo "[*] Ensuring binderfs is mounted..."
if ! mountpoint -q /dev/binderfs 2>/dev/null; then
    mkdir -p /dev/binderfs
    mount -t binder binder /dev/binderfs
fi

echo "[*] Setting up udmabuf..."
chmod 666 /dev/udmabuf 2>/dev/null || true

echo "[*] Setting up XDG runtime dir..."
mkdir -p $XDG/pulse
touch $XDG/pulse/native
chown -R $WAYDROID_USER $XDG 2>/dev/null || true

echo "[*] Starting sway headless compositor..."
pkill sway 2>/dev/null || true; pkill weston 2>/dev/null || true; sleep 1
sudo -u $WAYDROID_USER env \
    WLR_BACKENDS=headless \
    WLR_LIBINPUT_NO_DEVICES=1 \
    XDG_RUNTIME_DIR=$XDG \
    WAYLAND_DISPLAY=wayland-1 \
    sway --config /dev/null > /tmp/sway.log 2>&1 &
for i in $(seq 1 10); do
    [ -S $XDG/wayland-1 ] && echo "[*] sway socket ready" && break
    sleep 1
done
if [ ! -S $XDG/wayland-1 ]; then
    echo "[!] sway failed — log:"; cat /tmp/sway.log; exit 1
fi

echo "[*] Starting waydroid-container service..."
systemctl start waydroid-container
sleep 2

echo "[*] Starting waydroid session..."
pkill -f 'waydroid session' 2>/dev/null || true; sleep 1
sudo -u $WAYDROID_USER env XDG_RUNTIME_DIR=$XDG WAYLAND_DISPLAY=wayland-1 \
    waydroid session start > /tmp/waydroid_session.log 2>&1 &

echo "[*] Waiting for Android container to be RUNNING (up to 3 min)..."
for i in $(seq 1 60); do
    STATE=$(sudo -u $WAYDROID_USER env XDG_RUNTIME_DIR=$XDG waydroid status 2>/dev/null | grep Container | awk '{print $2}')
    echo "  [$i] Container: ${STATE:-unknown}"
    [ "$STATE" = "RUNNING" ] && echo "[*] Container RUNNING!" && break
    sleep 3
done

echo "[*] Starting wayvnc on port 5900..."
pkill wayvnc 2>/dev/null || true; sleep 1
sudo -u $WAYDROID_USER env XDG_RUNTIME_DIR=$XDG WAYLAND_DISPLAY=wayland-1 \
    wayvnc 0.0.0.0 5900 > /tmp/wayvnc.log 2>&1 &
sleep 2
ss -tlnp | grep -q 5900 && echo "[*] wayvnc listening on :5900" || echo "[!] wayvnc failed — check /tmp/wayvnc.log"

echo "[*] Waiting for adbd on port 5555 (up to 2 min)..."
for i in $(seq 1 40); do
    CPID=$(lxc-info -P /var/lib/waydroid/lxc -n waydroid 2>/dev/null | grep 'PID:' | awk '{print $2}')
    if [ -n "$CPID" ]; then
        nsenter -t $CPID -n -- ss -tlnp 2>/dev/null | grep -q 5555 && echo "[*] adbd ready!" && break
    fi
    sleep 3
done

# Pre-authorize host ADB key in container BEFORE any adb command is run below.
# Without this, `adb connect` returns "unauthorized" and the cert push fails,
# which `set -e` propagates up and kills the caller (vps_clone_init.sh).
ADBPUB=/home/$WAYDROID_USER/.android/adbkey.pub
if [ ! -f "$ADBPUB" ]; then
    echo "[*] Generating host ADB keypair..."
    sudo -u $WAYDROID_USER mkdir -p /home/$WAYDROID_USER/.android
    sudo -u $WAYDROID_USER adb keygen "$ADBPUB" 2>/dev/null || \
        sudo -u $WAYDROID_USER sh -c 'adb start-server >/dev/null 2>&1; adb kill-server >/dev/null 2>&1'
fi
if [ -f "$ADBPUB" ]; then
    echo "[*] Pre-authorizing host ADB key in Waydroid container..."
    ADBKEY=$(cat "$ADBPUB")
    lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c \
        "mkdir -p /data/misc/adb && echo \"$ADBKEY\" > /data/misc/adb/adb_keys && chmod 640 /data/misc/adb/adb_keys"
    # Force client to reconnect so adbd re-reads the keys file
    sudo -u $WAYDROID_USER adb kill-server >/dev/null 2>&1 || true
    sleep 1
fi

echo "[*] Setting up socat ADB proxy on port 5556..."
CPID=$(lxc-info -P /var/lib/waydroid/lxc -n waydroid 2>/dev/null | grep 'PID:' | awk '{print $2}')
if [ -z "$CPID" ]; then
    echo "[!] Container not running. Session log:"; cat /tmp/waydroid_session.log; exit 1
fi
pkill socat 2>/dev/null || true; sleep 1
printf '#!/bin/bash\nexec nsenter -t %s -n socat STDIO TCP4:127.0.0.1:5555\n' "$CPID" \
    > /usr/local/bin/adb-waydroid-proxy.sh
chmod +x /usr/local/bin/adb-waydroid-proxy.sh
socat TCP-LISTEN:5556,fork,bind=0.0.0.0 EXEC:/usr/local/bin/adb-waydroid-proxy.sh \
    > /tmp/socat_adb.log 2>&1 &

echo "[*] Ensuring mitmproxy CA cert is on /sdcard..."
HOST_CERT=/home/$WAYDROID_USER/.mitmproxy/mitmproxy-ca-cert.pem
if [ ! -f "$HOST_CERT" ]; then
    echo "[!] Host cert missing at $HOST_CERT — run 'mitmdump --listen-port 18888 &; sleep 4; kill %1' as $WAYDROID_USER to generate."
    exit 1
fi
sudo -u $WAYDROID_USER adb connect 127.0.0.1:5556 >/dev/null 2>&1
if ! sudo -u $WAYDROID_USER adb -s 127.0.0.1:5556 shell 'ls /sdcard/mitmproxy-ca.pem' 2>/dev/null | grep -q mitmproxy-ca; then
    echo "[*] Pushing host cert to /sdcard/mitmproxy-ca.pem..."
    sudo -u $WAYDROID_USER adb -s 127.0.0.1:5556 push "$HOST_CERT" /sdcard/mitmproxy-ca.pem
fi

echo "[*] Installing mitmproxy CA cert..."
# umount first so a re-run of this script always gets a fresh bind-mount with the
# correct cert — without this, a second run copies stale files from the already-mounted
# tmpfs back into itself and the old cert stays in place.
lxc-attach -P /var/lib/waydroid/lxc -n waydroid -- sh -c '
  umount /system/etc/security/cacerts 2>/dev/null || true
  mkdir -p /tmp/cacerts
  cp /system/etc/security/cacerts/* /tmp/cacerts/
  cp /sdcard/mitmproxy-ca.pem /tmp/cacerts/c8750f0d.0
  chmod 644 /tmp/cacerts/c8750f0d.0
  mount --bind /tmp/cacerts /system/etc/security/cacerts
  echo cert_mounted
'

# show-full-ui runs AFTER adbd is ready — Android userspace is booted by this point.
# Running it earlier causes "Failed to get service waydroidplatform" because the
# Android binder service isn't registered until userspace finishes booting.
echo "[*] Launching Android UI via show-full-ui..."
sudo -u $WAYDROID_USER env XDG_RUNTIME_DIR=$XDG WAYLAND_DISPLAY=wayland-1 \
    waydroid show-full-ui > /tmp/waydroid_ui.log 2>&1 &
sleep 5
tail -3 /tmp/waydroid_ui.log 2>/dev/null || true

VM_IP=$(curl -sf http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip -H 'Metadata-Flavor: Google' 2>/dev/null || hostname -I | awk '{print $1}')
echo "[*] All done!"
echo "[*] VNC: ${VM_IP}:5900  (TigerVNC or RealVNC, no password)"
echo "[*] ADB: adb connect ${VM_IP}:5556"
