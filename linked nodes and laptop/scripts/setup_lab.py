"""Start this standalone copy's local three-node lab without changing the original."""
import argparse
import socket
import sys
import time
from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError
from cluster_config import LOCAL, ROOT, SETTINGS, load_linked
sys.path.insert(0, str(ROOT))
from faults.control import command, stop_cluster, validate_compose


def direct(node, cluster=LOCAL):
    host, port = cluster.nodes[node]
    return MongoClient(f"mongodb://{host}:{port}/?directConnection=true",
                       **cluster.auth, serverSelectionTimeoutMS=1500,
                       connectTimeoutMS=1000, socketTimeoutMS=5000)


def wait_for(check, description, seconds=90, cancel=None):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise RuntimeError("Connection cancelled.")
        try:
            result = check()
            if result:
                return result
        except PyMongoError as exc:
            last = type(exc).__name__
        if cancel is None:
            time.sleep(1)
        else:
            cancel.wait(1)
    raise RuntimeError(f"Timed out waiting for {description}. {last or ''}")


def validate_config(current, cluster=LOCAL, bootstrap=False):
    expected = cluster.members(bootstrap=bootstrap)
    members = sorted(current.get("members", []), key=lambda m: m.get("_id", -1))
    if current.get("_id") != cluster.name or len(members) != len(expected):
        raise RuntimeError("Existing replica set does not match this lab; left unchanged.")
    for actual, wanted in zip(members, expected):
        # Old authenticated deployments may retain this obsolete tag in their volumes.
        # Hostnames, owners, node identities, votes and priorities must still match.
        if cluster.linked and isinstance(actual.get("tags"), dict):
            actual = dict(actual, tags={k: v for k, v in actual["tags"].items() if k != "team"})
        if (any(actual.get(k, 1 if k in ("votes", "priority") else None) != v for k, v in wanted.items())
                or actual.get("arbiterOnly") or actual.get("hidden") or actual.get("secondaryDelaySecs", 0)):
            raise RuntimeError("Unexpected member identity, votes, tags, or priority; left unchanged.")


def ready(cluster=LOCAL):
    for node in cluster.nodes:
        try:
            with direct(node, cluster) as client:
                if not client.admin.command("hello").get("isWritablePrimary"):
                    continue
                current = client.admin.command("replSetGetConfig")["config"]
                try:
                    validate_config(current, cluster)
                except RuntimeError:
                    if not cluster.linked:
                        raise
                    # A may be upgrading the earlier single-candidate bootstrap configuration.
                    # Only the exact bootstrap configuration is a valid pending state.
                    validate_config(current, cluster, bootstrap=True)
                    return False
                members = client.admin.command("replSetGetStatus")["members"]
                return (len(members) == len(cluster.nodes)
                        and sum(m.get("state") == 1 for m in members) == 1
                        and all(m.get("health") == 1 and m.get("state") in (1, 2) for m in members))
        except PyMongoError:
            continue
    return False


def setup():
    for name in LOCAL.nodes:
        try:
            address = socket.gethostbyname(name)
        except OSError:
            address = None
        if address != "127.0.0.1":
            raise RuntimeError("Add to your OS hosts file: 127.0.0.1 mongo1 mongo2 mongo3")
    validate_compose(LOCAL)
    command([*LOCAL.compose, "up", "-d"], timeout=180)
    for node in LOCAL.nodes:
        def ping(node=node):
            with direct(node) as client:
                return client.admin.command("ping")["ok"]
        wait_for(ping, node)
    with direct("mongo1") as client:
        try:
            current = client.admin.command("replSetGetConfig")["config"]
        except OperationFailure as exc:
            if exc.code != 94:
                raise
            client.admin.command("replSetInitiate", {"_id": LOCAL.name, "members": LOCAL.members()})
        else:
            validate_config(current)
    wait_for(ready, "three healthy local members")
    return "Local lab ready at http://127.0.0.1:8502"


def startup():
    # Check before any Docker changes: another copy may already own the dashboard.
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", 8502)) == 0:
            raise RuntimeError("Port 8502 is already in use. Open http://127.0.0.1:8502, "
                               "or stop the existing dashboard before starting another copy. "
                               "No Docker containers were changed.")
    if SETTINGS.exists() or (ROOT / "compose.linked.json").exists():
        stop_cluster(load_linked())
    return setup()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--startup", action="store_true", help="Stop this copy's leftover shared nodes first.")
    args = parser.parse_args()
    try:
        print(startup() if args.startup else setup())
    except Exception as exc:
        raise SystemExit(f"Setup stopped: {exc}\nVolumes were not deleted.")
