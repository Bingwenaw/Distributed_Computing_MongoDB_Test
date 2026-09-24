"""Shared settings for the DSA5208 MongoDB consistency experiments.

Run from the project with uv; both test clients run on one laptop.
Run `python config.py --config C1` to display settings without connecting.
Each test must pass session=session to every related MongoDB operation.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.cluster_config import Cluster


CONFIGS = {
    "C1": {"read_concern": "majority", "write_concern": "majority"},
    "C2": {"read_concern": "majority", "write_concern": 1},
    "C3": {"read_concern": "local", "write_concern": 1},
    "C4": {"read_concern": "local", "write_concern": "majority"},
}

DEFAULT_CONFIG = "C1"
# Reuse the dashboard's nine-node inventory and the OS hosts-file mappings.
CLUSTER = Cluster(linked=True)
MONGO_URI = CLUSTER.uri
DATABASE_NAME = "consistency_lab"
CAUSAL_CONSISTENCY = False
READ_PREFERENCE = "secondary"

# Fixed across profiles. Disabled retries make failure attempts easier to log.
RETRY_READS = False
RETRY_WRITES = False
SERVER_SELECTION_TIMEOUT_MS = 15000
OPERATION_TIMEOUT_MS = 20000
WRITE_CONCERN_TIMEOUT_MS = 10000


def get_config(name=DEFAULT_CONFIG):
    """Return a copy so callers cannot accidentally change a stored profile."""
    name = name.upper()
    if name not in CONFIGS:
        raise ValueError(f"Unknown configuration {name!r}; choose {', '.join(CONFIGS)}")
    return dict(CONFIGS[name])


def create_client(event_listeners=None):
    """Create one MongoClient per client process; caller must close it."""
    from pymongo import MongoClient

    return MongoClient(
        MONGO_URI,
        event_listeners=event_listeners or [],
        retryReads=RETRY_READS,
        retryWrites=RETRY_WRITES,
        serverSelectionTimeoutMS=SERVER_SELECTION_TIMEOUT_MS,
        timeoutMS=OPERATION_TIMEOUT_MS,
    )


def get_collection(client, collection_name, config_name=DEFAULT_CONFIG):
    """Apply the chosen concerns to this collection handle, not server defaults."""
    from pymongo import ReadPreference
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern

    profile = get_config(config_name)
    preferences = {
        "primary": ReadPreference.PRIMARY,
        "secondary": ReadPreference.SECONDARY,
    }
    return client[DATABASE_NAME].get_collection(
        collection_name,
        read_concern=ReadConcern(profile["read_concern"]),
        write_concern=WriteConcern(
            w=profile["write_concern"], wtimeout=WRITE_CONCERN_TIMEOUT_MS
        ),
        read_preference=preferences[READ_PREFERENCE],
    )


def start_session(client, causal=CAUSAL_CONSISTENCY):
    """Use as a context manager; A and B each keep their own session."""
    return client.start_session(causal_consistency=causal)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Display experiment settings (no DB operations).")
    parser.add_argument("--config", type=str.upper, choices=CONFIGS, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    print(json.dumps({
        "configuration": args.config,
        **get_config(args.config),
        "mongo_uri": MONGO_URI,
        "database": DATABASE_NAME,
        "causal_consistency": CAUSAL_CONSISTENCY,
        "read_preference": READ_PREFERENCE,
        "retry_reads": RETRY_READS,
        "retry_writes": RETRY_WRITES,
        "server_selection_timeout_ms": SERVER_SELECTION_TIMEOUT_MS,
        "operation_timeout_ms": OPERATION_TIMEOUT_MS,
        "write_concern_timeout_ms": WRITE_CONCERN_TIMEOUT_MS,
    }, indent=2))
