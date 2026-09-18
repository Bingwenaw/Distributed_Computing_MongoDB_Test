"""Run with: uv run streamlit run scripts/app.py"""
from dataclasses import asdict
import json
from itertools import product
import sys

import streamlit as st

from lab import PROPERTIES, ROOT, SOURCE, Settings, object_json, prediction
from cluster_config import SETTINGS, TEAM, create_team, save_settings, load_linked, host_entries
from experiments import SCENARIOS, run_suite, healthy
sys.path.insert(0, str(ROOT))
from faults.control import change, restore


from dashboard_state import Dashboard


@st.cache_resource
def dashboard():
    """Share client connections and the exclusive data-reset lock across browser tabs."""
    # ponytail: one shared local lab across browser tabs; separate processes if multiple groups need isolation.
    return Dashboard()


st.set_page_config(page_title="MongoDB Consistency Lab", page_icon="🧪", layout="wide")
st.title("MongoDB Consistency Lab")
st.caption("Standalone copy · Local / shared cluster · Two independent clients · Every operation recorded")
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
        disabled = lab.blocked or lab.busy(name) or lab.busy("suite") or lab.busy("reset")
        with st.form(f"client_{name}"):
            db_col, coll_col = st.columns(2)
            database = db_col.text_input("Database", "lab", key=f"db{name}")
            collection = coll_col.text_input("Collection", "manual", key=f"coll{name}")
            rc_col, wc_col = st.columns(2)
            read = rc_col.selectbox("Read concern", ["majority", "local"], key=f"rc{name}")
            write = wc_col.selectbox("Write concern", ["majority", 1], key=f"wc{name}")
            route = st.selectbox("Read from", lab.cluster.routes, index=2, key=f"route{name}")
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


def connection_panel():
    busy = any(lab.busy(k) for k in lab.jobs)
    state = lab.connection_state
    if state == "connected":
        cluster_healthy = healthy(lab.status, lab.cluster)
        st.subheader("Connected · Shared cluster" if cluster_healthy else "Shared mode · Checking / degraded")
        st.caption(f"Laptop {lab.cluster.laptop} · Local and shared data are separate. Network loss never redirects writes to local data.")
    elif state == "disconnected":
        st.subheader("Disconnected · Local lab")
    else:
        st.subheader(state.title())
    st.info(lab.message)
    label = "Connect" if state == "disconnected" else "Return to local mode" if state == "error" else "Disconnect"
    if st.button(label, type="primary", disabled=busy or lab.switching, key="connection_button"):
        try:
            lab.request_connection(state == "disconnected")
        except Exception as exc:
            st.error(str(exc))
        else:
            st.rerun()
    if state == "connecting" and st.button("Cancel connection"):
        lab.connection_cancel.set()
        st.info("Cancellation requested; waiting for the current bounded setup step, then restoring local mode.")
    if busy and not lab.switching:
        st.caption("Finish active operations before switching. Use Stop experiments and wait for recovery first.")
    with st.expander("Connection setup · complete once on each laptop"):
        st.write("Join the same hotspot first. A creates the team file; B and C import the same file. Each laptop must click Connect.")
        disabled = busy or lab.blocked or lab.cluster.linked
        try:
            saved = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
        except (ValueError, OSError):
            saved = {}
        laptop = st.selectbox("This laptop", ["A", "B", "C"], index=["A", "B", "C"].index(saved.get("laptop", "A")), disabled=disabled)
        addresses = {name: st.text_input(f"Laptop {name} hotspot IPv4", saved.get("addresses", {}).get(name, ""), disabled=disabled)
                     for name in "ABC"}
        upload = st.file_uploader("Import shared team.json (B and C)", type=["json"], disabled=disabled)
        if st.button("Create team file on A", disabled=disabled or laptop != "A" or TEAM.exists()):
            create_team()
            st.success("Team file created. Download it below and share privately with your two friends.")
        if TEAM.exists():
            st.download_button("Download private team file", TEAM.read_bytes(), file_name="team.json", mime="application/json", disabled=disabled)
        if st.button("Save connection settings", disabled=disabled):
            try:
                if upload is not None:
                    if upload.size > 10_000:
                        raise ValueError("Team file is too large.")
                    team = json.loads(upload.getvalue())
                elif TEAM.exists():
                    team = json.loads(TEAM.read_text())
                else:
                    raise ValueError("Create or import the team file first.")
                save_settings(laptop, {name: value.strip() for name, value in addresses.items()}, team)
                st.success("Settings saved. Check your hosts file below before connecting.")
            except Exception as exc:
                st.error(str(exc))
        try:
            configured = load_linked()
        except (ValueError, OSError, KeyError):
            st.caption("Save valid settings to see the required hostname mappings.")
        else:
            st.write("Add/update these entries in each laptop's OS hosts file. This app does not edit system files.")
            st.code(host_entries(configured), language="text")
            st.caption("macOS/Linux: /etc/hosts. Windows: C:\\Windows\\System32\\drivers\\etc\\hosts. Allow the three laptops through the firewall on TCP 29017–29025. See README for setup and recovery.")


