"""Small, repeatable histories. A finite history can find a violation, not prove a guarantee."""
from dataclasses import asdict, replace
from itertools import product
import sys
import threading
import time
from uuid import uuid4

from lab import NODES, PROPERTIES, ROOT, Settings, prediction, topology
sys.path.insert(0, str(ROOT))
from faults.control import change, restore

SCENARIOS = ["Normal", "Secondary failure", "Primary failure", "Two nodes down", "Network partition"]


class Inconclusive(RuntimeError):
    pass


def evaluate(property_name, evidence):
    """Witness checks on controlled, single-document histories (no concurrent deletes)."""
    observations = evidence.get("observations", [])
    prerequisite = evidence.get("prerequisite")
    if prerequisite is None or not evidence.get("fault_verified", False):
        return "inconclusive"
    if property_name in ("Monotonic writes", "Writes-follow-reads") and not evidence.get("dependent_completed"):
        return "inconclusive"
    for item in observations:
        doc = item.get("document") or {}
        if property_name in ("Read-your-writes", "Monotonic reads"):
            if doc.get("version", 0) < prerequisite:
                return "violation observed"
            if property_name == "Monotonic reads":
                prerequisite = doc["version"]
        elif property_name == "Monotonic writes":
            if doc.get("step2") and not doc.get("step1"):
                return "violation observed"
        elif property_name == "Writes-follow-reads":
            if doc.get("derived_from") == prerequisite and doc.get("version", 0) < prerequisite:
                return "violation observed"
        else:
            raise ValueError("Unknown property.")
    if evidence.get("errors") or not observations:
        return "inconclusive"
    if property_name == "Monotonic writes" and not any((o.get("document") or {}).get("step2") for o in observations):
        return "inconclusive"
    if property_name == "Writes-follow-reads" and not any((o.get("document") or {}).get("derived_from") == prerequisite for o in observations):
        return "inconclusive"
    return "no violation observed"


def wait_topology(predicate, log, cancel, seconds=35):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel.is_set():
            raise Inconclusive("Cancelled by user.")
        state = topology()
        log.emit("topology", category="diagnostic", nodes=state)
        if predicate(state):
            return state
        cancel.wait(1)
    raise Inconclusive("The required topology/election was not observed before the deadline.")


def healthy(state):
    return sum(n["role"] == "PRIMARY" for n in state) == 1 and sum(n["role"] == "SECONDARY" for n in state) == 2


def apply_scenario(scenario, log, cancel):
    before = topology()
    if not healthy(before):
        raise Inconclusive("Trial requires three healthy nodes before fault injection.")
    leader = next(n["node"] for n in before if n["role"] == "PRIMARY")
    secondary = next(n["node"] for n in before if n["role"] == "SECONDARY")
    targets = []
    if scenario == "Normal":
        return before
    if scenario == "Secondary failure":
        targets = [secondary]
    elif scenario in ("Primary failure", "Network partition"):
        targets = [leader]
    elif scenario == "Two nodes down":
        targets = [leader, secondary]
    else:
        raise ValueError("Unknown scenario.")
    for node in targets:
        change("isolate" if scenario == "Network partition" else "stop", node, log)
    def achieved(state):
        if scenario == "Two nodes down":
            return sum(n["reachable"] for n in state) == 1 and not any(n["role"] == "PRIMARY" for n in state)
        remaining = [n for n in state if n["node"] not in targets]
        elected = [n for n in remaining if n["role"] == "PRIMARY"]
        return bool(elected) and all(n["reachable"] for n in remaining) and (
            scenario == "Secondary failure" or elected[0]["node"] != leader)
    after = wait_topology(achieved, log, cancel)
    log.emit("scenario_verified", scenario=scenario, targets=targets, before=before, after=after)
    return after


