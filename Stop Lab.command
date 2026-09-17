#!/bin/bash
# Double-click in Finder, or run: ./"Stop Lab.command"
set -e
export PATH="$PATH:$HOME/.local/bin:$HOME/.docker/bin:/opt/homebrew/bin:/usr/local/bin"
cd -- "$(dirname -- "$0")"
lab_root=$(pwd -P)

trap 'lab_exit_code=$?; echo "Shutdown incomplete. Check the error above; database volumes were not deleted."; if [[ -t 0 ]]; then read -r -p "Press Return to close… " lab_reply; fi; exit "$lab_exit_code"' ERR

echo "[1/2] Stopping this project's dashboard and experiment workers…"
lab_pids=()
lab_candidates=$(pgrep -f 'streamlit.*run.*scripts/app[.]py') || [[ $? -eq 1 ]]
for lab_pid in $lab_candidates; do
    lab_cwd=$(lsof -a -p "$lab_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    if [[ -z "$lab_cwd" ]] && kill -0 "$lab_pid" 2>/dev/null; then
        echo "Cannot identify the project for dashboard PID $lab_pid. Close its Terminal and retry."
        false
    fi
    if [[ "$lab_cwd" == "$lab_root" ]]; then
        lab_pids+=("$lab_pid")
        kill -TERM "$lab_pid" 2>/dev/null || true
    fi
done

# Wait for the workers to exit before removing containers they could restart.
for lab_attempt in {1..15}; do
    lab_running=()
    for lab_pid in "${lab_pids[@]}"; do
        if kill -0 "$lab_pid" 2>/dev/null; then
            lab_running+=("$lab_pid")
        fi
    done
    lab_pids=("${lab_running[@]}")
    if [[ ${#lab_pids[@]} -eq 0 ]]; then break; fi
    sleep 1
done
for lab_pid in "${lab_pids[@]}"; do
    lab_cwd=$(lsof -a -p "$lab_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
    if [[ "$lab_cwd" == "$lab_root" ]]; then
        echo "Dashboard PID $lab_pid did not exit within 15 seconds; forcing it to stop."
        kill -KILL "$lab_pid" 2>/dev/null || true
    fi
done
if [[ ${#lab_pids[@]} -gt 0 ]]; then
    sleep 1
    for lab_pid in "${lab_pids[@]}"; do
        if kill -0 "$lab_pid" 2>/dev/null; then
            echo "PID $lab_pid is still present. Close its dashboard Terminal and retry shutdown."
            false
        fi
    done
fi

echo "[2/2] Stopping and removing this lab's MongoDB containers…"
if ! docker info >/dev/null 2>&1; then
    echo "Docker is unavailable. The dashboard shutdown was attempted, but container shutdown could not be verified."
    false
fi
# Removing containers/networks also clears old isolation faults; named volumes remain.
docker compose -p mongo-local-lab -f "$lab_root/compose.local.yml" down --volumes --timeout 20
echo "Lab stopped. Results/ logs are preserved."
echo "Next time, double-click Start Lab.command."
