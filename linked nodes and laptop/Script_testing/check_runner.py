"""Offline regression check: uv run --locked Script_testing/check_runner.py."""
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

import common
from config import CLUSTER, CONFIGS, MONGO_URI, create_client, get_collection


def check():
    assert 'replicaSet=rs-linked' in MONGO_URI
    assert len(CLUSTER.nodes) == 9
    # Constructing handles does not connect or issue database operations.
    with patch('pymongo.MongoClient') as factory:
        create_client()
        assert factory.call_args.args == (CLUSTER.uri,)
    from pymongo import MongoClient
    with MongoClient(MONGO_URI, connect=False) as client:
        for name, profile in CONFIGS.items():
            col = get_collection(client, 'products', name)
            assert col.read_concern.level == profile['read_concern']
            assert col.write_concern.document['w'] == profile['write_concern']

    for name in ('ryw', 'mr', 'mw', 'wfr'):
        e = object.__new__(common.Experiment)
        e.test = name
        e.args = SimpleNamespace(run_id='trial', coord_timeout=30)
        e.col = Mock()
        e.wait_baseline, e.log, e.signal = Mock(), Mock(), Mock()
        e.setup()
        assert e.col.with_options.call_args.kwargs['write_concern'].document['w'] == 9
        reset = e.col.with_options.return_value.replace_one.call_args
        assert reset.kwargs == {'upsert': True}
        assert reset.args[1]['trial'] == 'trial' and reset.args[1]['version'] == 0
        e.wait_baseline.assert_called_once_with()
        e.signal.assert_called_once_with('ready')

    hosts = [f'{host}:{port}' for host, port in CLUSTER.nodes.values()]
    e = object.__new__(common.Experiment)
    e.args = SimpleNamespace(run_id='trial', coord_timeout=30)
    e.col, e.client, e.log = Mock(), Mock(), Mock()
    e.client.admin.command.return_value = {'hosts': hosts[:5] + hosts[6:8],
                                          'passives': [hosts[5], hosts[8]]}
    baseline = {'trial': 'trial', 'version': 0}
    clients = []
    def member(*args, **kwargs):
        client = Mock()
        col = Mock()
        # One stale read per node forces the synchronization retry.
        col.find_one.side_effect = [{'trial': 'old', 'version': 12}, baseline]
        client.__getitem__ = Mock(return_value=SimpleNamespace(get_collection=lambda *a, **k: col))
        clients.append(client)
        return client
    with patch('pymongo.MongoClient', side_effect=member) as factory, patch('common.time.sleep'):
        e.wait_baseline()
    assert {call.args[0] for call in factory.call_args_list} == {'mongodb://' + h for h in hosts}
    assert len(clients) == 9
    for client in clients:
        client.close.assert_called_once()
    e.client.admin.command.return_value.pop('passives')
    try:
        e.wait_baseline()
    except common.Inconclusive:
        pass
    else:
        raise AssertionError('Missing non-voting members must reject the baseline')

    # Real child processes exercise launch, file coordination, logs and cleanup.
    child = '''import argparse, json, time
from pathlib import Path
p = argparse.ArgumentParser()
for key in ('config', 'causal', 'role', 'run-id', 'results-dir', 'reads', 'read-interval'):
    p.add_argument('--' + key)
a = p.parse_args()
test = {'test_read_your_writes': 'ryw', 'test_monotonic_reads': 'mr',
        'test_monotonic_writes': 'mw', 'test_writes_follow_reads': 'wfr'}[Path(__file__).stem]
root = Path(a.results_dir) / a.run_id
root.mkdir(parents=True, exist_ok=True)
assert (root.parents[2] / '.batch.lock').exists()
if test in ('mr', 'wfr'):
    (root / a.role).touch()
    end = time.monotonic() + 5
    while not (root / ('B' if a.role == 'A' else 'A')).exists():
        assert time.monotonic() < end, 'Client roles did not share results'
        time.sleep(.01)
row = dict(role=a.role, run_id=a.run_id, test=test, config=a.config)
code = 0
if a.role == 'A' and test in ('mr', 'wfr'):
    print(json.dumps(dict(row, event='role_complete')))
else:
    code = {'C1': 0, 'C2': 1, 'C3': 2, 'C4': 2}[a.config]
    status = ['NO_VIOLATION_OBSERVED', 'VIOLATION_OBSERVED', 'UNAVAILABLE_OR_INCONCLUSIVE'][code]
    print(json.dumps(dict(row, event='verdict', result=status, fatal=a.config == 'C4')))
print(json.dumps(dict(row, event='command_trace', commands=[])))
raise SystemExit(code)
'''
    with TemporaryDirectory(prefix='script-testing-') as directory:
        project = Path(directory)
        scripts = project / 'Script_testing'
        scripts.mkdir()
        (project / 'scripts').mkdir()
        (project / 'scripts' / 'cluster_config.py').write_text('# inventory snapshot\n')
        for name in ('read_your_writes', 'monotonic_reads', 'monotonic_writes', 'writes_follow_reads'):
            (scripts / f'test_{name}.py').write_text(child)

        def batch(test, config, count):
            argv = ['common.py', '--test', test, '--config', config, '--causal', 'on', '--count', str(count)]
            before = set(project.glob('results/*/summary.json'))
            with patch.object(common, '__file__', str(scripts / 'common.py')), patch.object(sys, 'argv', argv):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = common.batch_main()
            created = set(project.glob('results/*/summary.json')) - before
            assert len(created) == 1
            report = created.pop()
            assert (report.parent / 'sources' / 'test_monotonic_reads.py').exists()
            assert (report.parent / 'sources' / 'cluster_config.py').exists()
            assert not (project / 'results' / '.batch.lock').exists()
            return code, json.loads(report.read_text())

        for test, config, count, status in (
            ('ryw', 'C1', 2, 'NO_VIOLATION_OBSERVED'),
            ('mr', 'C1', 2, 'NO_VIOLATION_OBSERVED'),
            ('mw', 'C2', 1, 'VIOLATION_OBSERVED'),
            ('wfr', 'C3', 2, 'UNAVAILABLE_OR_INCONCLUSIVE'),
        ):
            code, report = batch(test, config, count)
            assert code == 0 and report['completed']
            assert report['counts'][status] == count
        code, report = batch('wfr', 'C4', 3)
        assert code == 2 and not report['completed'] and report['attempted'] == 1

        (scripts / 'test_read_your_writes.py').write_text('import time; time.sleep(60)\n')
        with patch('common.time.monotonic', side_effect=[0, 271]), patch('subprocess.Popen', wraps=subprocess.Popen) as launch:
            code, report = batch('ryw', 'C1', 1)
        assert code == 2 and not report['completed']
        assert report['counts']['UNAVAILABLE_OR_INCONCLUSIVE'] == 1
        assert launch.call_args.args[0][0] == sys.executable
    print('PASS: nine-node setup, passive members, baseline retry, client coordination, verdicts and timeout cleanup')


if __name__ == '__main__':
    check()
