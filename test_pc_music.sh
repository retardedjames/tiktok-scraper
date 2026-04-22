#!/bin/bash
# Test: scrape "PC Music" with 33 scrolls, show capture counts, no DB write.
# Run on the VM: bash test_pc_music.sh
set -a; source "$(dirname "$0")/.env"; set +a
cd "$(dirname "$0")"
python3 mobile_scrape.py "PC Music" --no-db
