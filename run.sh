#!/bin/bash
# Start the scraper on a GCP Waydroid VM.
# Pulls latest code from git, then runs scrape_forever.py.
# Usage: bash run.sh [--min-likes 5000] [--debug]

set -e
cd "$(dirname "$0")"

# Load .env if present (sets ADB_DEVICE and any overrides)
if [ -f .env ]; then
    set -a; source .env; set +a
fi

git pull --ff-only

exec python3 scrape_forever.py "$@"
