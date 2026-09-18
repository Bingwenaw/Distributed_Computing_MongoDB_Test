"""Docker controls restricted to this standalone copy and the local owner."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from cluster_config import LOCAL, load_linked, render_linked

COMPOSE = LOCAL.compose
NODES = tuple(LOCAL.nodes)
LOCK = threading.RLock()


def command(args, timeout=40, *, input=None):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, input=input)
    if result.returncode:
        raise RuntimeError("MongoDB setup command failed; verify team credentials and readiness."
                           if input is not None else (result.stderr or result.stdout).strip())
    return result.stdout.strip()


def container(node, cluster=LOCAL):
    if node not in cluster.local_nodes:
        raise ValueError("Only this laptop's lab nodes can be controlled.")
    identifier = command([*cluster.compose, "ps", "-aq", node])
    if not identifier or "\n" in identifier:
        raise RuntimeError(f"Expected one container for {node}; start this lab first.")
    data = json.loads(command(["docker", "inspect", identifier]))[0]
    check_labels(data, cluster)
    return identifier, data


def check_labels(data, cluster):
    labels = data["Config"].get("Labels", {})
    if (labels.get("com.docker.compose.project") != cluster.project
            or labels.get("com.docker.compose.service") not in cluster.local_nodes
            or Path(labels.get("com.docker.compose.project.working_dir", "")).resolve() != ROOT):
        raise RuntimeError("Refusing to manage a container outside this standalone folder.")


def network(cluster=LOCAL):
    ids = command(["docker", "network", "ls", "-q", "--filter",
                   f"label=com.docker.compose.project={cluster.project}", "--filter",
                   "label=com.docker.compose.network=mongo-lab"])
    if not ids or "\n" in ids:
        raise RuntimeError("Expected exactly one lab network.")
    return json.loads(command(["docker", "network", "inspect", ids]))[0]["Name"]


def change(action, node, log=None, cluster=LOCAL):
    if action not in ("stop", "start", "isolate", "reconnect") or node not in cluster.local_nodes:
        raise ValueError("Unsupported action or node; only this laptop's members can be controlled.")
    with LOCK:
        if log:
            log.emit("fault_requested", action=action, node=node, cluster=cluster.name, laptop=cluster.laptop)
        try:
            identifier, data = container(node, cluster)
            if action in ("stop", "start"):
                command(["docker", action, *(["--time", "2"] if action == "stop" else []), identifier])
            else:
                net = network(cluster)
                attached = net in data["NetworkSettings"]["Networks"]
                if action == "isolate" and attached:
                    command(["docker", "network", "disconnect", net, identifier])
                elif action == "reconnect" and not attached:
                    aliases = ["--alias", node]
                    if cluster.linked:
                        aliases += ["--alias", cluster.nodes[node][0]]
                    command(["docker", "network", "connect", *aliases, net, identifier])
            _, after = container(node, cluster)
            state = dict(running=after["State"]["Running"], networks=list(after["NetworkSettings"]["Networks"]))
            expected = (not state["running"] if action == "stop" else state["running"]
                        if action == "start" else network(cluster) not in state["networks"]
                        if action == "isolate" else network(cluster) in state["networks"])
            if not expected:
                raise RuntimeError(f"Docker did not reach the requested state: {state}")
            if log:
                log.emit("fault_completed", action=action, node=node, state=state, cluster=cluster.name)
            return state
        except Exception as exc:
            if log:
                log.emit("fault_failed", action=action, node=node, error=str(exc))
            raise


def restore(log=None, cluster=LOCAL):
    errors = []
    with LOCK:
        for node in cluster.local_nodes:
            for action in ("start", "reconnect"):
                try:
                    change(action, node, log, cluster)
                except Exception as exc:
                    errors.append(f"{node} {action}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return "This laptop's three nodes are started and reconnected. Allow election/catch-up time."


def validate_compose(cluster):
    config = json.loads(command([*cluster.compose, "config", "--format", "json"]))
    volumes = config.get("volumes", {})
    if (config.get("name") != cluster.project
            or set(config.get("services", {})) != set(cluster.local_nodes)
            or set(volumes) != set(cluster.local_nodes)
            or any(v.get("external") or v.get("name") != f"{cluster.project}_{name}"
                   for name, v in volumes.items())):
        raise RuntimeError("Compose does not describe only this laptop's lab resources.")
    identifiers = command([*cluster.compose, "ps", "-aq"])
    if identifiers:
        for data in json.loads(command(["docker", "inspect", *identifiers.splitlines()])):
            check_labels(data, cluster)
    return config


def stop_cluster(cluster):
    with LOCK:
        if cluster.linked and not cluster.compose_file.exists():
            render_linked(cluster)
        validate_compose(cluster)
        # No --volumes: databases survive switching and shutdown.
        command([*cluster.compose, "down", "--timeout", "20"], timeout=90)


def clear_data(log, *, confirmed=False, cluster=LOCAL):
    if cluster.linked:
        raise ValueError("Shared reset is disabled. Coordinate a fresh team deployment instead.")
    if not confirmed:
        raise ValueError("Confirm permanent deletion of this copy's local databases first.")
    with LOCK:
        log.emit("data_reset_requested", cluster=cluster.name)
        validate_compose(cluster)
        try:
            command([*cluster.compose, "down", "--volumes", "--timeout", "2"], timeout=90)
            command([sys.executable, str(ROOT / "scripts" / "setup_lab.py")], timeout=300)
            log.emit("data_reset_completed")
            return "A fresh local replica set is ready."
        except Exception as exc:
            log.emit("data_reset_failed", error=str(exc))
            raise RuntimeError(f"Data reset failed: {exc}. Deletion may already have occurred.") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stop", "start", "isolate", "reconnect", "restore"])
    parser.add_argument("node", nargs="?")
    parser.add_argument("--linked", action="store_true")
    args = parser.parse_args()
    cluster = load_linked() if args.linked else LOCAL
    if args.action != "restore" and args.node is None:
        parser.error("This action requires a node.")
    print(restore(cluster=cluster) if args.action == "restore" else change(args.action, args.node, cluster=cluster))
