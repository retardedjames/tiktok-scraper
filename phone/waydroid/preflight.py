"""
Pre-flight checks for the Waydroid VPS scraper.

Adapted from phone/preflight.py — strips usbipd auto-attach (not applicable),
adds Waydroid-specific checks:
  - Auto `adb connect` to the socat proxy (default 127.0.0.1:5556)
  - mitmproxy CA cert is mounted as a system CA in the container
  - Stale proxy cleared + mitmproxy installed + port 8080 free + TikTok installed
  - Screen on + stay-awake
  - coords loaded & reported
"""
import os
import socket
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Waydroid default: socat proxy on the host side of the container
ADB_DEVICE = os.environ.get("ADB_DEVICE", "127.0.0.1:5556")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _adb(cmd: str) -> subprocess.CompletedProcess:
    base = ["adb", "-s", ADB_DEVICE] if ADB_DEVICE else ["adb"]
    return _run(base + cmd.split())


def _ok(msg: str):   print(f"  [✓] {msg}")
def _warn(msg: str): print(f"  [!] {msg}")
def _fail(msg: str): print(f"  [✗] {msg}")


def check_adb_device(auto_fix: bool = True) -> bool:
    """Ensure ADB_DEVICE is reachable; auto `adb connect` if not."""
    r = _run(["adb", "-s", ADB_DEVICE, "get-state"])
    if r.returncode == 0 and "device" in r.stdout:
        _ok(f"ADB device connected: {ADB_DEVICE}")
        return True

    if not auto_fix:
        _fail(f"ADB_DEVICE={ADB_DEVICE} not reachable.")
        return False

    _warn(f"Connecting to {ADB_DEVICE} ...")
    r = _run(["adb", "connect", ADB_DEVICE])
    _warn(f"adb connect: {r.stdout.strip()}")
    time.sleep(1.0)

    r = _run(["adb", "-s", ADB_DEVICE, "get-state"])
    if r.returncode == 0 and "device" in r.stdout:
        _ok(f"ADB device connected: {ADB_DEVICE}")
        return True

    _fail(f"Could not reach {ADB_DEVICE}.")
    _fail("Check that Waydroid is running: sudo bash /usr/local/bin/waydroid-start.sh")
    _fail("Check that socat proxy is active: ss -tlnp | grep 5556")
    return False


def check_mitmproxy() -> bool:
    mitmdump = os.path.expanduser("~/.local/bin/mitmdump")
    if os.path.exists(mitmdump):
        _ok(f"mitmdump found: {mitmdump}")
        return True
    r = _run(["which", "mitmdump"])
    if r.returncode == 0:
        _ok(f"mitmdump found: {r.stdout.strip()}")
        return True
    _fail("mitmdump not found. Run: pip install --break-system-packages mitmproxy")
    return False


def check_port_8080() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 8080), timeout=0.5).close()
        _warn("Port 8080 in use — killing...")
        subprocess.run("fuser -k 8080/tcp 2>/dev/null || true", shell=True)
        time.sleep(0.4)
        _ok("Port 8080 freed")
    except OSError:
        _ok("Port 8080 free")
    return True


def check_tiktok_installed() -> bool:
    r = _adb("shell pm list packages com.tiktok.lite.go")
    if "com.tiktok.lite.go" in r.stdout:
        _ok("TikTok Lite installed")
        return True
    _fail("TikTok Lite (com.tiktok.lite.go) not found in Waydroid.")
    _fail("Run: bash ~/tiktok-scraper/waydroid/vps_clone_init.sh (or re-install APKs)")
    return False


def check_screen_on() -> bool:
    """Emulator screen is always 'on' but we still send wakeup to be safe."""
    _adb("shell input keyevent KEYCODE_WAKEUP")
    time.sleep(0.3)
    _ok("Screen wakeup sent")
    return True


def check_stale_proxy() -> bool:
    r = _adb("shell settings get global http_proxy")
    val = r.stdout.strip()
    if val and val not in ("null", ":0", ""):
        _warn(f"Stale proxy found ({val}) — clearing...")
        _adb("shell settings put global http_proxy :0")
        _adb("shell settings delete global http_proxy")
        _ok("Stale proxy cleared")
    else:
        _ok("No stale proxy")
    return True


def check_mitmproxy_cert() -> bool:
    """Verify the mitmproxy CA is mounted in the Waydroid system CA store.

    On Waydroid the cert is bind-mounted as /system/etc/security/cacerts/c8750f0d.0
    by waydroid-start.sh on every boot. We compare md5s with the host cert.
    """
    host_cert = os.path.expanduser("~/.mitmproxy/mitmproxy-ca-cert.pem")
    if not os.path.exists(host_cert):
        _fail(f"Host mitmproxy cert missing: {host_cert}")
        return False

    host_md5 = _run(["md5sum", host_cert]).stdout.split()[0] if _run(["md5sum", host_cert]).stdout else ""
    r = _run(["sudo", "lxc-attach", "-P", "/var/lib/waydroid/lxc", "-n", "waydroid",
              "--", "md5sum", "/system/etc/security/cacerts/c8750f0d.0"])
    android_md5 = r.stdout.split()[0] if r.returncode == 0 and r.stdout else ""

    if host_md5 and host_md5 == android_md5:
        _ok(f"mitmproxy cert mounted in Waydroid system CA ({host_md5[:8]})")
        return True
    _warn(f"mitmproxy cert mismatch (host={host_md5[:8]}, android={android_md5[:8] or 'missing'})")
    _warn("Re-run: sudo bash /usr/local/bin/waydroid-start.sh")
    return False


def report_coords():
    """Print the coords we'd use, for visibility before the user starts."""
    try:
        import coords as coords_mod
        base = ["adb", "-s", ADB_DEVICE] if ADB_DEVICE else ["adb"]
        c = coords_mod.load(base)
        _ok(f"Coords: {c.source}")
    except Exception as e:
        _warn(f"Could not load coords: {e}")


def run_all(require_device: bool = True) -> bool:
    print(f"[*] Running preflight checks (Waydroid — ADB={ADB_DEVICE})...")
    results = []

    results.append(check_port_8080())
    results.append(check_mitmproxy())

    device_ok = check_adb_device(auto_fix=True)
    results.append(device_ok)

    if device_ok:
        results.append(check_tiktok_installed())
        results.append(check_screen_on())
        results.append(check_stale_proxy())
        check_mitmproxy_cert()  # warn-only
        report_coords()

    all_ok = all(results)
    if all_ok:
        print("[*] Preflight OK — ready to scrape.\n")
    else:
        print("[!] Preflight FAILED — fix issues above before running.\n")
    return all_ok


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
