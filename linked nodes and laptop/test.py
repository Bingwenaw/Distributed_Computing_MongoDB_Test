"""uv run test.py [--integration] [--faults] [--ui]"""
from pathlib import Path
from queue import Queue
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent / "scripts"))
from lab import Actor, EventLog, NODES, PROPERTIES, Settings, object_json, prediction
from experiments import evaluate, run_suite
from setup_lab import validate_config
from faults.control import change, clear_data, COMPOSE


class LabChecks(unittest.TestCase):
    def test_local_ports_match_server_and_healthcheck(self):
        from cluster_config import LOCAL
        result = subprocess.run([*LOCAL.compose, "config", "--format", "json"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        for node, (_, port) in LOCAL.nodes.items():
            with self.subTest(node=node):
                service = services[node]
                for args in (service["command"], service["healthcheck"]["test"]):
                    self.assertIn("--port", args)
                    self.assertEqual(int(args[args.index("--port") + 1]), port)
                self.assertEqual(len(service["ports"]), 1)
                published = service["ports"][0]
                self.assertEqual(published["host_ip"], "127.0.0.1")
                self.assertEqual(int(published["published"]), port)
                self.assertEqual(int(published["target"]), port)

    def test_mac_launcher(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            project = Path(temp) / "project with spaces"
            project.mkdir()
            launcher = project / "Start Lab.command"
            launcher.write_text((Path(__file__).parent / launcher.name).read_text())
            launcher.chmod(0o755)
            binaries = Path(temp) / "bin"
            binaries.mkdir()
            scripts = {
                "docker": '''#!/bin/bash
printf 'docker|%s|%s\\n' "$PWD" "$*" >> "$LAB_LAUNCH_TRACE"
case "$1 $2" in
  "info ") test -f "$LAB_DOCKER_READY" ;;
  "desktop start") touch "$LAB_DOCKER_READY" ;;
  "compose version") exit 0 ;;
  *) exit 9 ;;
esac
''',
                "uv": '''#!/bin/bash
printf 'uv|%s|%s\\n' "$PWD" "$*" >> "$LAB_LAUNCH_TRACE"
if [[ "$1" == sync && "$LAB_FAIL_SYNC" == 1 ]]; then exit 7; fi
''',
            }
            for name, script in scripts.items():
                executable = binaries / name
                executable.write_text(script)
                executable.chmod(0o755)
            trace = Path(temp) / "trace"
            env = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}",
                       LAB_LAUNCH_TRACE=str(trace), LAB_DOCKER_READY=str(Path(temp) / "ready"),
                       LAB_FAIL_SYNC="0")
            result = subprocess.run([str(launcher)], cwd=temp, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = [line.split("|", 2) for line in trace.read_text().splitlines()]
            self.assertTrue(all(Path(row[1]).resolve() == project.resolve() for row in rows))
            self.assertIn("desktop start --timeout 120", [row[2] for row in rows])
            self.assertEqual([row[2] for row in rows if row[0] == "uv"], [
                "sync --locked", "run --locked scripts/setup_lab.py --startup",
                "run --locked streamlit run scripts/app.py --server.headless false"])
            trace.write_text("")
            env["LAB_FAIL_SYNC"] = "1"
            failed = subprocess.run([str(launcher)], cwd=temp, env=env, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(failed.returncode, 7)
            self.assertNotIn("setup_lab.py", trace.read_text())
            self.assertIn("Startup stopped", failed.stdout)

    def test_mac_stopper(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            project = Path(temp) / "project with spaces"
            project.mkdir()
            stopper = project / "Stop Lab.command"
            stopper.write_text((Path(__file__).parent / stopper.name).read_text())
            stopper.chmod(0o755)
            binaries = Path(temp) / "bin"
            binaries.mkdir()
            scripts = {
                "pgrep": '#!/bin/bash\nprintf "%s\\n" "$LAB_STOP_PIDS"\n',
                "lsof": '''#!/bin/bash
if [[ "$3" == "$LAB_OWN_PID" ]]; then
    printf 'n%s\\n' "$LAB_PROJECT"
else
    printf 'n%s\\n' "$LAB_OTHER_PROJECT"
fi
''',
                "uv": '''#!/bin/bash
printf '%s\\n' "$*" >> "$LAB_STOP_TRACE"
exit "$LAB_STOP_DOCKER_EXIT"
''',
            }
            for name, script in scripts.items():
                executable = binaries / name
                executable.write_text(script)
                executable.chmod(0o755)
            trace = Path(temp) / "trace"
            own = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=project)
            other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=temp)
            reaper = threading.Thread(target=own.wait)
            reaper.start()
            env = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}",
                       LAB_STOP_PIDS=f"{own.pid}\n{other.pid}", LAB_OWN_PID=str(own.pid),
                       LAB_PROJECT=str(project.resolve()), LAB_OTHER_PROJECT=str(Path(temp).resolve()),
                       LAB_STOP_TRACE=str(trace), LAB_STOP_DOCKER_EXIT="0")
            try:
                result = subprocess.run([str(stopper)], cwd=temp, env=env,
                                        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                reaper.join(timeout=2)
                self.assertIsNotNone(own.returncode)
                self.assertIsNone(other.poll(), "Another project's process must remain running.")
                self.assertEqual(trace.read_text().splitlines(), ["run --locked scripts/shutdown.py"])
                self.assertIn("volumes and results/ logs are preserved", result.stdout)
                env["LAB_STOP_PIDS"] = str(other.pid)
                again = subprocess.run([str(stopper)], cwd=temp, env=env, stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, timeout=5)
                self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
                env["LAB_STOP_DOCKER_EXIT"] = "7"
                failed = subprocess.run([str(stopper)], cwd=temp, env=env, stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(failed.returncode, 7)
                self.assertIn("Shutdown incomplete", failed.stdout)
                self.assertNotIn("Lab stopped.", failed.stdout)
            finally:
                for process in (own, other):
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                reaper.join(timeout=2)

    def test_inputs(self):
        self.assertEqual(object_json('{"version": 1}'), {"version": 1})
        for invalid in ("[]", "null", "not json", "1"):
            with self.assertRaises(ValueError):
                object_json(invalid)
        with self.assertRaises(ValueError):
            Settings(read="1")
        with self.assertRaises(ValueError):
            Settings(route="unknown")
        with self.assertRaises(ValueError):
            change("stop", "another-project")

    def test_log_is_complete_and_ordered(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            log = EventLog(temp)
            threads = [threading.Thread(target=lambda: [log.emit("sample", client="A") for _ in range(10)])
                       for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            rows = [object_json(line) for line in log.path.read_text().splitlines()]
            self.assertEqual([r["event_index"] for r in rows], list(range(1, 31)))
            self.assertEqual(rows, log.snapshot())

    def test_session_independence_and_validation(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            log = EventLog(temp)
            a, b = Actor("A", log), Actor("B", log)
            try:
                a.reset(True)
                b.reset(True)
                old_a, old_b = a.session, b.session
                self.assertIsNot(a.client, b.client)
                self.assertNotEqual(old_a.session_id, old_b.session_id)
                a.reset(False)
                self.assertIs(b.session, old_b)
                self.assertIsNot(a.session, old_a)
                self.assertFalse(a.causal)
                record = a.execute("update_one", Settings(), document={"version": 2})
                self.assertEqual(record["status"], "error")
                self.assertEqual(record["error"]["type"], "ValueError")
            finally:
                a.close()
                b.close()

    def test_verdicts(self):
        def evidence(*documents, errors=None):
            return dict(prerequisite=1, fault_verified=True, dependent_completed=True,
                        observations=[{"document": d} for d in documents], errors=errors or [])
        self.assertEqual(evaluate(PROPERTIES[0], evidence({"version": 1})), "no violation observed")
        self.assertEqual(evaluate(PROPERTIES[0], evidence(None)), "violation observed")
        self.assertEqual(evaluate(PROPERTIES[1], evidence({"version": 1}, {"version": 2}, {"version": 1})), "violation observed")
        self.assertEqual(evaluate(PROPERTIES[1], evidence({"version": 1}, {"version": 2})), "no violation observed")
        for model, valid, invalid in [
            (PROPERTIES[2], {"step1": True, "step2": True}, {"step2": True}),
            (PROPERTIES[3], {"version": 1, "derived_from": 1}, {"derived_from": 1}),
        ]:
            self.assertEqual(evaluate(model, evidence(valid)), "no violation observed")
            self.assertEqual(evaluate(model, evidence(invalid)), "violation observed")
            self.assertEqual(evaluate(model, dict(evidence(invalid), dependent_completed=False)), "inconclusive")
            self.assertEqual(evaluate(model, evidence({})), "inconclusive")
        self.assertEqual(evaluate(PROPERTIES[0], evidence(errors=["timeout"])), "inconclusive")
        self.assertEqual(evaluate(PROPERTIES[0], dict(evidence(None), fault_verified=False)), "inconclusive")
        self.assertIn("Guaranteed", prediction(Settings(), PROPERTIES[0]))
        self.assertIn("No general", prediction(Settings(causal=False), PROPERTIES[0]))
        self.assertIn("Guaranteed", prediction(Settings(write=1), PROPERTIES[1]))
        self.assertIn("No general", prediction(Settings(write=1), PROPERTIES[0]))

    def test_migration_rejects_unexpected_topology(self):
        from cluster_config import LOCAL
        valid = {"_id": LOCAL.name, "members": LOCAL.members()}
        validate_config(valid)
        with self.assertRaises(RuntimeError):
            validate_config(dict(valid, _id="production"))

    def test_clear_data_requires_confirmation_and_limits_scope(self):
        from cluster_config import LOCAL
        config = dict(name=LOCAL.project, services={n: {} for n in NODES},
                      volumes={n: {"name": f"{LOCAL.project}_{n}"} for n in NODES})
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            log = EventLog(temp)
            with patch("faults.control.command") as command:
                with self.assertRaises(ValueError):
                    clear_data(log)
                command.assert_not_called()
                command.return_value = json.dumps(dict(config, name="another-project"))
                with self.assertRaises(RuntimeError):
                    clear_data(log, confirmed=True)
                self.assertEqual(command.call_count, 1)
            with patch("faults.control.command", side_effect=[json.dumps(config), "", "", "ready"]) as command:
                self.assertIn("fresh", clear_data(log, confirmed=True))
                self.assertEqual(command.call_args_list[2].args[0], [*COMPOSE, "down", "--volumes", "--timeout", "2"])
                self.assertEqual(log.snapshot()[-1]["event"], "data_reset_completed")
            with patch("faults.control.command", side_effect=[json.dumps(config), "", "", RuntimeError("startup failed")]):
                with self.assertRaisesRegex(RuntimeError, "Deletion may already have occurred"):
                    clear_data(log, confirmed=True)


def integration(include_faults=False):
    log = EventLog()
    actors = [Actor("A", log), Actor("B", log)]
    key = "smoke-" + uuid4().hex
    try:
        written = actors[0].execute("insert_one", Settings(), collection="integration_smoke",
                                    document={"_id": key, "version": 1})
        assert written["status"] == "ok", written
        for node in NODES:
            # A's session must survive changing read targets and obey afterClusterTime.
            found = actors[0].execute("find", Settings(route=node), collection="integration_smoke", query={"_id": key})
            assert found["status"] == "ok" and found["result"][0]["version"] == 1, found
        for read in ("local", "majority"):
            for write in (1, "majority"):
                for causal in (False, True):
                    cfg = Settings(read, write, "primary", causal)
                    updated = actors[1].execute("update_one", cfg, collection="integration_smoke",
                                               query={"_id": key}, document={"$inc": {"version": 1}})
                    assert updated["status"] == "ok", updated
                    found = actors[1].execute("find", cfg, collection="integration_smoke", query={"_id": key})
                    assert found["status"] == "ok" and found["result"], found
        normal = run_suite(actors, log, Settings(), PROPERTIES, ["Normal"], 1, True, threading.Event())
        assert len(normal) == 32, normal
        guaranteed = [r for r in normal if r["prediction"].startswith("Guaranteed")]
        assert guaranteed and all(r["verdict"] == "no violation observed" for r in guaranteed), guaranteed
        if include_faults:
            for scenario in ("Secondary failure", "Primary failure", "Two nodes down", "Network partition"):
                print(f"Checking {scenario}…", flush=True)
                result = run_suite(actors, log, Settings(), [PROPERTIES[0]], [scenario], 1, False, threading.Event())
                assert result and result[0]["evidence"]["fault_verified"], result
                assert result[0]["verdict"] != "violation observed", result
                assert not any("Recovery failed" in error for error in result[0]["evidence"]["errors"]), result
        wire = [r for r in log.snapshot() if r["event"] == "command_started" and r["command"] == "find"]
        assert wire and all(r["server"] and r["operation_id"] for r in wire)
        assert {tuple(r["server"]) for r in wire} >= {(n, p) for n, p in NODES.items()}
        print(f"Integration checks passed. Evidence: {log.path}")
    finally:
        for actor in actors:
            actor.close()


def ui_check():
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file("scripts/app.py", default_timeout=20).run()
    assert not app.exception, app.exception
    assert [b.label for b in app.button].count("Read") == 2
    assert [b.label for b in app.button].count("Write") == 2
    app.text_area(key="filterA").set_value("[]")
    next(b for b in app.button if b.label == "Read").click().run()
    assert not app.exception
    assert any("JSON object" in e.value for e in app.error)
    submitted = Queue()
    def read_result(actor, operation, settings, database, collection, query, document, upsert, limit):
        if operation == "find":
            assert document is None
            rows = [] if query["_id"] == "missing" else [{"_id": query["_id"]}]
        else:
            assert operation == "insert_one"
            rows = {"inserted_id": document["_id"]}
        result = dict(event="operation_finished", operation=operation, status="ok", result=rows,
                      database=database, collection=collection, query=query,
                      timestamp="2026-09-09T00:00:00+00:00", duration_ms=1)
        submitted.put((actor.client_id, query))
        return result
    with patch.object(Actor, "execute", autospec=True, side_effect=read_result):
        for name in ("A", "B"):
            # A different write-document ID must never replace the submitted read filter.
            app.text_area(key=f"doc{name}").set_value('{"_id":"write-only"}')
            for identifier in ("first", "second", "missing"):
                app.text_area(key=f"filter{name}").set_value('{"_id":"' + identifier + '"}')
                [b for b in app.button if b.label == "Read"][0 if name == "A" else 1].click().run()
                assert submitted.get(timeout=5) == (name, {"_id": identifier})
                app.run()
                assert not app.exception, app.exception
                assert any('"_id": "' + identifier + '"' in c.value for c in app.code)
                if identifier == "missing":
                    assert any("No documents matched" in message.value for message in app.info)
            [b for b in app.button if b.label == "Write"][0 if name == "A" else 1].click().run()
            assert submitted.get(timeout=5) == (name, {"_id": "missing"})
            app.run()
            assert not app.exception, app.exception
            rendered = [json.loads(item.value) for item in app.json]
            assert [] in rendered and {"inserted_id": "write-only"} in rendered
        sections = [m.value for m in app.markdown if m.value in ("**Read result**", "#### Write", "**Write result**")]
        assert sections == ["**Read result**", "#### Write", "**Write result**"] * 2, sections
    assert any(b.label == "Clear MongoDB data" for b in app.button)
    with patch("faults.control.command") as docker:
        next(b for b in app.button if b.label == "Clear MongoDB data").click().run()
        assert not app.exception, app.exception
        assert any(b.label == "Confirm delete and recreate" for b in app.button)
        docker.assert_not_called()  # Opening the confirmation never deletes data.
    reset_called = threading.Event()
    def fake_clear(log, *, confirmed=False, cluster=None):
        assert confirmed
        reset_called.set()
        return "Fresh replica set ready (mocked)."
    with patch("faults.control.clear_data", side_effect=fake_clear), patch("faults.control.command") as docker:
        # Rerun binds the fake reset function before clicking confirmation.
        app.run()
        if not any(b.label == "Confirm delete and recreate" for b in app.button):
            next(b for b in app.button if b.label == "Clear MongoDB data").click().run()
        next(b for b in app.button if b.label == "Confirm delete and recreate").click().run()
        assert reset_called.wait(5), {"errors": [e.value for e in app.error],
                                      "buttons": [(b.label, b.disabled) for b in app.button],
                                      "info": [i.value for i in app.info]}
        app.run()
        assert not app.exception, app.exception
        deadline = time.monotonic() + 5
        while list(app.json) and time.monotonic() < deadline:
            threading.Event().wait(0.05)
            app.run()
        assert not list(app.json), "Reset must clear both clients' old displayed results."
        docker.assert_not_called()
    print("Streamlit checks passed: changed read filters, independent clients, empty results, and invalid input.")


if __name__ == "__main__":
    options = set(sys.argv[1:])
    unknown = options - {"--integration", "--faults", "--ui"}
    if unknown:
        raise SystemExit(f"Unknown options: {unknown}")
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LabChecks))
    if not result.wasSuccessful():
        raise SystemExit(1)
    if "--integration" in options or "--faults" in options:
        integration("--faults" in options)
    if "--ui" in options:
        ui_check()
