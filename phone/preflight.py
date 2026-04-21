"""
Pre-flight checks for the PHONE (USB + usbipd → WSL2 → ADB) scraper.

Verifies and auto-heals:
  - ADB server running and at least one device connected
  - usbipd auto-attach (from .usbipd_device.json cache) if no device
  - mitmproxy installed and port 8080 free
  - TikTok Lite installed on the phone
  - Phone screen on / stale proxy cleared
  - mitmproxy CA cert present (warn-only)

Adapted from the old ~/tiktoks project's preflight.py, kept compatible with
the new phone/ scraper (imports `coords` / supports ADB_DEVICE env override).
"""
import json
import os
import re
import socket
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
USBIPD_CACHE = os.path.join(SCRIPT_DIR, ".usbipd_device.json")

ADB_DEVICE = os.environ.get("ADB_DEVICE", "")


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _adb(cmd: str) -> subprocess.CompletedProcess:
    base = ["adb", "-s", ADB_DEVICE] if ADB_DEVICE else ["adb"]
    return _run(base + cmd.split())


def _ok(msg: str):   print(f"  [\u2713] {msg}")
def _warn(msg: str): print(f"  [!] {msg}")
def _fail(msg: str): print(f"  [\u2717] {msg}")


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


def _load_usbipd_cache() -> dict:
    try:
        with open(USBIPD_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_usbipd_cache(cache: dict):
    try:
        with open(USBIPD_CACHE, "w") as f:
            json.dump(cache, f, indent=2)
    except Exception:
        pass


def _parse_usbipd_list(output: str) -> list[dict]:
    entries = []
    for line in output.splitlines():
        line = line.strip()
        m = re.match(
            r"^(\d+-\d+)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.*?)\s+"
            r"(Not shared|Shared|Attached|Perturbed)$",
            line,
        )
        if m:
            entries.append({
                "busid":  m.group(1),
                "vidpid": m.group(2),
                "device": m.group(3).strip(),
                "state":  m.group(4),
            })
    return entries


def _usbipd_attach() -> bool:
    """Auto-attach the phone to WSL via usbipd.

    Strategy:
      1. Use the busid saved from a previous successful session.
      2. Match by VID:PID saved previously (survives port changes).
      3. Heuristic: first Shared/Attached device with an Android VID or
         'adb'/'android' in its name.
    """
    ps = _find_powershell()
    if not ps:
        _warn("powershell.exe not found — cannot auto-attach via usbipd.")
        return False

    r = _run([ps, "-Command", "usbipd list"])
    if r.returncode != 0:
        _warn(f"usbipd list failed (is usbipd-win installed?): {r.stderr.strip()}")
        return False

    cache = _load_usbipd_cache()
    saved_busid = cache.get("busid")
    saved_vidpid = cache.get("vidpid")

    parsed = _parse_usbipd_list(r.stdout)
    target = None

    if saved_busid:
        for entry in parsed:
            if entry["busid"] == saved_busid and entry["state"] in ("Shared", "Attached"):
                target = entry
                _warn(f"Using saved busid {saved_busid} ({entry['device']})")
                break

    if not target and saved_vidpid:
        for entry in parsed:
            if entry["vidpid"] == saved_vidpid and entry["state"] in ("Shared", "Attached"):
                target = entry
                _warn(f"Matched saved VID:PID {saved_vidpid} at busid {entry['busid']}")
                break

    if not target:
        ANDROID_VIDS = {"18d1", "04e8", "22b8", "2717", "2a70", "0bb4", "12d1", "19d2"}
        for entry in parsed:
            if entry["state"] not in ("Shared", "Attached"):
                continue
            vid = entry["vidpid"].split(":")[0].lower()
            name = entry["device"].lower()
            if vid in ANDROID_VIDS or "android" in name or "adb" in name:
                target = entry
                _warn(f"Heuristic match: {entry['busid']} {entry['vidpid']} '{entry['device']}'")
                break

    if not target:
        _warn("No bindable Android device found in usbipd list.")
        for entry in parsed:
            _warn(f"  {entry['busid']}  {entry['vidpid']}  {entry['device']}  [{entry['state']}]")
        _warn("If your phone is listed but state is 'Not shared':")
        _warn("  PowerShell (Admin): usbipd bind --busid <busid>")
        return False

    r = _run([ps, "-Command", f"usbipd attach --wsl --busid {target['busid']}"])
    if r.returncode == 0:
        _ok(f"usbipd attached busid {target['busid']}")
        cache.update({"busid": target["busid"], "vidpid": target["vidpid"],
                      "device": target["device"]})
        _save_usbipd_cache(cache)
        return True
    err = r.stderr.strip() or r.stdout.strip()
    _warn(f"usbipd attach failed: {err}")
    if "not shared" in err.lower() or "not bound" in err.lower():
        _fail(f"Device not bound. PowerShell (Admin): usbipd bind --busid {target['busid']}")
    return False


def check_adb_device(auto_fix: bool = True) -> bool:
    """Ensure at least one ADB device is online. Tries server restart + usbipd."""
    r = _run(["adb", "devices"])
    devices = [l for l in r.stdout.splitlines()[1:] if l.strip() and "device" in l]
    if devices:
        serial = devices[0].split()[0]
        _ok(f"ADB device connected: {serial}")
        cache = _load_usbipd_cache()
        cache["adb_serial"] = serial
        _save_usbipd_cache(cache)
        return True

    if not auto_fix:
        _fail("No ADB device found.")
        return False

    _warn("No ADB device — restarting ADB server...")
    _run(["adb", "kill-server"])
    time.sleep(0.5)
    _run(["adb", "start-server"])
    time.sleep(1.0)

    r = _run(["adb", "devices"])
    devices = [l for l in r.stdout.splitlines()[1:] if l.strip() and "device" in l]
    if devices:
        _ok(f"ADB device connected after server restart: {devices[0].split()[0]}")
        return True

    _warn("Still no device — attempting usbipd auto-attach...")
    if _usbipd_attach():
        time.sleep(2.0)
        _run(["adb", "start-server"])
        time.sleep(1.0)
        r = _run(["adb", "devices"])
        devices = [l for l in r.stdout.splitlines()[1:] if l.strip() and "device" in l]
        if devices:
            _ok(f"ADB device connected via usbipd: {devices[0].split()[0]}")
            return True

    _fail("No ADB device after all recovery attempts.")
    _fail("Manual steps:")
    _fail("  1. PowerShell (Admin): usbipd bind --busid <id>  [one-time per device]")
    _fail("  2. PowerShell:         usbipd attach --wsl --busid <id>")
    _fail("  3. On phone: confirm the ADB authorization popup")
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
    _fail("TikTok Lite (com.tiktok.lite.go) not found on device.")
    _fail("Install it from the Play Store, or: adb install <apk>")
    return False


def check_screen_on() -> bool:
    """Wake the screen if off so UI automation works."""
    r = _adb("shell dumpsys power")
    if "mWakefulness=Awake" in r.stdout or "Display Power: state=ON" in r.stdout:
        _ok("Screen is on")
        return True
    _warn("Screen off — sending WAKEUP keyevent...")
    _adb("shell input keyevent KEYCODE_WAKEUP")
    time.sleep(0.8)
    _adb("shell input keyevent KEYCODE_MENU")  # dismiss simple swipe lock
    time.sleep(0.5)
    _ok("Wakeup sent (unlock by PIN if required)")
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
    """Warn-only: best-effort check that the mitmproxy CA cert is installed."""
    r = _adb("shell ls /data/misc/user/0/cacerts-added/ 2>/dev/null")
    if r.stdout.strip():
        _ok(f"User CA certs present: {r.stdout.strip()[:60]}")
        return True
    _warn("Cannot confirm mitmproxy cert is installed on device.")
    _warn("Install: adb push ~/.mitmproxy/mitmproxy-ca-cert.pem /sdcard/mitmproxy-ca.pem")
    _warn("Then on phone: Settings \u2192 Security \u2192 Install certificate \u2192 CA certificate")
    return True


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
    print("[*] Running preflight checks (phone/USB)...")
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
