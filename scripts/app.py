"""Run with: uv run streamlit run scripts/app.py"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from itertools import product
import sys
import threading
import time

import streamlit as st

from lab import Actor, EventLog, NODES, PROPERTIES, ROOT, ROUTES, SOURCE, Settings, object_json, prediction, topology
from experiments import SCENARIOS, run_suite
sys.path.insert(0, str(ROOT))
import faults.control as control
from faults.control import change, restore


class Dashboard:
    def __init__(self):
        self.log = EventLog()
        self.actors = [Actor("A", self.log), Actor("B", self.log)]
        self.jobs = {}
        self.lock = threading.Lock()
        self.control = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-control")
        self.diagnostics = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-status")
        self.cancel = threading.Event()
        self.status = []
        self.status_job = None
        self.status_time = 0
        self.log.emit("dashboard_started", defaults=asdict(Settings()))

    def busy(self, key):
        job = self.jobs.get(key)
        return job is not None and not job.done()

    def submit(self, key, function, *args, **kwargs):
        with self.lock:
            if self.busy("suite") or self.busy("reset") or self.busy(key):
                raise ValueError("This client or the experiment runner is busy.")
            if key in ("suite", "reset") and any(not job.done() for job in self.jobs.values()):
                raise ValueError("Wait for active commands, experiments, and fault controls first.")
            if key == "suite":
                self.cancel.clear()
            executor = self.actors[0 if key == "A" else 1].executor if key in ("A", "B") else self.control
            self.jobs[key] = executor.submit(function, *args, **kwargs)

    def clear_database(self):
        try:
            for actor in self.actors:
                actor.close()
            return control.clear_data(self.log, confirmed=True)
        finally:
            # New MongoClients discard clocks and session history from the deleted replica set.
            with self.lock:
                self.actors = [Actor("A", self.log), Actor("B", self.log)]
                self.jobs = {key: job for key, job in self.jobs.items() if key == "reset"}
                self.status = []

    def refresh_status(self):
        if self.status_job is not None and self.status_job.done():
            self.status = self.status_job.result()
            self.log.emit("topology", category="diagnostic", nodes=self.status)
            self.status_job = None
        if self.status_job is None and time.monotonic() - self.status_time >= 3:
            self.status_job = self.diagnostics.submit(topology)
            self.status_time = time.monotonic()


@st.cache_resource
def dashboard():
    """Share client connections and the exclusive data-reset lock across browser tabs."""
    # ponytail: one shared local lab across browser tabs; separate processes if multiple groups need isolation.
    return Dashboard()


st.set_page_config(page_title="MongoDB Consistency Lab", page_icon="🧪", layout="wide")
st.title("MongoDB Consistency Lab")
st.caption("Three replicas · Two independent clients · Every operation recorded")
lab = dashboard()


def submit(key, function, *args, **kwargs):
    try:
        lab.submit(key, function, *args, **kwargs)
        if key in ("A", "B"):
            kind = ("read" if args[0] == "find" else "write") if function.__name__ == "execute" else "session"
            lab.jobs[f"{key}:{kind}"] = lab.jobs[key]
    except Exception as exc:
        st.error(str(exc))


def job_result(key):
    job = lab.jobs.get(key)
    if job is None:
        return
    if not job.done():
        st.info("Running… live logs continue below.")
    else:
        try:
            result = job.result()
            if isinstance(result, dict) and result.get("event") == "operation_finished":
                if result["operation"] == "find":
                    st.caption(f"Last submitted read · {result.get('database', 'lab')}.{result.get('collection', 'manual')} "
                               f"· {result['timestamp']}")
                    st.code(json.dumps(result.get("query", {}), ensure_ascii=False), language="json")
                if result["status"] == "ok":
                    st.success(f"Completed in {result['duration_ms']:.0f} ms")
                    if result["operation"] == "find" and not result["result"]:
                        st.info("No documents matched this read filter.")
                    st.json(result["result"], expanded=True)
                else:
                    st.error(f"{result['status']}: {result['error']['message']}")
            elif key != "suite":
                st.write(result)
        except Exception as exc:
            st.error(str(exc))


def confirm_clear_data():
    st.warning("This permanently deletes all databases in the three lab MongoDB volumes, "
               "then creates a fresh replica set. Saved log files remain available.")
    if st.button("Confirm delete and recreate", type="primary", disabled=any(lab.busy(k) for k in lab.jobs)):
        try:
            lab.submit("reset", lab.clear_database)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["confirm_clear_data"] = False
            st.rerun()
    if st.button("Cancel deletion"):
        st.session_state["confirm_clear_data"] = False
        st.rerun()


def client_panel(actor):
    name = actor.client_id
    with st.container(border=True):
        st.subheader(f"Client {name}")
        st.caption("Independent connection and session • writes always go to the primary")
        disabled = lab.busy(name) or lab.busy("suite") or lab.busy("reset")
        with st.form(f"client_{name}"):
            db_col, coll_col = st.columns(2)
            database = db_col.text_input("Database", "lab", key=f"db{name}")
            collection = coll_col.text_input("Collection", "manual", key=f"coll{name}")
            rc_col, wc_col = st.columns(2)
            read = rc_col.selectbox("Read concern", ["majority", "local"], key=f"rc{name}")
            write = wc_col.selectbox("Write concern", ["majority", 1], key=f"wc{name}")
            route = st.selectbox("Read from", ROUTES, index=2, key=f"route{name}")
            causal = st.checkbox("Causal consistency", value=True, key=f"causal{name}")
            st.markdown("#### Read")
            query = st.text_area("Read filter (JSON)", '{"_id": "demo"}', key=f"filter{name}", height=80,
                                 help='Change _id here to read another document. Use {} to read all documents, up to the limit. This filter also selects the document for update/replace.')
            st.caption('To read demo1, enter {"_id": "demo1"} in the Read filter above.')
            limit = st.number_input("Maximum documents to read", 1, 100, 100, key=f"limit{name}")
            do_read = st.form_submit_button("Read", disabled=disabled, width="stretch")
            read_result_area = st.container()
            st.divider()
            st.markdown("#### Write")
            operation = st.selectbox("Write operation", ["insert_one", "update_one", "replace_one"], key=f"op{name}")
            document = st.text_area("Write document / update (JSON, used only by Write)", '{"_id": "demo", "version": 1}',
                                    key=f"doc{name}", height=110)
            st.caption('Update example: {"$set": {"version": 2}}. Update/replace matches the Read filter above.')
            upsert = st.checkbox("Upsert: create if no document matches (update / replace)", key=f"upsert{name}")
            write_col, reset_col = st.columns(2)
            do_write = write_col.form_submit_button("Write", disabled=disabled, type="primary", width="stretch")
            do_reset = reset_col.form_submit_button("Reset session", disabled=disabled, width="stretch")
        cfg = Settings(read, write, route, causal)
        if do_reset:
            submit(name, actor.reset, causal)
        elif do_read or do_write:
            try:
                parsed_query = object_json(query)
                parsed_doc = object_json(document) if do_write else None
                submit(name, actor.execute, "find" if do_read else operation, cfg,
                       database, collection, parsed_query, parsed_doc, upsert, int(limit))
            except Exception as exc:
                lab.log.emit("validation_error", client=name, error=str(exc))
                st.error(str(exc))
        with read_result_area:
            st.markdown("**Read result**")
            if f"{name}:read" in lab.jobs:
                job_result(f"{name}:read")
            else:
                st.caption("Run a read to see its result here.")
        st.markdown("**Write result**")
        if f"{name}:write" in lab.jobs:
            job_result(f"{name}:write")
        else:
            st.caption("Run a write to see its result here.")
        job_result(f"{name}:session")
        with st.expander(f"Client {name} recent operations"):
            recent = [e for e in lab.log.snapshot() if e.get("client") == name and e["event"] == "operation_finished"]
            st.dataframe([{k: e.get(k) for k in ("timestamp", "client_sequence", "operation", "status", "duration_ms")}
                          for e in recent[-15:]], hide_index=True, width="stretch")


@st.fragment(run_every="1s")
def workspace():
    if st.button("Clear MongoDB data", disabled=any(lab.busy(k) for k in lab.jobs)):
        st.session_state["confirm_clear_data"] = True
    if st.session_state.get("confirm_clear_data"):
        confirm_clear_data()
    job_result("reset")
    lab.refresh_status()
    with st.expander("Cluster & fault controls", expanded=True):
        cols = st.columns(3)
        for col, node in zip(cols, NODES):
            state = next((s for s in lab.status if s["node"] == node), {})
            col.metric(node, state.get("role", "CHECKING"))
        st.caption("Network isolation disconnects a node from mongo-lab and can also remove client access. "
                   "Stop uses a short graceful shutdown; these faults do not guarantee a rollback.")
        node_col, action_col, button_col, restore_col = st.columns([2, 2, 1, 1])
        node = node_col.selectbox("Node", list(NODES), key="fault_node")
        action = action_col.selectbox("Action", ["stop", "start", "isolate", "reconnect"], key="fault_action")
        disabled = lab.busy("control") or lab.busy("suite") or lab.busy("reset")
        if button_col.button("Apply fault", disabled=disabled):
            submit("control", change, action, node, lab.log)
        if restore_col.button("Restore lab", disabled=disabled):
            submit("control", restore, lab.log)
        job_result("control")
        if lab.status and not any(s["reachable"] for s in lab.status):
            st.info("No MongoDB node is reachable. Start Docker Desktop, then run `uv run scripts/setup_lab.py`. See README for hostname setup.")

    left, right = st.columns(2)
    with left:
        client_panel(lab.actors[0])
    with right:
        client_panel(lab.actors[1])

    with st.expander("Repeatable experiments & predictions", expanded=False):
        st.write("Each trial starts new client sessions and uses a unique document in `lab.experiments`. "
                 "Reads are scheduled across reachable replicas. Manual settings and data are kept separate.")
        props = st.multiselect("Consistency models", PROPERTIES, default=PROPERTIES)
        scenarios = st.multiselect("Scenarios", SCENARIOS, default=["Normal"])
        c1, c2, c3 = st.columns(3)
        er = c1.selectbox("Experiment read concern", ["majority", "local"])
        ew = c2.selectbox("Experiment write concern", ["majority", 1])
        ec = c3.checkbox("Experiment causal sessions", value=True)
        cfg = Settings(er, ew, "secondaryPreferred", ec)
        matrix = st.checkbox("Compare all 8 concern/session configurations")
        reps = st.number_input("Repetitions per combination", 1, 100, 1)
        total = len(props) * len(scenarios) * int(reps) * (8 if matrix else 1)
        st.caption(f"{total} trials. Fault trials can take up to several minutes each. "
                   "Manual clients are reserved while the suite runs; Stop restores any injected fault.")
        predicted_configs = [Settings(r, w, "secondaryPreferred", c) for r, w, c in product(
            ("local", "majority"), (1, "majority"), (False, True))] if matrix else [cfg]
        st.dataframe([{"Read": c.read, "Write": str(c.write), "Causal": c.causal,
                       "Model": p, "Prediction": prediction(c, p)}
                      for c in predicted_configs for p in PROPERTIES],
                     hide_index=True, width="stretch")
        st.caption("Predictions include durability through failover. Without causal sessions, the displayed "
                   "baseline makes no general guarantee across all scenarios; normal operation can still satisfy the properties.")
        st.markdown(f"[MongoDB guarantee table]({SOURCE})")
        start_col, stop_col = st.columns(2)
        if start_col.button("Run experiments", type="primary", disabled=not total or any(lab.busy(k) for k in lab.jobs)):
            submit("suite", run_suite, lab.actors, lab.log, cfg, props, scenarios, int(reps), matrix, lab.cancel)
        if stop_col.button("Stop experiments", disabled=not lab.busy("suite")):
            lab.cancel.set()
            lab.log.emit("suite_cancel_requested")
        job_result("suite")
        job = lab.jobs.get("suite")
        rows = job.result() if job and job.done() and job.exception() is None else [
            e for e in lab.log.snapshot() if e["event"] == "trial_finished"]
        if rows:
            summary = [{"Trial": r["trial"], "Model": r["property"], "Scenario": r["scenario"],
                        "Read": r["settings"]["read"], "Write": str(r["settings"]["write"]),
                        "Causal": r["settings"]["causal"], "Outcome": r["verdict"],
                        "Errors": len(r["evidence"]["errors"])} for r in rows]
            st.dataframe(summary, hide_index=True, width="stretch")
            st.download_button("Download experiment results", json.dumps(rows, indent=2),
                               file_name="experiments.json", mime="application/json")
        st.caption("No violation observed describes only the recorded history. Unavailable operations and "
                   "unmet preconditions are inconclusive. A write timeout may still have changed the database.")

    st.subheader("Live event log")
    st.caption(f"Saved immediately to {lab.log.path.relative_to(ROOT)} · Latest 1,500 events shown; full history stays on disk.")
    f1, f2, f3 = st.columns(3)
    client_filter = f1.selectbox("Client filter", ["All", "A", "B"])
    include_diagnostics = f2.checkbox("Include topology / diagnostic events", value=False)
    event_filter = f3.text_input("Event contains", placeholder="operation, command, fault, trial…")
    events = [e for e in lab.log.snapshot()
              if (client_filter == "All" or e.get("client") == client_filter)
              and (include_diagnostics or e.get("category") != "diagnostic")
              and event_filter.lower() in e["event"].lower()]
    st.dataframe([{"#": e["event_index"], "Time": e["timestamp"], "Client": e.get("client", "—"),
                   "Event": e["event"], "Server": str(e.get("server", "")),
                   "Status": e.get("status", e.get("verdict", "")),
                   "Details": json.dumps(e, ensure_ascii=False)} for e in reversed(events[-100:])],
                 hide_index=True, width="stretch", height=350)
    with st.expander("Full JSON for the latest 20 matching events"):
        st.code("\n".join(json.dumps(e, ensure_ascii=False) for e in events[-20:]), language="json")
    # Read the full file only on request; refreshing logs must not repeatedly load an unbounded history.
    if st.button("Prepare full log download"):
        st.session_state["log_download"] = lab.log.path.read_bytes()
    if "log_download" in st.session_state:
        st.download_button("Download prepared JSONL snapshot", st.session_state["log_download"],
                           file_name=f"{lab.log.run_id}-events.jsonl", mime="application/x-ndjson")


workspace()
