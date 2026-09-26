"""Offline checks: uv run --locked Continuous_testing/check.py (no cluster needed)."""
from collections import Counter
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pymongo.errors import AutoReconnect, OperationFailure
import run
import mark


def history(test, role, reads=(), writes=(), witness=()):
    """Bound the real worker by scripted DB operations, never by wall time."""
    col = Mock()
    remaining = len(reads) + len(writes) + len(witness)
    sessions = []
    intentions = []

    def scripted(values, kind):
        values = iter(values)
        def call(*args, **kwargs):
            nonlocal remaining
            remaining -= 1
            sessions.append(kwargs['session'])
            if kind != 'read':
                intentions.append(args[1]['$set'])
            value = next(values)
            if isinstance(value, Exception):
                raise value
            if kind == 'update':
                return SimpleNamespace(acknowledged=True, matched_count=1)
            return None if value is None else {'_id': 'test', 'version': value}
        return call
    col.find_one.side_effect = scripted(reads, 'read')
    col.update_one.side_effect = scripted(writes, 'update')
    col.find_one_and_update.side_effect = scripted(witness, 'witness')
    rows = []
    session = object()
    run.workload(test, role, col, session, run.Trace(), 'test', lambda: remaining > 0, rows.append)
    assert remaining == 0 and all(s is session for s in sessions)
    return rows, intentions


def checks(rows):
    return [r for r in rows if r['event'] == 'check']


