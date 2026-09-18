"""Linked-node startup and one-time coordinator bootstrap."""
import argparse
from copy import deepcopy
import json
import socket
import sys
from pymongo.errors import OperationFailure
from cluster_config import ROOT, load_linked, render_linked
from setup_lab import direct, wait_for, ready, validate_config
sys.path.insert(0, str(ROOT))
from faults.control import command, container, validate_compose, stop_cluster


def check_hosts(cluster):
    for node, (host, _) in cluster.nodes.items():
        expected = cluster.addresses[cluster.owner(node)]
        try:
            actual = socket.gethostbyname(host)
        except OSError:
            actual = None
        if actual != expected:
            raise RuntimeError(f"Hosts entry needed: {expected} {host}. See Connection setup.")


def shell(cluster, node, script):
    identifier, _ = container(node, cluster)
    return command(["docker", "exec", "-i", identifier, "mongosh", "--quiet", "--port",
                    str(cluster.nodes[node][1]), "--file", "/dev/stdin"], timeout=35, input=script + "\n")


def peers_reachable(cluster):
    for node in cluster.nodes:
        with direct(node, cluster) as client:
            hello = client.admin.command("hello")
            if hello.get("setName") not in (None, cluster.name):
                raise RuntimeError(f"{node} belongs to a different replica set.")
    return True


def container_connections(cluster):
    endpoints = [f"mongodb://{host}:{port}/?directConnection=true&serverSelectionTimeoutMS=1500"
                 for host, port in cluster.nodes.values()]
    script = "try { for (const uri of " + json.dumps(endpoints) + ") { " \
             "const r = new Mongo(uri).getDB('admin').runCommand({hello:1}); " \
             "if (!r.ok) quit(2); } quit(0); } catch (e) { quit(2); }"
    for node in cluster.local_nodes:
        try:
            shell(cluster, node, script)
        except RuntimeError as exc:
            raise RuntimeError(f"{node} cannot reach all peers from Docker. Check hotspot/firewalls and IP mappings.") from exc


def bootstrap(cluster, cancel):
    """A initializes a new set; existing data and matching configurations are reused."""
    if cancel.is_set():
        raise RuntimeError("Connection cancelled.")
    with direct("mongo1", cluster) as client:
        try:
            current = client.admin.command("replSetGetConfig")["config"]
        except OperationFailure as exc:
            if exc.code != 94:
                raise
            # Use PyMongo directly so initialization errors cannot be swallowed by a shell.
            client.admin.command("replSetInitiate", {"_id": cluster.name, "members": cluster.members()})
            return
        try:
            validate_config(current, cluster)
            return
        except RuntimeError:
            validate_config(current, cluster, bootstrap=True)

    # Preserve a partially initialized older deployment, enabling its other candidates.
    def leader_ready():
        with direct("mongo1", cluster) as client:
            return client.admin.command("hello").get("isWritablePrimary")
    wait_for(leader_ready, "coordinator primary; update and restart all three laptops", cancel=cancel)
    with direct("mongo1", cluster) as client:
        current = client.admin.command("replSetGetConfig")["config"]
        validate_config(current, cluster, bootstrap=True)
        updated = deepcopy(current)
        updated["members"] = cluster.members()
        updated["version"] += 1
        client.admin.command("replSetReconfig", updated, maxTimeMS=12000)


def start_shared(cluster, cancel, progress):
    progress("Checking hostname mappings and this laptop's configuration…")
    check_hosts(cluster)
    render_linked(cluster)
    validate_compose(cluster)
    if cancel.is_set():
        raise RuntimeError("Connection cancelled.")
    progress("Starting this laptop's three shared nodes…")
    command([*cluster.compose, "up", "-d"], timeout=180)
    progress("Waiting for all three laptops. Each owner must click Connect (up to 120 seconds)…")
    wait_for(lambda: peers_reachable(cluster), "all nine endpoints; ask both friends to Connect", seconds=120, cancel=cancel)
    container_connections(cluster)
    if cluster.laptop == "A":
        progress("Checking/initializing the shared replica set on coordinator A…")
        bootstrap(cluster, cancel)
    progress("Waiting for one primary and eight secondaries…")
    wait_for(lambda: ready(cluster), "the shared set; check that all three laptops use the updated app",
             seconds=120, cancel=cancel)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "stop"])
    args = parser.parse_args()
    cluster = load_linked()
    if args.action == "stop":
        stop_cluster(cluster)
    else:
        check_hosts(cluster)
        print("Ready" if ready(cluster) else "Cluster is not fully ready")
