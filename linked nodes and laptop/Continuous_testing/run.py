"""Continuous, no-checkpoint consistency trials. See README.md in this folder."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'Script_testing'))
import config
from pymongo import MongoClient, ReadPreference, ReturnDocument
from pymongo.errors import ConnectionFailure, ExecutionTimeout, WriteConcernError, PyMongoError
from pymongo.monitoring import CommandListener
from pymongo.write_concern import WriteConcern
import pymongo


def utc():
    return datetime.now(timezone.utc).isoformat()


class Trace(CommandListener):
    """One listener per worker; retain only the current operation's commands."""
    def __init__(self):
        self.events = []

    def started(self, event):
        self.events.append(dict(event='started', command=event.command_name,
                                server=str(event.connection_id), request=event.request_id,
                                monotonic_ns=time.monotonic_ns()))

    def succeeded(self, event):
        self.events.append(dict(event='succeeded', request=event.request_id,
                                duration_us=event.duration_micros))

    def failed(self, event):
        self.events.append(dict(event='failed', request=event.request_id,
                                duration_us=event.duration_micros, error=str(event.failure)))


def operation(trace, role, number, kind, call, **intent):
    trace.events.clear()
    row = dict(event='operation', role=role, operation_id=f'{role}-{number}', kind=kind,
               time=utc(), started_ns=time.monotonic_ns(), **intent)
    try:
        value = call()
        if hasattr(value, 'matched_count'):
            value = dict(acknowledged=value.acknowledged, matched=value.matched_count)
        row.update(status='ok', value=value)
    except PyMongoError as exc:
        # An error never triggers a resubmission. A write may already have applied.
        row.update(status='error', error=repr(exc), outcome_unknown=kind == 'write',
                   fatal=not isinstance(exc, (ConnectionFailure, ExecutionTimeout, WriteConcernError)))
    row.update(ended_ns=time.monotonic_ns(), commands=list(trace.events))
    row['elapsed_ms'] = (row['ended_ns'] - row['started_ns']) / 1e6
    return row


def version(doc, run_id):
    if doc is None:
        return -1
    if doc.get('_id') != run_id or type(doc.get('version')) is not int or doc['version'] < 0:
        raise ValueError('Unexpected test document; inspect for external modification')
    return doc['version']


def workload(test, role, col, session, trace, run_id, running, emit):
    """No sleeps, retries, primary polling, or document resets in this loop."""
    sequence = 0
    number = 0
    previous = None
    uncertain_write = False
    query = {'_id': run_id}

    def attempt(kind, call, **intent):
        nonlocal number
        number += 1
        row = operation(trace, role, number, kind, call, **intent)
        if row.get('fatal'):
            emit(row)
            raise RuntimeError('Non-transient database error: ' + row['error'])
        return row

    def check(rows, required=None, observed=None, reason=None):
        status = 'inconclusive' if reason else ('pass' if observed >= required else 'violation')
        for row in rows:
            emit(row)
        emit(dict(event='check', role=role, status=status, required=required,
                  observed=observed, reason=reason,
                  operations=[row['operation_id'] for row in rows]))

    def read():
        return attempt('read', lambda: col.find_one(query, session=session))

    def observed(row, *earlier):
        try:
            return version(row['value'], run_id)
        except ValueError:
            for record in (*earlier, row):
                emit(record)
            raise

    def write(n, witness=False, dependency=None):
        update = {'$set': {'version': n}} if dependency is None else {
            '$set': {'followed_version': dependency, 'follow_operation': n}}
        if witness:
            return attempt('write', lambda: col.find_one_and_update(
                query, update, session=session, return_document=ReturnDocument.BEFORE),
                sequence=n, dependency=dependency)
        return attempt('write', lambda: col.update_one(query, update, session=session), sequence=n)

    while running():
        sequence += 1
        if role == 'A' and test in ('mr', 'wfr'):
            row = write(sequence)
            emit(row)
            if row['status'] == 'ok' and row['value']['matched'] != 1:
                raise ValueError('Writer lost the baseline document')
        elif test == 'ryw':
            first = write(sequence)
            if first['status'] != 'ok':
                check([first], reason='Write outcome unknown; no acknowledged dependency')
                continue
            if first['value']['matched'] != 1:
                emit(first)
                raise ValueError('Writer lost the baseline document')
            if not running():
                check([first], required=sequence, reason='Deadline before dependent read')
                continue
            # Buffer both records: no log-file or terminal I/O between this pair.
            second = read()
            second['write_to_read_gap_ms'] = (second['started_ns'] - first['ended_ns']) / 1e6
            check([first, second], required=sequence,
                  observed=observed(second, first) if second['status'] == 'ok' else None,
                  reason=None if second['status'] == 'ok' else 'Dependent read failed')
        elif test == 'mr':
            row = read()
            if row['status'] != 'ok':
                check([row], required=previous, reason='Read failed; retain previous observation')
                continue
            seen = observed(row)
            if previous is None:
                emit(row)
            else:
                check([row], required=previous, observed=seen)
            previous = seen if previous is None else max(previous, seen)
        elif test == 'mw':
            row = write(sequence, witness=True)
            if row['status'] != 'ok':
                uncertain_write = True
                check([row], required=previous, reason='Write outcome and pre-image unknown')
                continue
            seen = observed(row)
            if seen < 0:
                emit(row)
                raise ValueError('Witness write did not match the baseline document')
            if previous is None:
                emit(row)
            else:
                check([row], required=previous, observed=seen,
                      reason='Intervening uncertain write prevents this dependency check'
                      if uncertain_write else None)
            previous = sequence
            uncertain_write = False
        else:  # WFR role B; its writes preserve A's version counter.
            first = read()
            if first['status'] != 'ok':
                check([first], reason='Read failed; no new dependency')
                continue
            seen = observed(first)
            if seen <= 0:
                check([first], reason='No positive writer version observed')
                continue
            if not running():
                check([first], required=seen, reason='Deadline before dependent write')
                continue
            second = write(sequence, witness=True, dependency=seen)
            prior = observed(second, first) if second['status'] == 'ok' else None
            check([first, second], required=seen, observed=prior,
                  reason='Dependent write outcome unknown' if second['status'] != 'ok' else
                  ('Dependent write matched no document' if prior == -1 else None))


