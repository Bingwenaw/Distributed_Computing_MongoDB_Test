"""Initialize rs0 or migrate the original lab one member at a time, preserving volumes."""
import argparse
from copy import deepcopy
import socket
import sys
import time

from pymongo import MongoClient
from pymongo.errors import OperationFailure, PyMongoError

from lab import NODES, ROOT
sys.path.insert(0, str(ROOT))
from faults.control import COMPOSE, command


def direct(node):
    return MongoClient(f"mongodb://127.0.0.1:{NODES[node]}/?directConnection=true",
                       serverSelectionTimeoutMS=1500, connectTimeoutMS=1000, socketTimeoutMS=5000)


def wait_for(check, description, seconds=60):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (PyMongoError, RuntimeError) as exc:
            last = exc
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {description}. {last or ''}")


def config():
    for node in NODES:
        try:
            with direct(node) as client:
                return client.admin.command("replSetGetConfig")["config"]
        except PyMongoError:
            pass
    raise RuntimeError("No reachable replica-set configuration.")


def primary():
    for node in NODES:
        try:
            with direct(node) as client:
                if client.admin.command("hello").get("isWritablePrimary"):
                    return node
        except PyMongoError:
            pass
    return None


def healthy():
    leader = primary()
    if not leader:
        return False
    with direct(leader) as client:
        status = client.admin.command("replSetGetStatus")
        return all(m.get("health") == 1 and m.get("state") in (1, 2)
                   for m in status["members"]) and len(status["members"]) == 3


def validate_config(current):
    members = current.get("members", [])
    names = [m.get("host", "").split(":")[0] for m in members]
    if current.get("_id") != "rs0" or len(members) != 3 or set(names) != set(NODES):
        raise RuntimeError("Existing configuration is not the original three-node rs0 lab; left unchanged.")
    for member in members:
        name, port = member["host"].split(":")
        if int(port) not in (27017, NODES[name]) or member.get("arbiterOnly") or member.get("votes", 1) != 1:
            raise RuntimeError("Unexpected member configuration; migration stopped without forcing reconfiguration.")


def reconfigure(mutator):
    leader = wait_for(primary, "a writable primary")
    with direct(leader) as client:
        current = client.admin.command("replSetGetConfig")["config"]
        updated = deepcopy(current)
        mutator(updated)
        if updated == current:
            return
        updated["version"] += 1
        client.admin.command("replSetReconfig", updated, maxTimeMS=15000)


def setup():
    # Check DNS before changing any containers. Docker members use network aliases.
    for name in NODES:
        try:
            resolved = socket.gethostbyname(name)
        except OSError:
            resolved = None
        if resolved != "127.0.0.1":
            raise RuntimeError("Add this line to /etc/hosts first: 127.0.0.1 mongo1 mongo2 mongo3")
    command(["docker", "info", "--format", "{{.ServerVersion}}"])
    # Starting existing stopped containers first preserves their original port configuration.
    existing = command([*COMPOSE, "ps", "-aq"])
    if existing:
        command([*COMPOSE, "start", *NODES])
    command([*COMPOSE, "up", "-d", "mongo1"], timeout=180)
    def ping():
        with direct("mongo1") as client:
            return client.admin.command("ping")["ok"]
    wait_for(ping, "mongo1")
    with direct("mongo1") as client:
        try:
            current = client.admin.command("replSetGetConfig")["config"]
        except OperationFailure as exc:
            if exc.code != 94:  # NotYetInitialized; all other errors require investigation.
                raise
            current = None
    if current is None:
        command([*COMPOSE, "up", "-d", "mongo2", "mongo3"], timeout=180)
        for node in NODES:
            def ping_member(node=node):
                with direct(node) as client:
                    return client.admin.command("ping")["ok"]
            wait_for(ping_member, node)
        with direct("mongo1") as client:
            client.admin.command("replSetInitiate", {"_id": "rs0", "members": [
                {"_id": i, "host": f"{name}:{port}", "tags": {"node": name}}
                for i, (name, port) in enumerate(NODES.items())]})
    else:
        validate_config(current)
        wait_for(healthy, "healthy original replica set")
        for node in ("mongo2", "mongo3"):
            # The other two members retain quorum while this member changes its port.
            command([*COMPOSE, "up", "-d", node], timeout=180)
            def update_port(cfg, node=node):
                for member in cfg["members"]:
                    if member["host"].split(":")[0] == node:
                        member["host"] = f"{node}:{NODES[node]}"
            reconfigure(update_port)
            wait_for(healthy, f"{node} to rejoin before changing another member")
        def tags(cfg):
            for member in cfg["members"]:
                member.setdefault("tags", {})["node"] = member["host"].split(":")[0]
        reconfigure(tags)
    wait_for(healthy, "three healthy members")
    print("rs0 ready. Start the UI: uv run streamlit run scripts/app.py")


if __name__ == "__main__":
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        setup()
    except Exception as exc:
        raise SystemExit(f"Setup stopped: {exc}\nVolumes were not deleted. Recover connectivity before retrying.")
