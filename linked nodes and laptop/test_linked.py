"""Offline checks for config, ownership, mode switching, and the shared UI."""
from concurrent.futures import Future
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess
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
from lab import Actor, EventLog, Settings
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
    def test_peer_checks_distinguish_authentication_and_network_failures(self):
        from pymongo.errors import ConnectionFailure, OperationFailure
        from link_setup import peers_reachable, container_connections
        from setup_lab import wait_for
        with patch('link_setup.direct') as direct:
            command = direct.return_value.__enter__.return_value.admin.command
            command.side_effect = [{}, OperationFailure('not initialized', code=94)] * 9
            self.assertTrue(peers_reachable(self.cluster))
            for code in (13, 18):
                command.side_effect = [{}, OperationFailure('unauthorized', code=code)]
                with self.assertRaisesRegex(RuntimeError, 'mongo1 on laptop A.*still requires authentication'):
                    peers_reachable(self.cluster)
            command.side_effect = ConnectionFailure('Connection refused')
            with patch('setup_lab.time.monotonic', side_effect=[0, 0, 2]), patch('setup_lab.time.sleep'):
                with self.assertRaisesRegex(RuntimeError, 'mongo1 on laptop A.*Connection refused'):
                    wait_for(lambda: peers_reachable(self.cluster), 'all peers', seconds=1)
            command.side_effect = None
            command.return_value = {'setName': 'another-lab'}
            with self.assertRaisesRegex(RuntimeError, 'different replica set'):
                peers_reachable(self.cluster)
        with patch('link_setup.shell', side_effect=RuntimeError('mongo4.lab.test:29020 refused')):
            with self.assertRaisesRegex(RuntimeError, 'mongo1 cannot reach.*mongo4.lab.test:29020'):
                container_connections(self.cluster)

    def test_shutdown_deletes_volumes_but_mode_switch_preserves_them(self):
        import shutdown
        with patch('faults.control.validate_compose') as validate, \
             patch('faults.control.command') as command, \
             patch.object(shutdown, 'SETTINGS', self.base / 'secrets/laptop.json'), \
             patch.object(shutdown, 'load_linked', return_value=self.cluster), \
             patch('faults.control.render_linked'):
            shutdown.shutdown()
            self.assertEqual(validate.call_count, 2)
            self.assertEqual(command.call_count, 2)
            for call, cluster in zip(command.call_args_list, [LOCAL, self.cluster]):
                self.assertEqual(call.args[0], [*cluster.compose, 'down', '--volumes', '--timeout', '20'])
            control.stop_cluster(LOCAL)
            self.assertNotIn('--volumes', command.call_args.args[0])
            command.reset_mock()
            validate.side_effect = RuntimeError('Wrong folder')
            with self.assertRaisesRegex(RuntimeError, 'Wrong folder'):
                shutdown.shutdown()
            command.assert_not_called()

    def test_startup_rejects_occupied_dashboard_port_before_docker_changes(self):
        from setup_lab import startup
        with patch('setup_lab.socket.socket') as socket, \
             patch('setup_lab.stop_cluster') as stop, patch('setup_lab.setup') as setup:
            socket.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'No Docker containers were changed'):
                startup()
            stop.assert_not_called()
            setup.assert_not_called()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.base = Path(self.temp.name)
        self.stack = ExitStack()
        self.stack.enter_context(patch.multiple(config, ROOT=self.base,
            SETTINGS=self.base / 'secrets/laptop.json'))
        self.addresses = dict(A='192.168.43.10', B='192.168.43.11', C='192.168.43.12')
        config.save_settings('A', self.addresses)
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
            cluster = Cluster(True, owner, self.addresses)
            rendered = config.render_linked(cluster)
            self.assertEqual(set(rendered['services']), set(cluster.local_nodes))
            for node, service in rendered['services'].items():
                self.assertEqual(len(service['extra_hosts']), 6)
                self.assertNotIn(cluster.nodes[node][0], service['extra_hosts'])
                self.assertIn(cluster.nodes[node][0], service['networks']['mongo-lab']['aliases'])
                self.assertTrue(service['ports'][0].startswith(self.addresses[owner] + ':'))
                self.assertNotIn('--keyFile', service['command'])
                self.assertNotIn('--auth', service['command'])
                self.assertEqual(service['volumes'], [f'{node}:/data/db'])
                self.assertNotIn('entrypoint', service)
            # Parse using the real Compose CLI; this does not contact Docker or start containers.
            result = subprocess.run([*cluster.compose, 'config', '--format', 'json'],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            expanded = json.loads(result.stdout)
            self.assertEqual(set(expanded['services']), set(cluster.local_nodes))
            for node, service in expanded['services'].items():
                self.assertEqual(service['command'][0], 'mongod')
                self.assertEqual(service['command'][2], str(cluster.nodes[node][1]))
        self.assertNotEqual(LOCAL.project, 'mongo-local-lab')
        self.assertTrue(all(port >= 28017 for _, port in LOCAL.nodes.values()))

    def test_invalid_settings_and_existing_cluster_rejected(self):
        with self.assertRaises(ValueError):
            config.save_settings('B', self.addresses)
        with self.assertRaises(ValueError):
            config.save_settings('A', {**self.addresses, 'C': self.addresses['A']})
        with self.assertRaises(ValueError):
            config.save_settings('A', {**self.addresses, 'A': '127.0.0.1'})
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

    def test_legacy_bootstrap_is_pending_but_wrong_node_is_rejected(self):
        initial = dict(_id=self.cluster.name, members=self.cluster.members(bootstrap=True))
        with patch('setup_lab.direct') as direct:
            client = direct.return_value.__enter__.return_value
            client.admin.command.side_effect = [{'isWritablePrimary': True}, {'config': initial}]
            self.assertFalse(ready(self.cluster))
            initial['members'][0]['tags']['node'] = 'wrong-node'
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

    def test_settings_ignore_legacy_credentials_and_preserve_old_tags(self):
        self.assertFalse((self.base / 'secrets/team.json').exists())
        legacy = self.base / 'secrets/team.json'
        legacy.write_text('old credentials are deliberately not parsed')
        self.assertEqual(config.load_linked(), self.cluster)
        self.assertEqual(legacy.read_text(), 'old credentials are deliberately not parsed')
        current = dict(_id=self.cluster.name, members=self.cluster.members())
        for member in current['members']:
            member['tags']['team'] = 'old-team'
        validate_config(current, self.cluster)
        current['members'][0]['host'] = 'another-host:29017'
        with self.assertRaises(RuntimeError):
            validate_config(current, self.cluster)

    def test_initialization_reports_errors_and_reuses_existing_configuration(self):
        from pymongo.errors import OperationFailure
        from link_setup import bootstrap
        cancel = threading.Event()
        with patch('link_setup.direct') as direct:
            command = direct.return_value.__enter__.return_value.admin.command
            command.side_effect = [OperationFailure('not initialized', code=94), {'ok': 1}]
            bootstrap(self.cluster, cancel)
            self.assertEqual(command.call_args.args,
                ('replSetInitiate', {'_id': self.cluster.name, 'members': self.cluster.members()}))
            command.reset_mock()
            command.side_effect = [OperationFailure('not initialized', code=94),
                                   OperationFailure('peer refused initialization', code=74)]
            with self.assertRaisesRegex(OperationFailure, 'peer refused'):
                bootstrap(self.cluster, cancel)
            command.reset_mock()
            command.side_effect = [{'config': {'_id': self.cluster.name, 'members': self.cluster.members()}}]
            bootstrap(self.cluster, cancel)
            command.assert_called_once_with('replSetGetConfig')

    def test_connection_setup_saves_without_team_upload(self):
        from streamlit.testing.v1 import AppTest
        import streamlit as st
        d = self.dashboard()
        self.stack.enter_context(patch('dashboard_state.Dashboard', return_value=d))
        self.stack.enter_context(patch.object(d, 'refresh_status'))
        st.cache_resource.clear()
        app = AppTest.from_file(str(ROOT / 'scripts/app.py'), default_timeout=10).run()
        self.assertFalse(app.exception, app.exception)
        self.assertFalse(any('team file' in b.label.lower() for b in app.button))
        for owner, address in self.addresses.items():
            next(t for t in app.text_input if t.label == f'Laptop {owner} hotspot IPv4').set_value(address)
        next(b for b in app.button if b.label == 'Save connection settings').click().run()
        self.assertFalse(app.exception, app.exception)
        self.assertEqual(config.load_linked().addresses, self.addresses)
        self.assertFalse((self.base / 'secrets/team.json').exists())
        st.cache_resource.clear()

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


def live_startup_check():
    """Nine members on three Docker networks; no real laptop settings or volumes are used."""
    import socket
    from uuid import uuid4
    from pymongo import MongoClient, ReadPreference
    from pymongo.write_concern import WriteConcern
    from link_setup import bootstrap, peers_reachable, container_connections
    from setup_lab import wait_for
    project = 'linked-noauth-check-' + uuid4().hex[:10]
    # Reserve distinct free ports, then release them immediately before Docker starts.
    with tempfile.TemporaryDirectory(prefix='live-check-', dir=ROOT) as temp, \
         patch.object(config, 'ROOT', Path(temp)), ExitStack() as reservations:
        ports = {}
        for node in Cluster(True).nodes:
            probe = reservations.enter_context(socket.socket())
            probe.bind(('127.0.0.1', 0))
            ports[node] = probe.getsockname()[1]

        class TestCluster(Cluster):
            @property
            def nodes(self):
                return {node: (host, ports[node]) for node, (host, _) in super().nodes.items()}

            @property
            def project(self):
                return project

        cluster = TestCluster(True, 'A', dict(A='192.168.43.10', B='192.168.43.11', C='192.168.43.12'))
        rendered = dict(name=project, services={}, networks={}, volumes={})
        owners = [TestCluster(True, owner, cluster.addresses) for owner in 'ABC']
        for owner in owners:
            part = config.render_linked(owner)
            network = f'laptop-{owner.laptop.lower()}'
            rendered['networks'][network] = {}
            rendered['volumes'].update(part['volumes'])
            for node, service in part['services'].items():
                port = ports[node]
                service['ports'] = [f'127.0.0.1:{port}:{port}']
                service['command'] += ['--wiredTigerCacheSizeGB', '0.25']
                service['networks'] = {network: service['networks']['mongo-lab']}
                service['extra_hosts'] = {host: 'host-gateway' for host in service['extra_hosts']}
                rendered['services'][node] = service
        cluster.compose_file.write_text(json.dumps(rendered))
        def docker(*args, input=None):
            result = subprocess.run([*cluster.compose, *args], capture_output=True, text=True, timeout=180, input=input)
            if result.returncode:
                raise RuntimeError(result.stderr or result.stdout)
            return result.stdout
        def direct(node, cluster=cluster):
            return MongoClient('127.0.0.1', ports[node], directConnection=True,
                               serverSelectionTimeoutMS=1500, connectTimeoutMS=1000, socketTimeoutMS=5000,
                               read_preference=ReadPreference.NEAREST)
        def ping(node):
            with direct(node) as client:
                return client.admin.command('ping')['ok']
        def replicated():
            for node in cluster.nodes:
                with direct(node) as client:
                    if client.friends_demo.checks.find_one({'_id': project}) is None:
                        return False
            return True
        try:
            reservations.close()
            for restart in (False, True):
                docker('up', '-d')
                for node in cluster.nodes:
                    wait_for(lambda node=node: ping(node), node, seconds=40)
                with patch('link_setup.direct', direct), patch('setup_lab.direct', direct):
                    assert peers_reachable(cluster)
                    def shell(owner, node, script):
                        return docker('exec', '-T', node, 'mongosh', '--quiet', '--port', str(ports[node]),
                                      '--file', '/dev/stdin', input=script + '\n')
                    with patch('link_setup.shell', shell):
                        for owner in owners:
                            container_connections(owner)
                    bootstrap(cluster, threading.Event())
                    wait_for(lambda: ready(cluster), 'nine-member replica set', seconds=90)
                if not restart:
                    for node in cluster.nodes:
                        with direct(node) as client:
                            if client.admin.command('hello').get('isWritablePrimary'):
                                client.friends_demo.get_collection('checks', write_concern=WriteConcern('majority')).insert_one(
                                    {'_id': project, 'message': 'No passwords needed'})
                                break
                wait_for(replicated, 'document on all temporary nodes', seconds=30)
                # Exercise the real dashboard clients, including replica-set discovery and tagged reads.
                resolve = socket.getaddrinfo
                def resolve_test_host(host, port, *args, **kwargs):
                    if host in {host for host, _ in cluster.nodes.values()}:
                        host = '127.0.0.1'
                    return resolve(host, port, *args, **kwargs)
                with patch('socket.getaddrinfo', resolve_test_host), ExitStack() as clients:
                    actors = [Actor(owner.laptop, EventLog(Path(temp) / 'results'), owner) for owner in owners]
                    for actor in actors:
                        clients.callback(actor.close)
                    identifiers = [f'{project}-{restart}-{actor.client_id}' for actor in actors]
                    for actor, identifier in zip(actors, identifiers):
                        result = actor.execute('insert_one', Settings(route='primary'), database='friends_demo',
                                               document={'_id': identifier})
                        assert result['status'] == 'ok', result
                    for actor in actors:
                        for node in cluster.nodes:
                            def shared_read(actor=actor, node=node):
                                result = actor.execute('find', Settings(route=node), database='friends_demo',
                                                       query={'_id': {'$in': identifiers}})
                                assert result['status'] == 'ok', result
                                return {row['_id'] for row in result['result']} == set(identifiers)
                            wait_for(shared_read, f'{actor.client_id} reading all three writes from {node}', seconds=30)
                print('Nine-node data preserved after restart.' if restart else
                      'Nine nodes / three networks: password-free startup, three dashboard clients, majority writes and all-node reads passed.', flush=True)
                if not restart:
                    docker('down', '--timeout', '5')  # Keep these test volumes for restart verification.
        finally:
            docker('down', '--volumes', '--timeout', '5')  # Delete only this uniquely named test project.
            print('Temporary test resources removed.', flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--docker']:
        live_startup_check()
    else:
        unittest.main(verbosity=2)
