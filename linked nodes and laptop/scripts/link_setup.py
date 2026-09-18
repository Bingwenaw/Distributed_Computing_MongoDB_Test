"""Linked-node startup and one-time coordinator bootstrap."""
import argparse
from copy import deepcopy
import json
import secrets
import socket
import sys
from pymongo import MongoClient
from pymongo.errors import OperationFailure
from cluster_config import ROOT, load_linked, render_linked, save_private
from setup_lab import direct, wait_for, ready, validate_config
sys.path.insert(0, str(ROOT))
from faults.control import command, container, validate_compose, stop_cluster


USER_SETUP = """
const a=db.getSiblingDB('admin');
const adminPassword=ADMIN_PASSWORD;
function authenticate() {
  const result=a.auth('setup', adminPassword);
  return result === 1 || result?.ok === 1;
}
try {
  let authenticated=false;
  try { authenticated=authenticate(); } catch (e) {}
  if (!authenticated) {
    a.createUser({user:'setup',pwd:adminPassword,roles:['root']});
    if (!authenticate()) quit(2);
  }
  if (!a.getUser('lab')) a.createUser({user:'lab',pwd:APP_PASSWORD,
      roles:[{role:'readWrite',db:'lab'},{role:'clusterMonitor',db:'admin'}]});
  quit(0);
} catch (e) { quit(2); }
"""

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
                    str(cluster.nodes[node][1])], timeout=35, input=script + "\n")


def peers_reachable(cluster):
    for node in cluster.nodes:
        with direct(node, cluster, authenticated=False) as client:
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
    """Only A can initialize. Start with mongo1 as the sole electable member."""
    with direct("mongo1", cluster, authenticated=False) as client:
        initialized = bool(client.admin.command("hello").get("setName"))
    if not initialized:
        config = dict(_id=cluster.name, members=cluster.members(bootstrap=True))
        script = "const r=db.getSiblingDB('admin').runCommand({replSetInitiate:" + json.dumps(config) + "}); quit(r.ok ? 0 : 2);"
        shell(cluster, "mongo1", script)

    # An established, matching cluster needs no bootstrap or administrator password.
    try:
        with direct("mongo1", cluster) as client:
            existing = client.admin.command("replSetGetConfig")["config"]
        validate_config(existing, cluster)
        return
    except OperationFailure as exc:
        if exc.code not in (13, 18):
            raise
    except RuntimeError:
        # A previous first startup may have stopped after creating users.
        validate_config(existing, cluster, bootstrap=True)

    def leader_ready():
        with direct("mongo1", cluster, authenticated=False) as client:
            return client.admin.command("hello").get("isWritablePrimary")
    wait_for(leader_ready, "coordinator primary (check matching team keys on all laptops)", cancel=cancel)
    admin_path = ROOT / "secrets" / "admin.json"
    if not admin_path.exists():
        save_private(admin_path, {"password": secrets.token_urlsafe(32)})
    password = json.loads(admin_path.read_text())["password"]
    # Secrets go over stdin, not shell arguments or the event log.
    script = USER_SETUP.replace("ADMIN_PASSWORD", json.dumps(password)).replace("APP_PASSWORD", json.dumps(cluster.team["password"]))
    shell(cluster, "mongo1", script)
    host, port = cluster.nodes["mongo1"]
    with MongoClient(f"mongodb://{host}:{port}/?directConnection=true", username="setup", password=password,
                     authSource="admin", serverSelectionTimeoutMS=3000, socketTimeoutMS=15000) as client:
        current = client.admin.command("replSetGetConfig")["config"]
        try:
            validate_config(current, cluster)
        except RuntimeError:
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
    progress("Waiting for authentication, one primary and eight secondaries…")
    wait_for(lambda: ready(cluster), "the shared set; check that A is connected and team files match",
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
