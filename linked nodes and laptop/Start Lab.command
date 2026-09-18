#!/bin/bash
# Double-click in Finder, or run: ./"Start Lab.command"
set -e
export PYTHONDONTWRITEBYTECODE=1
export PATH="$PATH:$HOME/.local/bin:$HOME/.docker/bin:/opt/homebrew/bin:/usr/local/bin"
cd -- "$(dirname -- "$0")"
export UV_CACHE_DIR="$PWD/.uv-cache"

trap 'lab_exit_code=$?; echo "Startup stopped. Check the error above and the README startup steps."; if [[ -t 0 ]]; then read -r -p "Press Return to close… " lab_reply; fi; exit "$lab_exit_code"' ERR

for tool in uv docker; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Missing $tool. Install it using the first-time setup in README.md."
        false
    fi
done
docker compose version >/dev/null

echo "[1/4] Starting Docker Desktop if needed…"
if ! docker info >/dev/null 2>&1; then
    docker desktop start --timeout 120
    for attempt in {1..60}; do
        if docker info >/dev/null 2>&1; then break; fi
        sleep 2
    done
    docker info >/dev/null
fi

echo "[2/4] Installing the locked dependencies into .venv…"
uv sync --locked

echo "[3/4] Starting this standalone copy in local mode…"
uv run --locked scripts/setup_lab.py --startup

echo "[4/4] Opening http://127.0.0.1:8502"
echo "Keep this Terminal window open. Press Ctrl+C here to stop the UI."
echo "When finished, double-click Stop Lab.command in this folder."
uv run --locked streamlit run scripts/app.py --server.headless false
