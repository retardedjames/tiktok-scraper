#!/bin/bash
# Dump every identifier a TikTok SDK is likely to read off the device.
# Run on each VM to compare fingerprints — anything identical across VMs is a
# candidate for how they link/fingerprint the account fleet.
#
#   bash phone/waydroid/device_fingerprint.sh > /tmp/fp_$(hostname).txt
#   diff /tmp/fp_vpsA.txt /tmp/fp_vpsB.txt
#
# The first section (MASQUERADE) is what should LOOK like a real Pixel 6.
# The second section (LEAK) is where Waydroid/x86/LXC tells show through —
# these are the most likely bot signals and the first thing to fix.

set -e
ADB="adb -s ${ADB_DEVICE:-127.0.0.1:5556}"

gp() { $ADB shell getprop "$1" 2>/dev/null | tr -d '\r'; }
hr() { printf '\n===== %s =====\n' "$1"; }

hr "MASQUERADE (should look like a real Pixel 6)"
for p in \
  ro.product.model ro.product.manufacturer ro.product.brand ro.product.device \
  ro.product.name ro.product.board \
  ro.build.id ro.build.display.id ro.build.version.release ro.build.version.sdk \
  ro.build.version.security_patch ro.build.fingerprint ro.build.description \
  ro.build.flavor ro.build.tags ro.build.type ro.build.user ro.build.host \
  ro.build.date.utc ro.product.first_api_level \
  ro.vendor.build.fingerprint; do
  printf '%-40s %s\n' "$p" "$(gp "$p")"
done

hr "LEAK (these expose the emulator/VM if a TikTok SDK reads them)"
for p in \
  ro.system.build.fingerprint ro.system_ext.build.fingerprint \
  ro.odm.build.fingerprint ro.vendor_dlkm.build.fingerprint \
  ro.product.cpu.abi ro.product.cpu.abilist \
  ro.hardware ro.hardware.chipname ro.bootloader ro.serialno \
  ro.board.platform ro.dalvik.vm.native.bridge \
  ro.modversion ro.lineage.version ro.lineage.device ro.lineage.releasetype; do
  printf '%-40s %s\n' "$p" "$(gp "$p")"
done

hr "ANDROID_ID / SECURE SETTINGS"
echo "android_id         = $($ADB shell settings get secure android_id | tr -d '\r')"
echo "bluetooth_address  = $($ADB shell settings get secure bluetooth_address | tr -d '\r')"
echo "device_name        = $($ADB shell settings get global device_name | tr -d '\r')"

hr "NETWORK / MAC"
$ADB shell 'for n in /sys/class/net/*/address; do echo "  $(basename $(dirname $n))=$(cat $n)"; done' 2>/dev/null

hr "TELEPHONY"
for p in gsm.sim.operator.numeric gsm.sim.operator.alpha gsm.operator.numeric \
         gsm.operator.alpha gsm.network.type persist.radio.imei; do
  printf '%-40s %s\n' "$p" "$(gp "$p")"
done

hr "DISPLAY"
$ADB shell 'wm size; wm density; getprop ro.sf.lcd_density' 2>/dev/null | sed 's/\r//'

hr "CPU / KERNEL (strong VM tells)"
$ADB shell 'uname -a; grep -E "vendor_id|model name|flags" /proc/cpuinfo | head -5' 2>/dev/null | sed 's/\r//'

hr "TIMEZONE / LOCALE"
for p in persist.sys.timezone persist.sys.locale ro.product.locale; do
  printf '%-40s %s\n' "$p" "$(gp "$p")"
done

hr "GOOGLE SERVICES PRESENCE"
$ADB shell 'pm list packages | grep -E "com.google.android.(gms|gsf)$|com.android.vending"' 2>/dev/null | sed 's/\r//'

hr "BOOT ID / UPTIME"
$ADB shell 'cat /proc/sys/kernel/random/boot_id; uptime' 2>/dev/null | sed 's/\r//'

hr "TIKTOK APP VERSION"
$ADB shell dumpsys package com.tiktok.lite.go 2>/dev/null \
  | grep -E 'versionName|versionCode|firstInstallTime|lastUpdateTime' | head