@st.fragment(run_every="1s")
def workspace():
    connection_panel()
    if lab.blocked:
        return
    token = lab.cluster.name
    if st.session_state.get("active_cluster") != token:
        for key in ("routeA", "routeB", "fault_node", "confirm_clear_data", "log_download"):
            st.session_state.pop(key, None)
        st.session_state["active_cluster"] = token
    if st.button("Clear MongoDB data", disabled=lab.cluster.linked or lab.blocked or any(lab.busy(k) for k in lab.jobs)):
        st.session_state["confirm_clear_data"] = True
    if st.session_state.get("confirm_clear_data") and not lab.cluster.linked:
        confirm_clear_data()
    job_result("reset")
    lab.refresh_status()
    with st.expander("Cluster & fault controls", expanded=True):
        nodes = list(lab.cluster.nodes)
        for offset in range(0, len(nodes), 3):
            for col, node in zip(st.columns(3), nodes[offset:offset + 3]):
                state = next((s for s in lab.status if s["node"] == node), {})
                col.metric(node, state.get("role", "CHECKING"))
                if lab.cluster.linked:
                    col.caption(f"Laptop {lab.cluster.owner(node)} · " + ("Non-voter" if node in ("mongo6", "mongo9") else "Voter"))
        st.caption("Network isolation disconnects a node from mongo-lab and can also remove client access. "
                   "Stop uses a short graceful shutdown; these faults do not guarantee a rollback.")
        node_col, action_col, button_col, restore_col = st.columns([2, 2, 1, 1])
        node = node_col.selectbox("Node", lab.cluster.local_nodes, key="fault_node")
        action = action_col.selectbox("Action", ["stop", "start", "isolate", "reconnect"], key="fault_action")
        disabled = lab.blocked or lab.busy("control") or lab.busy("suite") or lab.busy("reset")
        if button_col.button("Apply fault", disabled=disabled):
            submit("control", change, action, node, lab.log, lab.cluster)
        if restore_col.button("Restore this laptop", disabled=disabled):
            submit("control", restore, lab.log, lab.cluster)
        job_result("control")
        if lab.status and not any(s["reachable"] for s in lab.status):
            st.info("No node is reachable. Check Docker and hostname settings. In shared mode, check the hotspot and ask your friends to Connect; Disconnect returns to local mode.")

    left, right = st.columns(2)
    with left:
        client_panel(lab.actors[0])
    with right:
        client_panel(lab.actors[1])

    with st.expander("Repeatable experiments & predictions", expanded=False):
        if lab.cluster.linked:
            st.info("Shared mode supports automated Normal trials. Coordinate manual faults with each owner; only one laptop should run experiments at a time.")
        st.write("Each trial starts new client sessions and uses a unique document in `lab.experiments`. "
                 "Reads are scheduled across reachable replicas. Manual settings and data are kept separate.")
        props = st.multiselect("Consistency models", PROPERTIES, default=PROPERTIES)
        scenarios = st.multiselect("Scenarios", ["Normal"] if lab.cluster.linked else SCENARIOS, default=["Normal"])
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
        if start_col.button("Run experiments", type="primary", disabled=lab.blocked or not total or any(lab.busy(k) for k in lab.jobs)):
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
