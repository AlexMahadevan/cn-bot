#!/bin/bash
# Wrapper for the launchd job (com.alexmahadevan.cn-bot). It does two things:
#
#   1. Runs the bot under a HARD TIMEOUT. If a run hangs — e.g. the laptop
#      sleeps mid-request and a socket read never returns — it self-kills
#      instead of staying alive forever. This matters because launchd's
#      StartCalendarInterval will NOT start a new run while a previous one
#      is still alive, so one frozen run silently blocks every future
#      scheduled slot until it's killed by hand.
#
#   2. PUBLISHES the dashboard after every run: regenerate the snapshot from
#      notes.db, commit, and push. Pushing triggers the GitHub Pages rebuild,
#      so the public site stays current automatically. (Previously scheduled
#      runs only wrote to the DB and the site went stale until a manual push.)
#
# Note: no `set -e` — we deliberately publish even if the bot run fails or
# times out, so partial progress still reaches the site.
set -uo pipefail

REPO="/Users/alexmahadevan/python_projects/template-api-note-writer"
cd "$REPO"

# Homebrew installs the watchdog as `timeout`/`gtimeout`, but those dirs are
# not on launchd's minimal PATH — so search known locations and fall back to
# running without a watchdog rather than failing outright.
TIMEOUT_BIN=""
for cand in timeout gtimeout \
            /opt/homebrew/bin/timeout /opt/homebrew/bin/gtimeout \
            /usr/local/bin/timeout /usr/local/bin/gtimeout; do
    if command -v "$cand" >/dev/null 2>&1; then TIMEOUT_BIN="$cand"; break; fi
done

# 1. Run the bot. PYTHONPATH lets imports resolve from src/.
#    15m ceiling (a healthy 50-post run takes ~2-3 min); --kill-after sends
#    SIGKILL 30s after SIGTERM in case the process is wedged in a syscall.
if [ -n "$TIMEOUT_BIN" ]; then
    PYTHONPATH=src "$TIMEOUT_BIN" --kill-after=30s 15m \
        "$REPO/.venv/bin/python" "$REPO/src/main.py" --num-posts 50 \
        || echo "[run-bot] bot run failed or timed out (exit $?); publishing anyway."
else
    echo "[run-bot] WARNING: no timeout binary found; running without watchdog."
    PYTHONPATH=src "$REPO/.venv/bin/python" "$REPO/src/main.py" --num-posts 50 \
        || echo "[run-bot] bot run failed (exit $?); publishing anyway."
fi

# 2. Publish the snapshot. refresh.sh regenerates bot.json/candidate_pool.json
#    and commits only if something changed; we push so Pages rebuilds.
bash "$REPO/dashboard/tools/refresh.sh" || echo "[run-bot] refresh.sh failed (exit $?)."
git push || echo "[run-bot] git push failed (exit $?); will retry next run."
