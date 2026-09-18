"""Offline checks for config, ownership, mode switching, and the shared UI."""
from concurrent.futures import Future
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'scripts'))
import cluster_config as config
from cluster_config import Cluster, LOCAL
from dashboard_state import Dashboard
from experiments import healthy, run_suite
from lab import EventLog, Settings
from setup_lab import validate_config, ready
from faults import control


class FakeActor:
    def __init__(self, client_id, log, cluster=LOCAL):
        self.client_id, self.cluster, self.closed = client_id, cluster, False

    def close(self):
        self.closed = True


def state(cluster):
    return [dict(node=node, role='PRIMARY' if i == 0 else 'SECONDARY', reachable=True)
            for i, node in enumerate(cluster.nodes)]


class LinkedChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.base = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.multiple(config, ROOT=self.base,
            SETTINGS=self.base / 'secrets/laptop.json', TEAM=self.base / 'secrets/team.json'))
        self.team = config.create_team()
        self.addresses = dict(A='192.168.43.10', B='192.168.43.11', C='192.168.43.12')
        config.save_settings('A', self.addresses, self.team)
        self.cluster = config.load_linked()
        self.dashboards = []

    def tearDown(self):
        for d in self.dashboards:
            d.control.shutdown(wait=True)
            d.diagnostics.shutdown(wait=True)
            for actor in d.actors:
                actor.close()
        self.stack.close()
        self.temp.cleanup()

    def dashboard(self):
        self.stack.enter_context(patch('dashboard_state.Actor', FakeActor))
        self.stack.enter_context(patch('dashboard_state.EventLog', lambda: EventLog(self.base / 'results')))
        d = Dashboard()
        self.dashboards.append(d)
        return d

    def test_inventory_render_and_compose(self):
        self.assertEqual(len(self.cluster.nodes), 9)
        self.assertEqual(sum(m['votes'] for m in self.cluster.members()), 7)
        self.assertEqual([m['_id'] for m in self.cluster.members() if not m['votes']], [5, 8])
        for owner in 'ABC':
            cluster = Cluster(True, owner, self.addresses, self.team)
            rendered = config.render_linked(cluster)
            self.assertEqual(set(rendered['services']), set(cluster.local_nodes))
            for node, service in rendered['services'].items():
                self.assertEqual(len(service['extra_hosts']), 6)
                self.assertNotIn(cluster.nodes[node][0], service['extra_hosts'])
                self.assertIn(cluster.nodes[node][0], service['networks']['mongo-lab']['aliases'])
                self.assertTrue(service['ports'][0].startswith(self.addresses[owner] + ':'))
                self.assertNotIn(self.team['password'], json.dumps(rendered))
            # Parse using the real Compose CLI; this does not contact Docker or start containers.
            result = subprocess.run([*cluster.compose, 'config', '--format', 'json'],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            expanded = json.loads(result.stdout)
            self.assertEqual(set(expanded['services']), set(cluster.local_nodes))
        self.assertNotEqual(LOCAL.project, 'mongo-local-lab')
        self.assertTrue(all(port >= 28017 for _, port in LOCAL.nodes.values()))

    def test_invalid_settings_and_existing_cluster_rejected(self):
        with self.assertRaises(ValueError):
            config.save_settings('B', self.addresses, self.team)
        with self.assertRaises(ValueError):
            config.save_settings('A', {**self.addresses, 'C': self.addresses['A']}, self.team)
        with self.assertRaises(ValueError):
            config.save_settings('A', {**self.addresses, 'A': '127.0.0.1'}, self.team)
        with self.assertRaises(ValueError):
            config.validate_team({**self.team, 'key': 'not-a-key'})
        expected = dict(_id=self.cluster.name, members=self.cluster.members())
        validate_config(expected, self.cluster)
        expected['members'][5]['votes'] = 1
        with self.assertRaises(RuntimeError):
            validate_config(expected, self.cluster)

    def test_health_and_mutation_boundaries(self):
        self.assertTrue(healthy(state(self.cluster), self.cluster))
        self.assertFalse(healthy(state(self.cluster)[:-1], self.cluster))
        self.assertFalse(healthy(state(self.cluster), LOCAL))
        with patch('faults.control.command') as command:
            with self.assertRaises(ValueError):
                control.change('stop', 'mongo4', cluster=self.cluster)
            with self.assertRaises(ValueError):
                control.clear_data(None, confirmed=True, cluster=self.cluster)
            command.assert_not_called()
        actors = [FakeActor('A', None, self.cluster)]
        with self.assertRaises(ValueError):
            run_suite(actors, None, Settings(), [], ['Primary failure'], 1, False, threading.Event())
        data = {'Config': {'Labels': {'com.docker.compose.project': LOCAL.project,
                'com.docker.compose.service': 'mongo1', 'com.docker.compose.project.working_dir': str(ROOT.parent)}}}
        with self.assertRaises(RuntimeError):
            control.check_labels(data, LOCAL)

    def test_bootstrap_is_pending_but_another_team_is_rejected(self):
        initial = dict(_id=self.cluster.name, members=self.cluster.members(bootstrap=True))
        with patch('setup_lab.direct') as direct:
            client = direct.return_value.__enter__.return_value
            client.admin.command.side_effect = [{'isWritablePrimary': True}, {'config': initial}]
            self.assertFalse(ready(self.cluster))
            initial['members'][0]['tags']['team'] = 'another-team'
            client.admin.command.side_effect = [{'isWritablePrimary': True}, {'config': initial}]
            with self.assertRaises(RuntimeError):
                ready(self.cluster)

    def test_connect_disconnect_and_busy_guard(self):
        d = self.dashboard()
        self.assertEqual(d.connection_state, 'disconnected')
        with patch('dashboard_state.load_linked', return_value=self.cluster), \
             patch('dashboard_state.check_hosts'), patch('dashboard_state.start_shared'), \
             patch('dashboard_state.setup') as setup, patch('dashboard_state.control.stop_cluster') as stop:
            d.request_connection(True)
            d.jobs['connection'].result(timeout=3)
            self.assertEqual(d.cluster, self.cluster)
            self.assertEqual(d.connection_state, 'connected')
            self.assertEqual(stop.call_args_list[0].args, (LOCAL,))
            pending = Future()
            d.jobs['A'] = pending
            with self.assertRaises(ValueError):
                d.request_connection(False)
            pending.set_result(None)
            d.request_connection(False)
            d.jobs['connection'].result(timeout=3)
            self.assertEqual(d.cluster, LOCAL)
            self.assertEqual(d.connection_state, 'disconnected')
            self.assertEqual(stop.call_args_list[-1].args, (self.cluster,))
            setup.assert_called_once()

    def test_failed_connect_restores_local_and_preflight_keeps_clients(self):
        d = self.dashboard()
        old = d.actors[:]
        with patch('dashboard_state.load_linked', return_value=self.cluster), \
             patch('dashboard_state.check_hosts', side_effect=RuntimeError('Missing hostname')), \
             patch('dashboard_state.control.stop_cluster') as stop:
            d.request_connection(True)
            d.jobs['connection'].result(timeout=3)
            self.assertEqual(d.actors, old)
            stop.assert_not_called()
        # A failure after pausing local mode must restore local clients and containers.
        config.render_linked(self.cluster)
        with patch('dashboard_state.load_linked', return_value=self.cluster), \
             patch('dashboard_state.check_hosts'), \
             patch('dashboard_state.start_shared', side_effect=RuntimeError('Peer unavailable')), \
             patch('dashboard_state.setup') as setup, patch('dashboard_state.control.stop_cluster') as stop:
            d.request_connection(True)
            d.jobs['connection'].result(timeout=3)
            self.assertEqual(d.cluster, LOCAL)
            self.assertEqual(d.connection_state, 'disconnected')
            self.assertIn('Peer unavailable', d.message)
            self.assertTrue(all(a.closed for a in old))
            self.assertEqual([c.args[0] for c in stop.call_args_list], [LOCAL, self.cluster])
            setup.assert_called_once()

    def test_cancellation_and_failed_recovery_are_explicit(self):
        d = self.dashboard()
        started = threading.Event()
        config.render_linked(self.cluster)
        def connecting(cluster, cancel, progress):
            started.set()
            cancel.wait(3)
            raise RuntimeError('Connection cancelled')
        with patch('dashboard_state.load_linked', return_value=self.cluster), \
             patch('dashboard_state.check_hosts'), patch('dashboard_state.start_shared', side_effect=connecting), \
             patch('dashboard_state.control.stop_cluster'), patch('dashboard_state.setup', side_effect=RuntimeError('Docker offline')):
            d.request_connection(True)
            self.assertTrue(started.wait(3))
            with self.assertRaises(ValueError):
                d.submit('A', lambda: None)
            d.connection_cancel.set()
            d.jobs['connection'].result(timeout=3)
            self.assertEqual(d.connection_state, 'error')
            self.assertIn('Docker offline', d.message)
        with patch('dashboard_state.control.stop_cluster'), patch('dashboard_state.setup'):
            d.request_connection(False)
            d.jobs['connection'].result(timeout=3)
            self.assertEqual(d.connection_state, 'disconnected')

    @unittest.skipUnless(shutil.which('node'), 'Optional JS bootstrap check needs Node.js')
    def test_bootstrap_javascript_accepts_auth_return_values_and_resumes(self):
        from link_setup import USER_SETUP
        script = USER_SETUP.replace('ADMIN_PASSWORD', '"admin-test"').replace('APP_PASSWORD', '"app-test"')
        for object_result in (False, True):
            for initial in ([], ['setup'], ['setup', 'lab']):
                shim = "const users=new Set(" + json.dumps(initial) + ");\n"
                shim += """
const admin={
  auth(user,pwd) { if (!users.has(user)) throw Error('Not authenticated'); return AUTH_RESULT; },
  createUser(doc) { if(users.has(doc.user)) throw Error('Duplicate user'); users.add(doc.user); },
  getUser(user) { return users.has(user) ? {user} : null; }
};
const db={getSiblingDB:()=>admin};
function quit(code) { process.exit(code || (users.has('setup') && users.has('lab') ? 0 : 9)); }
""".replace('AUTH_RESULT', '{ok:1}' if object_result else '1')
                result = subprocess.run([shutil.which('node'), '-'], input=shim + script,
                                        capture_output=True, text=True, timeout=5, cwd=ROOT)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_shared_ui_shows_nine_nodes_and_limits_controls(self):
        from streamlit.testing.v1 import AppTest
        d = self.dashboard()
        d.cluster = self.cluster
        d.target = self.cluster
        d.connection_state = 'connected'
        d.status = state(self.cluster)
        d.actors = [FakeActor('A', d.log, self.cluster), FakeActor('B', d.log, self.cluster)]
        self.stack.enter_context(patch('dashboard_state.Dashboard', return_value=d))
        self.stack.enter_context(patch.object(d, 'refresh_status'))
        # Clear the shared app cache so AppTest uses the injected standalone state.
        import streamlit as st
        st.cache_resource.clear()
        app = AppTest.from_file(str(ROOT / 'scripts/app.py'), default_timeout=10).run()
        self.assertFalse(app.exception, app.exception)
        self.assertEqual(len(app.metric), 9)
        self.assertEqual(app.selectbox(key='fault_node').options, self.cluster.local_nodes)
        self.assertEqual(len(app.selectbox(key='routeA').options), 12)
        self.assertTrue(next(b for b in app.button if b.label == 'Clear MongoDB data').disabled)
        self.assertEqual(next(m for m in app.multiselect if m.label == 'Scenarios').options, ['Normal'])
        with patch('dashboard_state.control.stop_cluster'), patch('dashboard_state.setup'):
            next(b for b in app.button if b.label == 'Disconnect').click().run()
            d.jobs['connection'].result(timeout=3)
            app.run()
            self.assertFalse(app.exception, app.exception)
            self.assertEqual(len(app.metric), 3)
            self.assertTrue(any(b.label == 'Connect' for b in app.button))
        st.cache_resource.clear()


if __name__ == '__main__':
    unittest.main(verbosity=2)