def setup(client, col, run_id, emit):
    """One all-member baseline, strictly before the workload clock starts."""
    hello = client.admin.command('hello')
    expected = {f'{host}:{port}' for host, port in config.CLUSTER.nodes.values()}
    if set(hello.get('hosts', []) + hello.get('passives', [])) != expected:
        raise ValueError('Setup requires the configured nine-member replica set')
    col.with_options(write_concern=WriteConcern(w=len(expected), wtimeout=15000)).insert_one(
        {'_id': run_id, 'version': 0})
    with ExitStack() as stack:
        copies = []
        for host in sorted(expected):
            direct = stack.enter_context(MongoClient('mongodb://' + host, directConnection=True,
                retryReads=False, retryWrites=False, serverSelectionTimeoutMS=1000, timeoutMS=1000))
            copies.append(direct[col.database.name].get_collection(col.name,
                read_preference=ReadPreference.NEAREST, read_concern=col.read_concern))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if all(version(copy.find_one({'_id': run_id}), run_id) == 0 for copy in copies):
                emit(dict(event='baseline_ready', hosts=sorted(expected), primary=hello.get('primary')))
                return
            time.sleep(.05)
    raise TimeoutError('Baseline was not visible on all nine members within 30 seconds')


def verdict(counts, completed):
    if counts['violation']:
        return 'VIOLATION_OBSERVED'
    if not completed or counts['error'] or counts['inconclusive'] or not counts['pass']:
        return 'UNAVAILABLE_OR_INCONCLUSIVE'
    return 'NO_VIOLATION_OBSERVED'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--test', required=True, choices=['ryw', 'mr', 'mw', 'wfr'])
    parser.add_argument('--config', required=True, choices=config.CONFIGS)
    parser.add_argument('--causal', required=True, choices=['on', 'off'])
    parser.add_argument('--scenario', required=True,
                        choices=['baseline', 'secondary', 'primary', 'majority-loss'])
    parser.add_argument('--duration', type=float, default=180)
    parser.add_argument('--disconnect-at', type=float, default=30)
    parser.add_argument('--restore-at', type=float, default=90)
    parser.add_argument('--results-dir', type=Path, default=ROOT / 'results' / 'continuous')
    args = parser.parse_args(argv)
    if not all(math.isfinite(n) and n > 0 for n in
               (args.duration, args.disconnect_at, args.restore_at)):
        parser.error('Duration and reminder times must be finite positive seconds')
    if args.scenario != 'baseline' and not args.disconnect_at < args.restore_at < args.duration:
        parser.error('Partition runs require disconnect-at < restore-at < duration')

    run_id = f'{args.test}_{args.config}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}'
    folder = args.results_dir.resolve() / run_id
    folder.mkdir(parents=True, exist_ok=False)
    (folder / 'sources').mkdir()
    for source in [Path(__file__), Path(__file__).with_name('mark.py'),
                   ROOT / 'Script_testing' / 'config.py', ROOT / 'scripts' / 'cluster_config.py',
                   ROOT / 'pyproject.toml', ROOT / 'uv.lock']:
        shutil.copy2(source, folder / 'sources' / source.name)
    settings = dict(vars(args), results_dir=str(args.results_dir), run_id=run_id,
        profile=config.CONFIGS[args.config], retry_reads=config.RETRY_READS,
        retry_writes=config.RETRY_WRITES, server_selection_timeout_ms=config.SERVER_SELECTION_TIMEOUT_MS,
        operation_timeout_ms=config.OPERATION_TIMEOUT_MS,
        write_concern_timeout_ms=config.WRITE_CONCERN_TIMEOUT_MS,
        uri=config.MONGO_URI, database=config.DATABASE_NAME, collection='continuous_trials',
        read_preference=config.READ_PREFERENCE, pymongo=pymongo.version, python=sys.version)
    (folder / 'settings.json').write_text(json.dumps(settings, indent=2) + '\n')
    print(f'Results: {folder}\nPreparing all nine members. Do not disconnect yet.', flush=True)
    counts = Counter()
    reasons = Counter()
    lock = threading.Lock()
    stop = threading.Event()
    started = None
    completed = False
    failure = None
    with (folder / 'events.jsonl').open('a', encoding='utf-8', buffering=1) as stream:
        def emit(row):
            row = dict(row)
            row.setdefault('time', utc())
            if started is not None:
                row['elapsed_s'] = (row.get('started_ns', time.monotonic_ns()) - started) / 1e9
            # ponytail: one log lock; use per-role files if logging limits throughput.
            with lock:
                if row['event'] in ('operation', 'check'):
                    counts[row['status']] += 1
                    if row.get('reason'):
                        reasons[row['reason']] += 1
                    if row['event'] == 'operation':
                        counts[f"{row['role']}_{row['kind']}_{row['status']}"] += 1
                stream.write(json.dumps(row, default=str) + '\n')

        try:
            if config.RETRY_READS or config.RETRY_WRITES:
                raise ValueError('Continuous experiments require automatic retries disabled')
            with ExitStack() as stack:
                initial = stack.enter_context(config.create_client())
                setup(initial, config.get_collection(initial, 'continuous_trials', args.config), run_id, emit)
                roles = ['A', 'B'] if args.test in ('mr', 'wfr') else ['A']
                workers = []
                for role in roles:
                    trace = Trace()
                    client = stack.enter_context(config.create_client([trace]))
                    col = config.get_collection(client, 'continuous_trials', args.config)
                    col.find_one({'_id': run_id})  # Warm outside the measured session.
                    session = stack.enter_context(config.start_session(client, args.causal == 'on'))
                    workers.append((role, col, session, trace))

                started = time.monotonic_ns()
                (folder / 'start.json').write_text(json.dumps(dict(time=utc(), monotonic_ns=started)))
                emit(dict(event='workload_start', duration_s=args.duration))
                print('RUNNING: operations continue automatically; no Enter or primary wait.', flush=True)
                deadline = started + int(args.duration * 1e9)
                def running():
                    return not stop.is_set() and time.monotonic_ns() < deadline
                with ThreadPoolExecutor(max_workers=len(roles)) as pool:
                    futures = [pool.submit(workload, args.test, role, col, session, trace,
                                           run_id, running, emit)
                               for role, col, session, trace in workers]
                    try:
                        next_progress = 0
                        reminders = [] if args.scenario == 'baseline' else [
                            (args.disconnect_at, 'DISCONNECT the chosen laptop(s) now'),
                            (args.restore_at, 'RESTORE connectivity now')]
                        while running():
                            for future in futures:
                                if future.done():
                                    future.result()  # Surface worker errors immediately.
                            elapsed = (time.monotonic_ns() - started) / 1e9
                            while reminders and elapsed >= reminders[0][0]:
                                _, message = reminders.pop(0)
                                emit(dict(event='reminder', message=message,
                                          note='Reminder only, not evidence of fault injection'))
                                print(f'[{elapsed:.1f}s] {message}. Workload is still running.', flush=True)
                            if elapsed >= next_progress:
                                with lock:
                                    snapshot = dict(counts)
                                print(f'[{elapsed:.0f}/{args.duration:g}s] '
                                      f'operations OK={snapshot.get("ok", 0)} '
                                      f'errors={snapshot.get("error", 0)} '
                                      f'violations={snapshot.get("violation", 0)}', flush=True)
                                next_progress = elapsed + 5
                            stop.wait(.2)  # Only the progress thread waits; workers never sleep.
                    finally:
                        stop.set()
                        print('Finishing in-flight operations (up to the operation timeout)…', flush=True)
                    for future in futures:
                        future.result()
                completed = True
        except KeyboardInterrupt:
            failure = 'Interrupted by operator'
        except Exception as exc:
            failure = repr(exc)
        finally:
            stop.set()
            emit(dict(event='workload_end', completed=completed, error=failure))
    result = verdict(counts, completed)
    summary = dict(run_id=run_id, result=result, completed=completed, error=failure,
                   counts=dict(counts), inconclusive_reasons=dict(reasons), requested_duration_s=args.duration,
                   elapsed_s=(time.monotonic_ns() - started) / 1e9 if started else None,
                   note='Bounded single-document checks. Errors and interrupted pairs are retained; '
                        'a reminder does not confirm a partition. See markers.jsonl and README.')
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(f'{result}\nResults: {folder}', flush=True)
    if failure:
        print(failure, file=sys.stderr)
    return 1 if counts['violation'] else (0 if completed else 2)


if __name__ == '__main__':
    raise SystemExit(main())
