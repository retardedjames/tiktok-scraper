#!/usr/bin/env bash
# Build a TikTok Lite APK that trusts user CA certs (so mitmproxy can MITM it).
#
# Run this on an x86_64 Linux box (apktool's bundled aapt2 is x86_64-only;
# debian's system aapt2 is too old to handle TikTok's targetSdk 35).
# Tested host: GCP VM 34.162.181.247.
#
# Inputs:  ../apkm_extracted/  (all .apk splits from apkmirror)
# Outputs: ./patched/          (10 signed APKs + keystore)

set -euo pipefail

WORKDIR="${1:-$(pwd)/apkpatch}"
APKM_DIR="${APKM_DIR:-$(cd "$(dirname "$0")/.." && pwd)/apkm_extracted}"
OUT_DIR="${OUT_DIR:-$(cd "$(dirname "$0")" && pwd)/patched}"
APKTOOL_VER="2.9.3"
APKTOOL_URL="https://github.com/iBotPeaches/Apktool/releases/download/v${APKTOOL_VER}/apktool_${APKTOOL_VER}.jar"
KS_PASS="tiktokpw"

REQUIRED_SPLITS=(
    split_config.arm64_v8a
    split_config.en
    split_config.xxhdpi
    split_df_edit_effects
    split_df_edit_filter
    split_df_edit_sticker
    split_df_fusing
    split_df_record_prop
    split_post_video
)

mkdir -p "$WORKDIR" "$OUT_DIR"
cd "$WORKDIR"

[[ -f apktool.jar ]] || wget -q "$APKTOOL_URL" -O apktool.jar

cp "$APKM_DIR/base.apk" .
for s in "${REQUIRED_SPLITS[@]}"; do cp "$APKM_DIR/${s}.apk" .; done

java -Xmx6g -jar apktool.jar d -f base.apk -o base_decoded

# Patch network_security_config: add <certificates src="user"/> to base-config.
python3 - <<'PY'
p = "base_decoded/res/xml/h.xml"
s = open(p).read()
old = '            <certificates overridePins="true" src="system" />\n        </trust-anchors>\n    </base-config>'
new = '            <certificates overridePins="true" src="system" />\n            <certificates overridePins="true" src="user" />\n        </trust-anchors>\n    </base-config>'
assert old in s, "NSC pattern not found — TikTok layout changed?"
open(p, "w").write(s.replace(old, new))
PY

# Two layout XMLs decompile with orphan attributes (` ="0"`, ` ="true"`) that
# aapt2 rejects. Strip them — they're not on the scrape path.
for f in base_decoded/res/layout/a5m.xml base_decoded/res/layout/nf.xml; do
    [[ -f $f ]] && sed -i -E 's/ ="[^"]*"//g' "$f"
done

java -Xmx6g -jar apktool.jar b base_decoded -o base_patched.apk --use-aapt2

[[ -f tt.keystore ]] || keytool -genkey -v -keystore tt.keystore -alias tt \
    -keyalg RSA -keysize 2048 -validity 3650 \
    -storepass "$KS_PASS" -keypass "$KS_PASS" \
    -dname "CN=tt, OU=tt, O=tt, L=x, S=x, C=US"

sign_apk() {
    local apk=$1
    zipalign -p -f 4 "$apk" "${apk}.aligned" && mv "${apk}.aligned" "$apk"
    apksigner sign --ks tt.keystore --ks-pass "pass:$KS_PASS" --key-pass "pass:$KS_PASS" "$apk"
}

sign_apk base_patched.apk
for s in "${REQUIRED_SPLITS[@]}"; do sign_apk "${s}.apk"; done

cp base_patched.apk "${REQUIRED_SPLITS[@]/%/.apk}" tt.keystore "$OUT_DIR/"
echo
echo "Patched APKs: $OUT_DIR"
echo "Install:  adb install-multiple $(ls "$OUT_DIR"/*.apk | tr '\n' ' ')"
