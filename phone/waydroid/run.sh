#!/bin/bash
# Start the Waydroid VPS scraper.
# Pulls latest from git (sparse-checkout ensures only phone/waydroid/ is updated),
# then runs scrape_forever.py.
# Usage: bash run.sh [--debug]

set -e
cd "$(dirname "$0")"

# Load .env (ADB_DEVICE, VM_NAME, NTFY_TOPIC, etc.)
if [ -f .env ]; then
    set -a; source .env; set +a
fi

# Pull latest — sparse-checkout config confines this to phone/waydroid/
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -n "$REPO_ROOT" ]; then
    git -C "$REPO_ROOT" fetch --quiet origin main
    git -C "$REPO_ROOT" reset --hard origin/main --quiet
fi

exec python3 scrape_forever.py "$@"
