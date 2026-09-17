"""Bounded Docker controls for this lab only. Also usable for terminal recovery."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.local.yml")]
NODES = ("mongo1", "mongo2", "mongo3")
LOCK = threading.RLock()


def command(args, timeout=40):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def container(node):
    if node not in NODES:
        raise ValueError("Unknown lab node.")
    identifier = command([*COMPOSE, "ps", "-aq", node])
    if not identifier or "\n" in identifier:
        raise RuntimeError(f"Expected one container for {node}; run setup first.")
    data = json.loads(command(["docker", "inspect", identifier]))[0]
    labels = data["Config"].get("Labels", {})
    if labels.get("com.docker.compose.project") != "mongo-local-lab" or labels.get("com.docker.compose.service") != node:
        raise RuntimeError("Refusing to manage a container outside this lab.")
    return identifier, data


def network():
    ids = command(["docker", "network", "ls", "-q", "--filter",
                   "label=com.docker.compose.project=mongo-local-lab", "--filter",
                   "label=com.docker.compose.network=mongo-lab"])
    if not ids or "\n" in ids:
        raise RuntimeError("Expected exactly one mongo-lab Compose network.")
    return json.loads(command(["docker", "network", "inspect", ids]))[0]["Name"]


def change(action, node, log=None):
    if action not in ("stop", "start", "isolate", "reconnect") or node not in NODES:
        raise ValueError("Unsupported fault action or node.")
    with LOCK:
        if log:
            log.emit("fault_requested", action=action, node=node)
        try:
            identifier, data = container(node)
            if action in ("stop", "start"):
                args = ["docker", action, *(["--time", "2"] if action == "stop" else []), identifier]
                command(args)
            else:
                net = network()
                attached = net in data["NetworkSettings"]["Networks"]
                if action == "isolate" and attached:
                    command(["docker", "network", "disconnect", net, identifier])
                elif action == "reconnect" and not attached:
                    command(["docker", "network", "connect", "--alias", node, net, identifier])
            _, after = container(node)
            state = dict(running=after["State"]["Running"],
                         networks=list(after["NetworkSettings"]["Networks"]))
            expected = (not state["running"] if action == "stop" else state["running"]
                        if action == "start" else network() not in state["networks"]
                        if action == "isolate" else network() in state["networks"])
            if not expected:
                raise RuntimeError(f"Docker did not reach the requested state: {state}")
            if log:
                log.emit("fault_completed", action=action, node=node, state=state)
            return state
        except Exception as exc:
            if log:
                log.emit("fault_failed", action=action, node=node, error=str(exc))
            raise


def restore(log=None):
    errors = []
    with LOCK:
        for node in NODES:
            for action in ("start", "reconnect"):
                try:
                    change(action, node, log)
                except Exception as exc:
                    errors.append(f"{node} {action}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return "All three nodes started and attached to mongo-lab. Allow time for election and catch-up."


def clear_data(log, *, confirmed=False):
    if not confirmed:
        raise ValueError("Confirm permanent deletion of the lab's MongoDB data first.")
    with LOCK:
        log.emit("data_reset_requested")
        try:
            config = json.loads(command([*COMPOSE, "config", "--format", "json"]))
            volumes = config.get("volumes", {})
            if (config.get("name") != "mongo-local-lab"
                    or set(config.get("services", {})) != set(NODES)
                    or set(volumes) != set(NODES)
                    or any(v.get("external") or v.get("name") != f"mongo-local-lab_{name}"
                           for name, v in volumes.items())):
                raise RuntimeError("Refusing deletion: Compose no longer describes only the three lab databases.")
            log.emit("data_reset_removing_volumes", volumes=[v["name"] for v in volumes.values()])
            command([*COMPOSE, "down", "--volumes", "--timeout", "2"], timeout=90)
            log.emit("data_reset_initializing")
            command([sys.executable, str(ROOT / "scripts" / "setup_lab.py")], timeout=300)
            log.emit("data_reset_completed")
            return "MongoDB data cleared. A fresh three-node replica set is ready; both client sessions have been reset."
        except Exception as exc:
            log.emit("data_reset_failed", error=str(exc))
            raise RuntimeError(f"Data reset failed: {exc}. Deletion may already have occurred. "
                               "Run uv run scripts/setup_lab.py to recover startup.") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stop", "start", "isolate", "reconnect", "restore"])
    parser.add_argument("node", nargs="?", choices=NODES)
    args = parser.parse_args()
    if args.action != "restore" and args.node is None:
        parser.error("This action requires a node.")
    print(restore() if args.action == "restore" else change(args.action, args.node))
