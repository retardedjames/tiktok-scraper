"""
Pre-flight checks for the TikTok scraper — EMULATOR variant.

Connects to an Android emulator running on Windows via ADB over TCP.
Supports two connection strategies (tried in order):

  1. Shared ADB server (Android Studio AVDs / any emulator):
     On Windows, run once per session:
       adb kill-server && adb -a nodaemon server
     This makes the Windows ADB server listen on all interfaces so WSL2 can
     reach it.  We connect with: adb -H <host-ip> -P 5037

  2. Direct emulator TCP port (LDPlayer=5555, MuMu=7555, AVD=5554):
     Some emulators expose ADB directly on a TCP port reachable from WSL2
     (e.g. after a netsh portproxy rule).  We try: adb connect <host-ip>:<port>

Override port: EMULATOR_ADB_PORT=7555 python3 batch_scrape.py

Verifies and auto-heals:
  - ADB TCP connection to Windows-side emulator
  - mitmproxy installed and port 8080 free
  - TikTok Lite installed on emulator
  - Stale proxy cleared
"""
import os
import socket
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Set ADB_DEVICE to skip auto-detection and use a specific device directly.
# e.g. ADB_DEVICE=127.0.0.1:5555 when running on the VM itself.
ADB_DEVICE = os.environ.get("ADB_DEVICE", "")

EMULATOR_ADB_PORT = int(os.environ.get("EMULATOR_ADB_PORT", "5554"))


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _adb(cmd: str) -> subprocess.CompletedProcess:
    base = ["adb", "-s", ADB_DEVICE] if ADB_DEVICE else ["adb"]
    return _run(base + cmd.split())


def _ok(msg: str):   print(f"  [✓] {msg}")
def _warn(msg: str): print(f"  [!] {msg}")
def _fail(msg: str): print(f"  [✗] {msg}")


def _find_powershell() -> str | None:
    for name in ("powershell.exe", "pwsh.exe"):
        r = _run(["which", name])
        if r.returncode == 0:
            return name
    for path in ("/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
                 "/mnt/c/Program Files/PowerShell/7/pwsh.exe"):
        if os.path.exists(path):
            return path
    return None


def _windows_host_ip() -> str:
    """Return the Windows host IP as seen from WSL2 (from /etc/resolv.conf nameserver)."""
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                if line.startswith("nameserver"):
                    return line.split()[1]
    except Exception:
        pass
    return "172.17.0.1"  # fallback


# ── individual checks ─────────────────────────────────────────────────────────

def check_adb_device(auto_fix: bool = True) -> bool:
    """Connect to the emulator via ADB TCP."""
    # If ADB_DEVICE is set, connect directly and skip auto-detection
    if ADB_DEVICE:
        _run(["adb", "connect", ADB_DEVICE])
        r = _run(["adb", "-s", ADB_DEVICE, "get-state"])
        if r.returncode == 0 and "device" in r.stdout:
            _ok(f"ADB device connected: {ADB_DEVICE}")
            return True
        _fail(f"ADB_DEVICE={ADB_DEVICE} not reachable (state: {r.stdout.strip() or r.stderr.strip()})")
        return False

    r = _run(["adb", "devices"])
    devices = [l for l in r.stdout.splitlines()[1:] if l.strip() and "device" in l]
    if devices:
        _ok(f"ADB device already connected: {devices[0].split()[0]}")
        return True

    if not auto_fix:
        _fail("No ADB device found.")
        return False

    host_ip = _windows_host_ip()
    _run(["adb", "kill-server"])
    time.sleep(0.5)

    # Strategy 0: restart Windows ADB server with -a flag so it listens on all interfaces
    _warn("Restarting Windows ADB server to listen on all interfaces...")
    ps = _find_powershell()
    if ps:
        _run([ps, "-Command", "adb kill-server; Start-Process adb -ArgumentList '-a','nodaemon','server' -WindowStyle Hidden"])
        time.sleep(1.5)
    else:
        _warn("powershell.exe not found — skipping Windows ADB server restart")

    # Strategy 1: connect to shared Windows ADB server (works with Android Studio AVDs)
    _warn(f"Trying shared ADB server at {host_ip}:5037 ...")
    env = os.environ.copy()
    env["ANDROID_ADB_SERVER_ADDRESS"] = host_ip
    env["ANDROID_ADB_SERVER_PORT"] = "5037"
    r = subprocess.run(["adb", "devices"], capture_output=True, text=True, env=env)
    devices = [l for l in r.stdout.splitlines()[1:] if l.strip() and "device" in l]
    if devices:
        _ok(f"Connected via shared ADB server: {devices[0].split()[0]}")
        # Persist env override for this process so subsequent adb() calls use it
        os.environ["ANDROID_ADB_SERVER_ADDRESS"] = host_ip
        os.environ["ANDROID_ADB_SERVER_PORT"] = "5037"
        return True

    # Strategy 2: direct TCP connect to emulator port
    target = f"{host_ip}:{EMULATOR_ADB_PORT}"
    _warn(f"Trying direct TCP connect to {target} ...")
    _run(["adb", "start-server"])
    time.sleep(0.5)
    r = _run(["adb", "connect", target])
    _warn(f"adb connect: {r.stdout.strip()}")
    time.sleep(1.0)

    r = _run(["adb", "devices"])
    devices = [l for l in r.stdout.splitlines()[1:] if l.strip() and "device" in l]
    if devices:
        _ok(f"ADB connected to emulator at {target}: {devices[0].split()[0]}")
        return True

    _fail(f"Could not connect to emulator.")
    _fail("Ensure the emulator is running on Windows, then run in PowerShell:")
    _fail("  adb kill-server && adb -a nodaemon server")
    _fail("That makes the ADB server reachable from WSL2 (needed for Android Studio AVDs).")
    _fail(f"Or set a portproxy for direct connect: EMULATOR_ADB_PORT=5554")
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
    _fail("mitmdump not found. Run: pip install mitmproxy")
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
    _fail("TikTok Lite (com.tiktok.lite.go) not found on emulator.")
    _fail("Install it via: adb install <apk>")
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
    r = _adb("shell ls /data/misc/user/0/cacerts-added/ 2>/dev/null")
    if r.stdout.strip():
        _ok(f"User CA certs present: {r.stdout.strip()[:60]}")
        return True
    _warn("Cannot confirm mitmproxy cert is installed on emulator.")
    _warn("Push and install: adb push ~/.mitmproxy/mitmproxy-ca-cert.pem /sdcard/mitmproxy-ca.pem")
    _warn("Then: Settings → Security → Install certificate → CA cert")
    return True  # warn-only


# ── main entry ────────────────────────────────────────────────────────────────

def run_all(require_device: bool = True) -> bool:
    print("[*] Running preflight checks (emulator mode)...")
    results = []

    results.append(check_port_8080())
    results.append(check_mitmproxy())

    device_ok = check_adb_device(auto_fix=True)
    results.append(device_ok)

    if device_ok:
        results.append(check_tiktok_installed())
        results.append(check_screen_on())
        results.append(check_stale_proxy())
        check_mitmproxy_cert()

    all_ok = all(results)
    if all_ok:
        print("[*] Preflight OK — ready to scrape.\n")
    else:
        print("[!] Preflight FAILED — fix issues above before running.\n")
    return all_ok


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
