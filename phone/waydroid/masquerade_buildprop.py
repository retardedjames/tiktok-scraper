#!/usr/bin/env python3
"""
Masquerade Waydroid's build.prop files to appear as a Google Pixel 6.

Usage (on the GCP VM):
    sudo waydroid session stop
    python3 masquerade_buildprop.py
    sudo bash /usr/local/bin/waydroid-start.sh &

What this does:
- Reads each rootfs build.prop
- Rewrites brand/model/manufacturer/device/name to google/Pixel 6/Google/oriole/oriole
  across all partition namespaces (system, system_ext, vendor, product, odm, odm_dlkm,
  vendor_dlkm, system_dlkm)
- Rewrites ro.build.fingerprint + related build identity to a real Pixel 6 release
  (Android 13 TQ3A.230901.001, build 10750268)
- Writes results to /var/lib/waydroid/overlay_rw/{system,vendor}/... which the
  Waydroid container's overlayfs will pick up on next session start

Target device was chosen because it's a common, plausible phone on Android 13,
and the Waydroid rootfs already has ro.build.id=TQ3A.230901.001 so no Android
version mismatch is introduced.

Why this matters:
TikTok's API requests include device_brand, device_type, and a cdid/X-Argus
signature derived from Build.BRAND/MODEL/FINGERPRINT. A stock Waydroid image
broadcasts "waydroid" in every request, which is trivially detectable. Reporting
as a real Pixel 6 makes the fingerprint much harder to flag.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# system.img mounts at rootfs/system/ with a system/ subdir inside it;
# vendor.img mounts at rootfs/vendor/ with files directly at that level.
ROOTFS_SYS = Path("/var/lib/waydroid/rootfs/system")
ROOTFS_VND = Path("/var/lib/waydroid/rootfs/vendor")
OVERLAY_SYS = Path("/var/lib/waydroid/overlay_rw/system")
OVERLAY_VND = Path("/var/lib/waydroid/overlay_rw/vendor")

FILES = [
    # (rootfs source, overlay destination relative to its overlay upperdir)
    (ROOTFS_SYS / "system/build.prop", OVERLAY_SYS / "system/build.prop"),
    (ROOTFS_SYS / "system/system_ext/etc/build.prop", OVERLAY_SYS / "system/system_ext/etc/build.prop"),
    (ROOTFS_SYS / "system/product/etc/build.prop", OVERLAY_SYS / "system/product/etc/build.prop"),
    (ROOTFS_SYS / "system/system_dlkm/etc/build.prop", OVERLAY_SYS / "system/system_dlkm/etc/build.prop"),
    (ROOTFS_VND / "build.prop", OVERLAY_VND / "build.prop"),
    (ROOTFS_VND / "odm/etc/build.prop", OVERLAY_VND / "odm/etc/build.prop"),
    (ROOTFS_VND / "odm_dlkm/etc/build.prop", OVERLAY_VND / "odm_dlkm/etc/build.prop"),
    (ROOTFS_VND / "vendor_dlkm/etc/build.prop", OVERLAY_VND / "vendor_dlkm/etc/build.prop"),
]

NS = "system|system_ext|vendor|product|odm|oem|odm_dlkm|vendor_dlkm|system_dlkm"
REPLACEMENTS = [
    (rf"^(ro\.product\.(?:{NS})\.brand)=.*", r"\1=google"),
    (rf"^(ro\.product\.(?:{NS})\.manufacturer)=.*", r"\1=Google"),
    (rf"^(ro\.product\.(?:{NS})\.model)=.*", r"\1=Pixel 6"),
    (rf"^(ro\.product\.(?:{NS})\.device)=.*", r"\1=oriole"),
    (rf"^(ro\.product\.(?:{NS})\.name)=.*", r"\1=oriole"),
    (r"^ro\.product\.brand=.*", "ro.product.brand=google"),
    (r"^ro\.product\.manufacturer=.*", "ro.product.manufacturer=Google"),
    (r"^ro\.product\.model=.*", "ro.product.model=Pixel 6"),
    (r"^ro\.product\.device=.*", "ro.product.device=oriole"),
    (r"^ro\.product\.name=.*", "ro.product.name=oriole"),
    (r"^ro\.product\.board=.*", "ro.product.board=slider"),
    (r"^ro\.build\.fingerprint=.*", "ro.build.fingerprint=google/oriole/oriole:13/TQ3A.230901.001/10750268:user/release-keys"),
    (r"^ro\.build\.display\.id=.*", "ro.build.display.id=TQ3A.230901.001"),
    (r"^ro\.build\.version\.incremental=.*", "ro.build.version.incremental=10750268"),
    (r"^ro\.build\.type=.*", "ro.build.type=user"),
    (r"^ro\.build\.user=.*", "ro.build.user=android-build"),
    (r"^ro\.build\.host=.*", "ro.build.host=abfarm-builder"),
    (r"^ro\.build\.tags=.*", "ro.build.tags=release-keys"),
    (r"^ro\.build\.flavor=.*", "ro.build.flavor=oriole-user"),
    (r"^ro\.build\.product=.*", "ro.build.product=oriole"),
    (r"^ro\.build\.description=.*", "ro.build.description=oriole-user 13 TQ3A.230901.001 10750268 release-keys"),
    (r"^ro\.product\.first_api_level=.*", "ro.product.first_api_level=32"),
    (r"^ro\.vendor\.build\.fingerprint=.*", "ro.vendor.build.fingerprint=google/oriole/oriole:13/TQ3A.230901.001/10750268:user/release-keys"),
    (r"^ro\.bootimage\.build\.fingerprint=.*", "ro.bootimage.build.fingerprint=google/oriole/oriole:13/TQ3A.230901.001/10750268:user/release-keys"),
]

def transform(text: str) -> str:
    for pat, repl in REPLACEMENTS:
        text = re.sub(pat, repl, text, flags=re.MULTILINE)
    return text

def main():
    if os.geteuid() != 0:
        print("must run as root (sudo)", file=sys.stderr)
        sys.exit(1)

    status = subprocess.run(["waydroid", "status"], capture_output=True, text=True).stdout
    if "Session:\tSTOPPED" not in status:
        print("Waydroid session must be STOPPED before running this.", file=sys.stderr)
        print("Run: sudo waydroid session stop", file=sys.stderr)
        sys.exit(1)

    for src, dst in FILES:
        if not src.exists():
            print(f"SKIP (missing): {src}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(transform(src.read_text()))
        dst.chmod(0o644)
        shutil.chown(dst, "root", "root")
        print(f"wrote {dst}")

    print("\nDone. Start Waydroid: sudo bash /usr/local/bin/waydroid-start.sh")

if __name__ == "__main__":
    main()
