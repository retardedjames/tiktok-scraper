#!/bin/bash
# Start the phone scraper on a Waydroid VPS.
# Pulls latest code from git, then runs scrape_forever.py.
# Usage: bash run.sh [--debug]

set -e
cd "$(dirname "$0")"

# Load .env if present (sets ADB_DEVICE, VM_NAME, NTFY_TOPIC, etc.)
if [ -f .env ]; then
    set -a; source .env; set +a
fi

git -C .. fetch --quiet && git -C .. reset --hard origin/main

exec python3 scrape_forever.py "$@"
