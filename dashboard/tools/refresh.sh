#!/usr/bin/env bash
# Regenerate the static bot.json snapshot from the bot's live audit DB
# and commit it so the next push triggers a Pages rebuild.
#
# Run this whenever you want the public dashboard to reflect fresh bot data.
# (Otherwise the dashboard shows whatever was last committed.)
set -euo pipefail
cd "$(dirname "$0")/../.."  # repo root

python3 dashboard/tools/build_bot_json.py > dashboard/src/data/bot.json

git add dashboard/src/data/bot.json
if git diff --staged --quiet; then
    echo "No changes to bot.json — skipping commit."
else
    git commit -m "Refresh dashboard data snapshot"
    echo "Committed. Run 'git push' to trigger a Pages rebuild."
fi