def trial(actors, log, settings, property_name, scenario, cancel):
    trial_id = uuid4().hex
    key = f"{trial_id}:{property_name}"
    a, b = actors
    evidence = {"prerequisite": None, "fault_verified": False, "dependent_completed": False,
                "observations": [], "errors": []}
    log.emit("trial_started", trial=trial_id, property=property_name, scenario=scenario,
             settings=asdict(settings), prediction=prediction(settings, property_name))

    def op(actor, operation, cfg=settings, document=None, query=None):
        if cancel.is_set():
            raise Inconclusive("Cancelled by user.")
        record = actor.execute(operation, cfg, collection="experiments",
                               query=query or {"_id": key}, document=document,
                               upsert=True, trial=trial_id)
        if record["status"] != "ok":
            raise Inconclusive(f"{actor.client_id}: {record['status']}: {record['error']}")
        return record["result"]

    def observe(actor, node, cfg, phase):
        try:
            rows = op(actor, "find", replace(cfg, route=node))
            document = rows[0] if rows else None
            evidence["observations"].append(dict(node=node, phase=phase, document=document))
            log.emit("observation", trial=trial_id, client=actor.client_id,
                     node=node, phase=phase, document=document)
        except Inconclusive as exc:
            evidence["errors"].append(str(exc))

    def sample(phase):
        # Observer uses local, noncausal reads to expose replica state, not advance A's session.
        actor = a if property_name in ("Read-your-writes", "Monotonic reads") else b
        cfg = settings if actor is a else Settings("local", 1, "primary", False)
        nodes = [n["node"] for n in topology() if n["role"] in ("PRIMARY", "SECONDARY")]
        if not nodes:
            evidence["errors"].append("No readable member available for observation.")
        for node in nodes:
            observe(actor, node, cfg, phase)

    fault_attempted = False
    try:
        wait_topology(healthy, log, cancel)
        a.reset(settings.causal)
        b.reset(settings.causal)
        if property_name == "Monotonic writes":
            op(a, "update_one", document={"$set": {"step1": True}})
            evidence["prerequisite"] = 1
        else:
            writer = b if property_name in ("Monotonic reads", "Writes-follow-reads") else a
            op(writer, "replace_one", document={"_id": key, "version": 1})
            if property_name != "Read-your-writes":
                initial = op(a, "find", replace(settings, route="primary"))
                if not initial or initial[0].get("version") != 1:
                    raise Inconclusive("The initial source version was not observed.")
            evidence["prerequisite"] = 1
        fault_attempted = scenario != "Normal"
        apply_scenario(scenario, log, cancel)
        evidence["fault_verified"] = True
        if property_name == "Monotonic reads":
            op(b, "update_one", document={"$set": {"version": 2}})
        elif property_name == "Monotonic writes":
            op(a, "update_one", document={"$set": {"step2": True}})
            evidence["dependent_completed"] = True
        elif property_name == "Writes-follow-reads":
            op(a, "update_one", document={"$set": {"derived_from": 1}})
            evidence["dependent_completed"] = True
        for _ in range(3):
            sample("during_scenario")
            if evaluate(property_name, evidence) == "violation observed":
                break
            cancel.wait(0.1)
    except Exception as exc:
        evidence["errors"].append(str(exc))
    finally:
        if fault_attempted:
            try:
                restore(log)
                # Recovery must continue even when the user cancels the experiment.
                wait_topology(healthy, log, threading.Event())
                if not cancel.is_set() and evidence["fault_verified"]:
                    sample("after_recovery")
            except Exception as exc:
                evidence["errors"].append(f"Recovery failed: {exc}. Run uv run faults/control.py restore")
    verdict = evaluate(property_name, evidence)
    return log.emit("trial_finished", trial=trial_id, property=property_name,
                    scenario=scenario, settings=asdict(settings), verdict=verdict,
                    prediction=prediction(settings, property_name), evidence=evidence)


def run_suite(actors, log, settings, properties, scenarios, repetitions, matrix, cancel):
    configs = [Settings(r, w, settings.route, c) for r, w, c in product(
        ("local", "majority"), (1, "majority"), (False, True))] if matrix else [settings]
    results = []
    log.emit("suite_started", configurations=len(configs), properties=properties,
             scenarios=scenarios, repetitions=repetitions)
    try:
        for cfg, prop, scenario, repetition in product(configs, properties, scenarios, range(repetitions)):
            if cancel.is_set():
                break
            log.emit("suite_progress", completed=len(results), repetition=repetition + 1)
            results.append(trial(actors, log, cfg, prop, scenario, cancel))
            if results[-1]["evidence"]["errors"] and not healthy(topology()):
                log.emit("suite_stopped", reason="Lab not healthy after trial; restore before continuing.")
                break
    finally:
        log.emit("suite_finished", completed=len(results), cancelled=cancel.is_set())
    return results
