#!/usr/bin/env bash
# Setup script for the phone/USB TikTok scraper (WSL2 Ubuntu).
# Run once from the phone/ directory: bash setup.sh

set -e
BOLD='\033[1m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

ok()   { echo -e "${GREEN}[\xE2\x9C\x93]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
fail() { echo -e "${RED}[\xE2\x9C\x97]${NC} $*"; }
hdr()  { echo -e "\n${BOLD}-- $* --${NC}"; }

hdr "Python"
command -v python3 &>/dev/null || { fail "python3 not found"; exit 1; }
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "Python $PY_VER"

hdr "System packages"
MISSING=()
for pkg in adb psmisc curl wget unzip; do
    command -v $pkg &>/dev/null || MISSING+=($pkg)
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    warn "Installing: ${MISSING[*]}"
    sudo apt-get update -qq && sudo apt-get install -y -qq "${MISSING[@]}" 2>/dev/null
    ok "Installed: ${MISSING[*]}"
else
    ok "System packages already installed"
fi

hdr "Python packages"
pip3 install --quiet --break-system-packages \
    mitmproxy sqlalchemy psycopg2-binary
ok "Python packages installed"

hdr "mitmproxy CA certificate"
CERT="$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
if [[ -f "$CERT" ]]; then
    ok "mitmproxy CA cert exists: $CERT"
else
    warn "Generating CA cert..."
    timeout 3 mitmdump --listen-port 18888 &>/dev/null || true
    if [[ -f "$CERT" ]]; then
        ok "CA cert generated: $CERT"
    else
        fail "Could not generate CA cert. Run: mitmdump (Ctrl+C after it starts)"
    fi
fi

echo ""
echo -e "${BOLD}=======================================================${NC}"
echo -e "${BOLD} Manual steps for USB + Android setup${NC}"
echo -e "${BOLD}=======================================================${NC}"
echo ""
echo -e "${BOLD}1. Install usbipd-win (Windows, one-time):${NC}"
echo "   winget install --interactive --exact dorssel.usbipd-win"
echo ""
echo -e "${BOLD}2. Bind phone (Windows PowerShell as Admin, one-time per USB port):${NC}"
echo "   usbipd list                    # find your phone's BUSID"
echo "   usbipd bind --busid <busid>    # share with WSL"
echo ""
echo -e "${BOLD}3. Attach phone (PowerShell, each session / after unplug):${NC}"
echo "   usbipd attach --wsl --busid <busid>"
echo "   # preflight.py will auto-attach on subsequent runs"
echo ""
echo -e "${BOLD}4. Enable ADB debugging on the phone:${NC}"
echo "   Settings -> About phone -> tap 'Build number' 7 times"
echo "   Settings -> Developer options -> USB debugging ON"
echo "   Accept the RSA fingerprint prompt on the phone"
echo ""
echo -e "${BOLD}5. Install mitmproxy CA cert on the phone:${NC}"
echo "   adb push $CERT /sdcard/mitmproxy-ca.pem"
echo "   # Then on phone: Settings -> Security -> Install certificate -> CA cert"
echo "   # Navigate to /sdcard/mitmproxy-ca.pem and install"
echo ""
echo -e "${BOLD}6. Install TikTok Lite on the phone${NC}"
echo "   Play Store: 'TikTok Lite'  (package: com.tiktok.lite.go)"
echo ""
echo -e "${BOLD}7. Copy .env and edit if needed:${NC}"
echo "   cp .env.example .env"
echo ""
echo -e "${BOLD}8. Preflight + calibrate:${NC}"
echo "   python3 preflight.py          # confirms ADB, TikTok, cert, coords"
echo "   python3 calibrate.py --show   # dump current coords + screenshot"
echo "   python3 calibrate.py          # walkthrough to dial them in per screen"
echo ""
echo -e "${BOLD}9. Test run:${NC}"
echo "   python3 mobile_scrape.py 'street art'"
echo ""
echo -e "${BOLD}10. Continuous run (consumes queue forever):${NC}"
echo "   python3 scrape_forever.py"
echo ""
ok "Setup finished. Follow the manual steps above."
