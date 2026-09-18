"""Fixed local/linked lab inventories. No imports from the original project."""
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import json
import os

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "secrets" / "laptop.json"


def save_private(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, indent=2)
        stream.write("\n")


def save_settings(laptop, addresses):
    if laptop not in ("A", "B", "C") or set(addresses) != {"A", "B", "C"}:
        raise ValueError("Select laptop A, B, or C and enter all three addresses.")
    for address in addresses.values():
        parsed = ipaddress.IPv4Address(address)
        if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
            raise ValueError("Use the laptop's hotspot IPv4 address, not localhost or a link-local address.")
    if len(set(addresses.values())) != 3:
        raise ValueError("Each laptop must have a different hotspot IP address.")
    if SETTINGS.exists() and json.loads(SETTINGS.read_text())["laptop"] != laptop:
        raise ValueError("Laptop identity is fixed for this copy. Use a fresh copy to change identity.")
    save_private(SETTINGS, dict(laptop=laptop, addresses=addresses))


@dataclass(frozen=True)
class Cluster:
    linked: bool = False
    laptop: str = "local"
    addresses: dict | None = None

    @property
    def name(self):
        return "rs-linked" if self.linked else "rs-local"

    @property
    def project(self):
        return "mongo-connect-shared" if self.linked else "mongo-connect-local"

    @property
    def nodes(self):
        return {f"mongo{i}": (f"mongo{i}.lab.test" if self.linked else f"mongo{i}",
                               (29016 if self.linked else 28016) + i)
                for i in range(1, 10 if self.linked else 4)}

    def owner(self, node):
        if node not in self.nodes:
            raise ValueError("Unknown lab node.")
        return "ABC"[(int(node[5:]) - 1) // 3] if self.linked else "local"

    @property
    def local_nodes(self):
        return [node for node in self.nodes if self.owner(node) == self.laptop]

    @property
    def routes(self):
        return ["primary", "secondary", "secondaryPreferred", *self.nodes]

    @property
    def uri(self):
        return "mongodb://" + ",".join(f"{host}:{port}" for host, port in self.nodes.values()) + f"/?replicaSet={self.name}"

    @property
    def auth(self):
        return {}  # Trusted-hotspot lab: no client or member authentication.

    @property
    def compose_file(self):
        return ROOT / ("compose.linked.json" if self.linked else "compose.local.yml")

    @property
    def compose(self):
        return ["docker", "compose", "-p", self.project, "-f", str(self.compose_file)]

    def members(self, bootstrap=False):
        members = []
        for index, (node, (host, port)) in enumerate(self.nodes.items()):
            votes = int(not self.linked or node not in ("mongo6", "mongo9"))
            tags = {"node": node}
            if self.linked:
                tags.update(laptop=self.owner(node))
            members.append(dict(_id=index, host=f"{host}:{port}", votes=votes,
                                priority=votes if not bootstrap or index == 0 else 0, tags=tags))
        return members


LOCAL = Cluster()


def load_linked():
    if not SETTINGS.exists():
        raise ValueError("Save this laptop identity and all three IP addresses first.")
    settings = json.loads(SETTINGS.read_text())
    # Reuse boundary validation without changing stored settings.
    if settings.get("laptop") not in ("A", "B", "C") or set(settings.get("addresses", {})) != {"A", "B", "C"}:
        raise ValueError("Invalid laptop settings.")
    addresses = settings["addresses"]
    if len(set(addresses.values())) != 3:
        raise ValueError("Laptop IP addresses must be distinct.")
    for address in addresses.values():
        parsed = ipaddress.IPv4Address(address)
        if parsed.is_loopback or parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
            raise ValueError("Invalid hotspot IP address.")
    return Cluster(True, settings["laptop"], addresses)


def host_entries(cluster):
    rows = ["127.0.0.1 mongo1 mongo2 mongo3"]
    for laptop, address in cluster.addresses.items():
        rows.append(address + " " + " ".join(host for node, (host, _) in cluster.nodes.items()
                                              if cluster.owner(node) == laptop))
    return "\n".join(rows)


def render_linked(cluster):
    """Compose accepts JSON; render only this owner's three fixed services."""
    services = {}
    for node in cluster.local_nodes:
        host, port = cluster.nodes[node]
        services[node] = {
            "image": "mongo:7.0.40",
            "command": ["mongod", "--port", str(port), "--replSet", cluster.name,
                        "--bind_ip_all", "--oplogSize", "256"],
            "ports": [f"{cluster.addresses[cluster.laptop]}:{port}:{port}"],
            "volumes": [f"{node}:/data/db"],
            "networks": {"mongo-lab": {"aliases": [host]}},
            "extra_hosts": {remote_host: cluster.addresses[cluster.owner(remote)]
                            for remote, (remote_host, _) in cluster.nodes.items()
                            if cluster.owner(remote) != cluster.laptop},
            "healthcheck": {"test": ["CMD", "mongosh", "--port", str(port), "--quiet", "--eval",
                                      "quit(db.adminCommand('ping').ok ? 0 : 1)"],
                            "interval": "5s", "timeout": "3s", "retries": 20},
        }
    config = dict(name=cluster.project, services=services, networks={"mongo-lab": {}},
                  volumes={node: {} for node in cluster.local_nodes})
    cluster.compose_file.write_text(json.dumps(config, indent=2) + "\n")
    return config
