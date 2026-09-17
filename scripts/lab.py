"""MongoDB operations and evidence, shared by the UI and experiment runner."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import threading
import time
from uuid import uuid4

from bson import json_util
from pymongo import MongoClient, ReadPreference, monitoring, timeout
from pymongo.errors import ConnectionFailure, ExecutionTimeout, WTimeoutError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import Nearest
from pymongo.write_concern import WriteConcern

ROOT = Path(__file__).resolve().parents[1]
NODES = {"mongo1": 27017, "mongo2": 27018, "mongo3": 27019}
URI = "mongodb://" + ",".join(f"{n}:{p}" for n, p in NODES.items()) + "/?replicaSet=rs0"
ROUTES = ["primary", "secondary", "secondaryPreferred", *NODES]
PROPERTIES = ["Read-your-writes", "Monotonic reads", "Monotonic writes", "Writes-follow-reads"]
SOURCE = "https://www.mongodb.com/docs/manual/core/causal-consistency-read-write-concerns/"


def plain(value):
    return json.loads(json_util.dumps(value))


def object_json(text):
    if len(text.encode()) > 1_000_000:
        raise ValueError("Keep each JSON input below 1 MB.")
    value = json_util.loads(text)
    if not isinstance(value, dict):
        raise ValueError("Enter a JSON object, such as {}.")
    return value


@dataclass(frozen=True)
class Settings:
    read: str = "majority"
    write: str | int = "majority"
    route: str = "secondaryPreferred"
    causal: bool = True

    def __post_init__(self):
        if self.read not in ("local", "majority") or self.write not in (1, "majority"):
            raise ValueError("Unsupported read/write concern.")
        if self.route not in ROUTES or type(self.causal) is not bool:
            raise ValueError("Unsupported routing/session setting.")

    def preference(self):
        return {
            "primary": ReadPreference.PRIMARY,
            "secondary": ReadPreference.SECONDARY,
            "secondaryPreferred": ReadPreference.SECONDARY_PREFERRED,
        }.get(self.route) or Nearest(tag_sets=[{"node": self.route}])


def prediction(settings, property_name):
    # Durability-inclusive session guarantees from MongoDB's documented table.
    guarantees = {
        ("majority", "majority"): set(PROPERTIES),
        ("majority", 1): {"Monotonic reads", "Writes-follow-reads"},
        ("local", "majority"): {"Monotonic writes"},
        ("local", 1): set(),
    }
    if settings.causal and property_name in guarantees[(settings.read, settings.write)]:
        return "Guaranteed for successful operations (including durability)"
    return "No general guarantee across the tested faults; violations are possible"


class EventLog:
    def __init__(self, base=ROOT / "results"):
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
        self.directory = Path(base) / self.run_id
        self.directory.mkdir(parents=True)
        self.path = self.directory / "events.jsonl"
        self.path.touch()
        self.lock = threading.Lock()
        self.recent = deque(maxlen=1500)
        self.sequence = 0

    def emit(self, event, **fields):
        with self.lock:
            self.sequence += 1
            record = plain(dict(run_id=self.run_id, event_index=self.sequence,
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                event=event, **fields))
            # Open/close per record: flushed before the UI sees it, no retained file handle.
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                stream.flush()
            self.recent.append(record)
            return record

    def snapshot(self):
        with self.lock:
            return list(self.recent)


class CommandLog(monitoring.CommandListener):
    def __init__(self, log, client_id):
        self.log, self.client_id = log, client_id
        self.context = threading.local()

    def record(self, event, stage, **extra):
        context = getattr(self.context, "operation", {})
        self.log.emit(stage, client=self.client_id, **context,
                      category="operation" if context else "diagnostic",
                      command=event.command_name, request_id=event.request_id,
                      server=event.connection_id, **extra)

    def started(self, event):
        self.record(event, "command_started", wire_command=event.command)

    def succeeded(self, event):
        self.record(event, "command_succeeded", duration_ms=event.duration_micros / 1000,
                    reply=event.reply)

    def failed(self, event):
        self.record(event, "command_failed", duration_ms=event.duration_micros / 1000,
                    error=event.failure)


class Actor:
    def __init__(self, client_id, log):
        self.client_id, self.log = client_id, log
        self.monitor = CommandLog(log, client_id)
        self.client = MongoClient(URI, connect=False, serverSelectionTimeoutMS=4000,
                                 connectTimeoutMS=2000, socketTimeoutMS=8000,
                                 retryWrites=False, retryReads=False,
                                 event_listeners=[self.monitor], appname=f"lab-{client_id}")
        self.session = None
        self.causal = None
        self.sequence = 0
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=client_id)

    def reset(self, causal=True):
        with self.lock:
            if self.session is not None:
                self.session.end_session()
            self.session = self.client.start_session(causal_consistency=causal)
            self.causal = causal
            return self.log.emit("session_reset", client=self.client_id,
                                 session=self.session.session_id, causal=causal)

    def execute(self, operation, settings, database="lab", collection="manual",
                query=None, document=None, upsert=False, limit=100, trial=None):
        with self.lock:
            self.sequence += 1
            op_id = uuid4().hex
            context = dict(operation_id=op_id, client_sequence=self.sequence, trial=trial)
            self.monitor.context.operation = context
            start = time.monotonic()
            self.log.emit("operation_started", client=self.client_id, **context,
                          operation=operation, settings=asdict(settings), database=database,
                          collection=collection, query=query, document=document,
                          upsert=upsert, limit=limit, timeout_ms=8000, retry_reads=False,
                          retry_writes=False, journal=True)
            result, error, status = None, None, "ok"
            try:
                if operation not in ("find", "insert_one", "update_one", "replace_one"):
                    raise ValueError("Unsupported operation.")
                if not database or database in ("admin", "config", "local"):
                    raise ValueError("Choose a non-system database, such as lab.")
                if not collection or collection.startswith("system."):
                    raise ValueError("Choose a non-system collection.")
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise ValueError("Read limit must be 1–100.")
                if query is not None and not isinstance(query, dict):
                    raise ValueError("Filter must be a JSON object.")
                if operation != "find" and not isinstance(document, dict):
                    raise ValueError("Document must be a JSON object.")
                if operation == "update_one" and (not document or not all(k.startswith("$") for k in document)):
                    raise ValueError('An update needs operators, for example {"$set": {"version": 2}}.')
                if operation in ("insert_one", "replace_one") and any(k.startswith("$") for k in document):
                    raise ValueError("Insert/replace expects a document, not update operators.")
                if self.session is None or self.causal != settings.causal:
                    self.reset(settings.causal)
                target = self.client[database][collection].with_options(
                    read_concern=ReadConcern(settings.read),
                    write_concern=WriteConcern(w=settings.write, j=True, wtimeout=5000),
                    read_preference=settings.preference())
                with timeout(8):
                    if operation == "find":
                        with target.find(query or {}, session=self.session,
                                         comment=op_id).limit(limit).max_time_ms(5000) as cursor:
                            result = list(cursor)
                    elif operation == "insert_one":
                        written = target.insert_one(dict(document), session=self.session, comment=op_id)
                        result = {"acknowledged": written.acknowledged, "inserted_id": written.inserted_id}
                    else:
                        written = getattr(target, operation)(query or {}, document,
                            upsert=upsert, session=self.session, comment=op_id)
                        result = {"acknowledged": written.acknowledged,
                                  "matched_count": written.matched_count,
                                  "modified_count": written.modified_count,
                                  "upserted_id": written.upserted_id}
            except Exception as exc:
                uncertain = operation != "find" and (
                    isinstance(exc, (ConnectionFailure, WTimeoutError, ExecutionTimeout))
                    or getattr(exc, "timeout", False))
                status = "write_outcome_unknown" if uncertain else "error"
                error = {"type": type(exc).__name__, "message": str(exc),
                         "code": getattr(exc, "code", None), "details": getattr(exc, "details", None)}
            finally:
                self.monitor.context.operation = {}
            return self.log.emit("operation_finished", client=self.client_id, **context,
                operation=operation, settings=asdict(settings), status=status,
                database=database, collection=collection, query=query or {},
                result=result, error=error, duration_ms=round((time.monotonic() - start) * 1000, 2),
                session=self.session.session_id if self.session else None,
                operation_time=self.session.operation_time if self.session else None,
                cluster_time=self.session.cluster_time if self.session else None)

    def close(self):
        self.executor.shutdown(wait=True)
        if self.session:
            self.session.end_session()
        self.client.close()


def topology():
    """Direct local probes work even when replica DNS or elections are broken."""
    def probe(item):
        name, port = item
        try:
            with MongoClient(f"mongodb://127.0.0.1:{port}/?directConnection=true",
                             serverSelectionTimeoutMS=700, connectTimeoutMS=700,
                             socketTimeoutMS=700) as client:
                hello = client.admin.command("hello")
                return dict(node=name, reachable=True,
                            role="PRIMARY" if hello.get("isWritablePrimary") else
                                 "SECONDARY" if hello.get("secondary") else "OTHER",
                            primary=hello.get("primary"), set_name=hello.get("setName"))
        except Exception as exc:
            return dict(node=name, reachable=False, role="UNREACHABLE", error=str(exc))
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(probe, NODES.items()))