def main():
    # A stale first read is retained, and a later pass never erases the violation.
    rows, _ = history('ryw', 'A', writes=[True, True], reads=[0, 2])
    assert [r['status'] for r in checks(rows)] == ['violation', 'pass']
    assert all('write_to_read_gap_ms' in r for r in rows if r.get('kind') == 'read')
    # A failed write isn't resubmitted: the next distinct write has sequence 2.
    rows, intent = history('ryw', 'A', writes=[AutoReconnect('cut'), True], reads=[2])
    assert [x['version'] for x in intent] == [1, 2]
    assert [r['status'] for r in checks(rows)] == ['inconclusive', 'pass']
    assert next(r for r in rows if r.get('status') == 'error')['outcome_unknown']
    rows, _ = history('ryw', 'A', writes=[True], reads=[AutoReconnect('cut')])
    assert checks(rows)[0]['status'] == 'inconclusive'
    # Deadline after a write must prevent another operation and retain the pair.
    rows, _ = history('ryw', 'A', writes=[True])
    assert checks(rows)[0]['reason'] == 'Deadline before dependent read'

    rows, _ = history('mr', 'B', reads=[10, AutoReconnect('cut'), 9, 11])
    assert [r['status'] for r in checks(rows)] == ['inconclusive', 'violation', 'pass']
    # Both MR and WFR writers continue past the old fixed 10/11/12 sequence.
    for test in ('mr', 'wfr'):
        rows, intent = history(test, 'A', writes=[True] * 5)
        assert [x['version'] for x in intent] == [1, 2, 3, 4, 5]

    rows, _ = history('mw', 'A', witness=[0, 0, 2])
    assert [r['status'] for r in checks(rows)] == ['violation', 'pass']
    rows, intent = history('mw', 'A', witness=[0, AutoReconnect('cut'), 2, 3])
    assert [r['status'] for r in checks(rows)] == ['inconclusive', 'inconclusive', 'pass']
    assert [x['version'] for x in intent] == [1, 2, 3, 4]

    rows, intent = history('wfr', 'B', reads=[10, 12], witness=[9, 13])
    assert [r['status'] for r in checks(rows)] == ['violation', 'pass']
    assert [x['followed_version'] for x in intent] == [10, 12]
    assert all('version' not in x for x in intent)  # B doesn't overwrite A's sequence.
    rows, _ = history('wfr', 'B', reads=[10, 12], witness=[AutoReconnect('cut'), 12])
    assert [r['status'] for r in checks(rows)] == ['inconclusive', 'pass']
    rows, _ = history('wfr', 'B', reads=[10], witness=[None])
    assert checks(rows)[0]['status'] == 'inconclusive'
    rows, _ = history('wfr', 'B', reads=[0, 10])
    assert all(r['status'] == 'inconclusive' for r in checks(rows))

    trace = run.Trace()
    row = run.operation(trace, 'A', 1, 'write', Mock(side_effect=OperationFailure('denied', code=13)))
    assert row['fatal'] and row['status'] == 'error'
    assert row['ended_ns'] >= row['started_ns'] and row['elapsed_ms'] >= 0
    assert run.verdict(Counter(), True) == 'UNAVAILABLE_OR_INCONCLUSIVE'
    assert run.verdict(Counter({'pass': 1}), True) == 'NO_VIOLATION_OBSERVED'
    assert run.verdict(Counter({'pass': 1, 'error': 1}), True) == 'UNAVAILABLE_OR_INCONCLUSIVE'
    assert run.verdict(Counter({'violation': 1, 'error': 1}), False) == 'VIOLATION_OBSERVED'
    with patch('pymongo.MongoClient') as factory:
        run.config.create_client()
        assert factory.call_args.kwargs['retryReads'] is False
        assert factory.call_args.kwargs['retryWrites'] is False

    # Baseline includes non-voters, requires all nine acknowledgements, and is
    # never reset by a workload iteration. Missing members reject setup.
    hosts = [f'{host}:{port}' for host, port in run.config.CLUSTER.nodes.values()]
    initial, collection = Mock(), Mock()
    initial.admin.command.return_value = {'hosts': hosts[:7], 'passives': hosts[7:]}
    collection.database.name, collection.name = 'consistency_lab', 'continuous_trials'
    copies = []
    def member(*args, **kwargs):
        assert kwargs['retryReads'] is False and kwargs['retryWrites'] is False
        client, copy = Mock(), Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.__getitem__ = Mock(return_value=SimpleNamespace(get_collection=lambda *a, **k: copy))
        copy.find_one.side_effect = [None, {'_id': 'test', 'version': 0}] + [
            {'_id': 'test', 'version': 0}] * 9
        copies.append(copy)
        return client
    with patch.object(run, 'MongoClient', side_effect=member), patch.object(run.time, 'sleep'):
        run.setup(initial, collection, 'test', Mock())
    assert len(copies) == 9 and all(copy.find_one.call_count >= 2 for copy in copies)
    assert collection.with_options.call_args.kwargs['write_concern'].document['w'] == 9
    collection.with_options.return_value.insert_one.assert_called_once_with({'_id': 'test', 'version': 0})
    initial.admin.command.return_value['passives'] = []
    try:
        run.setup(initial, collection, 'test', Mock())
    except ValueError:
        pass
    else:
        raise AssertionError('Missing passive members must fail setup')

    with redirect_stdout(io.StringIO()):
        for duration in ('nan', 'inf', '-1'):
            with patch('sys.stderr', io.StringIO()):
                try:
                    run.main(['--test', 'ryw', '--config', 'C1', '--causal', 'on',
                              '--scenario', 'baseline', '--duration', duration])
                except SystemExit as exc:
                    assert exc.code == 2
                else:
                    raise AssertionError('Invalid duration accepted')

    # Real duration/controller/thread/log/summary integration; fake only MongoDB.
    with TemporaryDirectory() as directory:
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        col = Mock()
        col.update_one.return_value = SimpleNamespace(acknowledged=True, matched_count=1)
        with patch.object(run.config, 'create_client', return_value=client), \
             patch.object(run.config, 'get_collection', return_value=col), \
             patch.object(run.config, 'start_session', return_value=session), \
             patch.object(run, 'setup') as baseline, redirect_stdout(io.StringIO()):
            # MR writer and reader actually run in separate worker threads.
            col.find_one.side_effect = lambda query, **kw: {'_id': query['_id'], 'version': 1}
            code = run.main(['--test', 'mr', '--config', 'C1', '--causal', 'on',
                             '--scenario', 'baseline', '--duration', '.1', '--results-dir', directory])
        assert code == 0
        baseline.assert_called_once()
        folder = next(Path(directory).iterdir())
        summary = json.loads((folder / 'summary.json').read_text())
        rows = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines()]
        assert summary['completed'] and summary['elapsed_s'] >= .1
        assert {r['role'] for r in rows if r['event'] == 'operation'} == {'A', 'B'}
        assert (folder / 'sources' / 'config.py').exists()
        with redirect_stdout(io.StringIO()):
            mark.main([str(folder), 'disconnected', '--note', 'Offline check only'])
        marker = json.loads((folder / 'markers.jsonl').read_text())
        assert marker['action'] == 'disconnected' and marker['elapsed_s'] >= 0
        # A network error must not terminate the controller. Reminders must be
        # emitted while the workers are active, not become a pause checkpoint.
        with patch.object(run.config, 'create_client', return_value=client), \
             patch.object(run.config, 'get_collection', return_value=col), \
             patch.object(run.config, 'start_session', return_value=session), \
             patch.object(run, 'setup'), redirect_stdout(io.StringIO()):
            failed_once = False
            def interrupted_update(*args, **kwargs):
                nonlocal failed_once
                if not failed_once:
                    failed_once = True
                    raise AutoReconnect('simulated partition')
                return SimpleNamespace(acknowledged=True, matched_count=1)
            col.update_one.side_effect = interrupted_update
            code = run.main(['--test', 'mr', '--config', 'C1', '--causal', 'on',
                '--scenario', 'secondary', '--duration', '.4', '--disconnect-at', '.05',
                '--restore-at', '.15', '--results-dir', directory])
        assert code == 0
        partition = next(p for p in Path(directory).iterdir() if p != folder)
        report = json.loads((partition / 'summary.json').read_text())
        events = [json.loads(line) for line in (partition / 'events.jsonl').read_text().splitlines()]
        assert report['completed'] and report['counts']['error'] == 1
        assert report['counts']['A_write_ok'] > 0
        assert report['result'] == 'UNAVAILABLE_OR_INCONCLUSIVE'
        assert len([r for r in events if r['event'] == 'reminder']) == 2
    print('PASS: four workloads, failures without retries, persistent sessions/dependencies, '
          'deadline, uncertainty, concurrent runner, logs, summary and markers (offline)')


if __name__ == '__main__':
    main()
