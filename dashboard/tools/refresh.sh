#!/usr/bin/env bash
# Regenerate the static bot.json snapshot from the bot's live audit DB
# and commit it so the next push triggers a Pages rebuild.
#
# Run this whenever you want the public dashboard to reflect fresh bot data.
# (Otherwise the dashboard shows whatever was last committed.)
set -euo pipefail
cd "$(dirname "$0")/../.."  # repo root

python3 dashboard/tools/build_bot_json.py > dashboard/src/data/bot.json
python3 dashboard/tools/build_candidate_pool_json.py > dashboard/src/data/candidate_pool.json
# Re-extract the live system prompts from source so the public "The prompts"
# page can never drift from what the bot actually runs.
python3 dashboard/tools/build_prompts_json.py > dashboard/src/data/prompts.json

git add dashboard/src/data/bot.json dashboard/src/data/candidate_pool.json dashboard/src/data/prompts.json
if git diff --staged --quiet; then
    echo "No changes to dashboard data — skipping commit."
else
    git commit -m "Refresh dashboard data snapshot"
    echo "Committed. Run 'git push' to trigger a Pages rebuild."
fi
