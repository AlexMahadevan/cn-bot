#!/bin/bash
# Wrapper for the launchd job. Runs the bot, then touches the dashboard
# data loader so Observable Framework's filesystem-based cache invalidates
# on the next page load.

set -e

REPO="/Users/alexmahadevan/python_projects/template-api-note-writer"
cd "$REPO"

# Run the bot. PYTHONPATH is set so imports resolve from src/.
PYTHONPATH=src "$REPO/.venv/bin/python" "$REPO/src/main.py" --num-posts 50

# Bust the dashboard data-loader cache by touching the loader script.
# Observable Framework re-runs the loader when its source file is newer
# than the cached output.
touch "$REPO/dashboard/src/data/bot.json.py"
