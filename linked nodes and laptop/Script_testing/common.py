"""Shared experiment plumbing. Coordination files are not MongoDB causal tokens."""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

if __name__ != "__main__":
    from pymongo import ReadPreference
    from pymongo.read_concern import ReadConcern
    from pymongo.write_concern import WriteConcern
    from pymongo.monitoring import CommandListener

    class Trace(CommandListener):
        def __init__(self):
            self.events = []
        def started(self, event):
            if event.command_name in ("find", "update"):
                self.events.append({"command": event.command_name, "server": str(event.connection_id), "started_ns": time.perf_counter_ns()})
        def succeeded(self, event):
            pass
        def failed(self, event):
            pass
from config import CLUSTER, ROOT, CONFIGS, create_client, get_collection, start_session


class Inconclusive(Exception):
    pass


class Experiment:
    def __init__(self, test):
        p = argparse.ArgumentParser(description=__doc__)
        p.add_argument('--config', choices=CONFIGS, default='C1')
        p.add_argument('--role', choices=['A', 'B'], default='A')
        p.add_argument('--run-id', required=True, help='Unique ID; same for A and B')
        p.add_argument('--results-dir', default=str(ROOT / 'results'))
        p.add_argument('--pause', action='store_true', help='Manual failure checkpoint')
        p.add_argument('--coord-timeout', type=float, default=180)
        p.add_argument('--causal', choices=['on','off'], default='off')
        p.add_argument('--read-interval', type=float, default=0.0)
        p.add_argument('--reads', type=int, default=100)
        self.args = p.parse_args()
        if self.args.read_interval < 0 or self.args.reads < 1:
            p.error('Read interval must be nonnegative and reads positive')
        self.pending = []
        self.mr_previous = None
        self.mr_violation = False
        if not re.fullmatch(r'[A-Za-z0-9_-]+', self.args.run_id):
            p.error('run-id must contain only letters, numbers, underscores or hyphens')
        self.test = test
        self.root = Path(self.args.results_dir) / f'{test}_{self.args.config}_{self.args.run_id}'
        self.root.mkdir(parents=True, exist_ok=True)
        # An exclusive role claim rejects stale IDs and accidental duplicate processes.
        (self.root / f'{self.args.role}.claim').open('x').close()
        self.trace = Trace()
        self.client = create_client([self.trace])
        self.col = get_collection(self.client, 'products', self.args.config)
        self.log('settings', profile=CONFIGS[self.args.config], causal_consistency=self.args.causal == "on", 
                 read_preference=str(self.col.read_preference), mongo_uri=CLUSTER.uri,
                 database=self.col.database.name, collection=self.col.name)

    def log(self, event, **data):
        row = dict(time=datetime.now(timezone.utc).isoformat(), role=self.args.role,
                   test=self.test, config=self.args.config, run_id=self.args.run_id,
                   event=event, **data)
        self.pending.append(row)

    def flush(self):
        with (self.root / f'{self.args.role}.jsonl').open('a', encoding='utf-8') as f:
            for row in self.pending:
                line = json.dumps(row, default=str)
                f.write(line + '\n')
                print(line, flush=True)
        self.pending.clear()

    def session(self):
        return start_session(self.client, self.args.causal == 'on')

    def warm_reads(self):
        # Outside the measured session, before its dependent operations.
        self.col.find_one({'_id': 'P001'})
        self.log('warmup_complete', note='Secondary read before measured operations')

    def track_mr(self, version):
        if self.mr_previous is not None and version < self.mr_previous:
            self.mr_violation = True
        self.mr_previous = version

    def signal(self, name):
        (self.root / name).touch()

    def wait(self, name):
        end = time.monotonic() + self.args.coord_timeout
        while not (self.root / name).exists():
            if (self.root / 'error').exists():
                raise Inconclusive('Other client reported an error')
            if time.monotonic() > end:
                raise Inconclusive(f'Timed out waiting for {name}')
            time.sleep(.1)

    def setup(self):
        # Reset only P001, outside the measured session. Restore all nine nodes
        # before starting and run one trial at a time across the whole team.
        setup = self.col.with_options(read_preference=ReadPreference.PRIMARY,
                                      read_concern=ReadConcern('local'),
                                      write_concern=WriteConcern(w=len(CLUSTER.nodes), wtimeout=15000))
        setup.replace_one({'_id': 'P001'}, {'_id': 'P001', 'name': 'Notebook',
                          'stock': 100, 'version': 0, 'trial': self.args.run_id}, upsert=True)
        self.wait_baseline()
        self.log('setup_complete', note='P001 reset and visible on all nine members; setup uses w=9')
        self.signal('ready')

    def wait_baseline(self):
        # Setup only: never pass these reads/tokens into A or B's test session.
        # Check every selectable data member with the profile's read concern;
        # majority read visibility can lag replication acknowledgement.
        from contextlib import ExitStack
        from pymongo import MongoClient

        hello = self.client.admin.command('hello')
        hosts = sorted(set(hello.get('hosts', []) + hello.get('passives', [])))
        expected = {f'{host}:{port}' for host, port in CLUSTER.nodes.values()}
        if set(hosts) != expected:
            raise Inconclusive('Baseline requires all nine shared replica-set members')
        end = time.monotonic() + min(30.0, self.args.coord_timeout)
        last = {}
        with ExitStack() as stack:
            collections = {}
            for host in hosts:
                client = MongoClient(
                    'mongodb://' + host, directConnection=True,
                    retryReads=False, retryWrites=False,
                    serverSelectionTimeoutMS=1000, timeoutMS=1000)
                stack.callback(client.close)
                collections[host] = client[self.col.database.name].get_collection(
                    self.col.name, read_preference=ReadPreference.NEAREST,
                    read_concern=self.col.read_concern)
            while time.monotonic() < end:
                matched = True
                for host, col in collections.items():
                    if time.monotonic() >= end:
                        matched = False
                        break
                    doc = col.find_one({'_id': 'P001'})
                    last[host] = doc
                    if not (doc and doc.get('trial') == self.args.run_id
                            and type(doc.get('version')) is int
                            and doc['version'] == 0):
                        matched = False
                if matched:
                    self.log('baseline_synchronized', members=hosts,
                             read_concern=self.col.read_concern.document,
                             trial=self.args.run_id, version=0)
                    return
                time.sleep(.05)
        self.log('baseline_timeout', documents=last)
        raise Inconclusive('Current trial/version 0 not visible on all replicas')

    def pause(self):
        if self.args.pause:
            self.log('checkpoint', note='Inject failure in another terminal, then press Enter here')
            self.flush()
            input('Failure checkpoint: press Enter to continue... ')

    def write(self, session, update, label):
        start = time.monotonic()
        result = self.col.update_one({'_id': 'P001'}, update, session=session)
        self.log(label, acknowledged=result.acknowledged, matched=result.matched_count,
                 elapsed_ms=round((time.monotonic()-start)*1000, 2),
                 operation_time=session.operation_time)
        if result.matched_count != 1:
            raise Inconclusive('P001 disappeared during test')

    def read(self, session, label):
        start = time.monotonic()
        doc = self.col.find_one({'_id': 'P001'}, session=session)
        self.log(label, document=doc, operation_time=session.operation_time,
                 elapsed_ms=round((time.monotonic()-start)*1000, 2))
        return doc

    def observed_version(self, doc):
        if doc is None:
            return -1
        if doc.get('trial') != self.args.run_id or type(doc.get('version')) is not int:
            raise Inconclusive('Unexpected data; possible overlapping trial or lost baseline')
        return doc['version']

    def read_a1(self, session):
        # B must actually observe A1 before the MR/WFR dependency exists.
        # Repeated reads here establish the prerequisite, not a post-write retry.
        end = time.monotonic() + self.args.coord_timeout
        while time.monotonic() < end:
            doc = self.read(session, 'B_establish_first_read')
            version = self.observed_version(doc)
            if self.test == 'mr':
                self.track_mr(version)
            if version == 10:
                return doc
            time.sleep(self.args.read_interval)
        raise Inconclusive('B never observed A1/version 10')

    def witness(self, label):
        # Diagnostic read, outside the tested causal session, on the primary.
        # The witness was computed atomically by the write, not inferred from
        # secondary visibility or from the final version alone.
        col = self.col.with_options(read_preference=ReadPreference.PRIMARY,
                                   read_concern=ReadConcern('local'))
        doc = col.find_one({'_id': 'P001'})
        self.log('diagnostic_witness', document=doc)
        if not doc or doc.get('trial') != self.args.run_id or doc.get('witness_label') != label:
            raise Inconclusive('Witness unavailable or rolled back; inspect logs')
        return doc.get('prior_version')

    def verdict(self, holds, detail):
        self.log('verdict', result='NO_VIOLATION_OBSERVED' if holds else 'VIOLATION_OBSERVED', detail=detail)
        return 0 if holds else 1


def run(test, body):
    e = None
    try:
        e = Experiment(test)
        return body(e)
    except Exception as exc:
        from pymongo.errors import ConnectionFailure, ExecutionTimeout, WTimeoutError
        fatal = not isinstance(exc, (Inconclusive, ConnectionFailure, ExecutionTimeout, WTimeoutError))
        if e:
            e.log('verdict', result='UNAVAILABLE_OR_INCONCLUSIVE', error=repr(exc), fatal=fatal,
                  note='A write error/timeout may have an uncertain outcome, not a proven violation')
            e.signal('error')
        else:
            print(f'Cannot start experiment: {exc}', flush=True)
        return 2
    finally:
        if e:
            e.log('command_trace', commands=e.trace.events)
            e.flush()
            e.client.close()


def batch_main():
    """Run both roles on this laptop against the connected nine-node cluster."""
    import csv
    import subprocess
    import sys
    import uuid
    import shutil
    p = argparse.ArgumentParser(description='Run N sequential trials; both client roles run on this laptop')
    p.add_argument('--test', choices=['ryw','mr','mw','wfr'], required=True)
    p.add_argument('--config', choices=CONFIGS, default='C3')
    p.add_argument('--causal', choices=['on','off'], default='off')
    p.add_argument('--count', type=int, default=1)
    p.add_argument('--read-interval', type=float, default=0.0)
    p.add_argument('--reads', type=int, default=100)
    args = p.parse_args()
    if not 1 <= args.count <= 10000 or args.read_interval < 0 or args.reads < 1:
        p.error('Count must be 1..10000, reads positive, and interval nonnegative')
    project = Path(__file__).resolve().parent.parent
    batch = f'{args.test}_{args.config}_{args.causal}_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{uuid.uuid4().hex[:8]}'
    root = project/'results'/batch
    root.mkdir(parents=True)
    lock = project/'results'/'.batch.lock'
    try:
        lock.open('x').close()
    except FileExistsError:
        p.error('Another batch is active, or its lock remains. See instructions before removing the lock.')
    counts = dict(NO_VIOLATION_OBSERVED=0, VIOLATION_OBSERVED=0, UNAVAILABLE_OR_INCONCLUSIVE=0)
    filenames = {'ryw':'test_read_your_writes.py','mr':'test_monotonic_reads.py',
                 'mw':'test_monotonic_writes.py','wfr':'test_writes_follow_reads.py'}
    completed = False
    fatal_reason = None
    def save():
        valid = counts['NO_VIOLATION_OBSERVED'] + counts['VIOLATION_OBSERVED']
        report = dict(settings=vars(args), completed=completed, attempted=sum(counts.values()),
                      evaluated=valid, counts=counts, fatal_reason=fatal_reason,
                      violation_rate=counts['VIOLATION_OBSERVED']/valid if valid else None)
        (root/'summary.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    try:
        (root/'sources').mkdir()
        for source in (project/'Script_testing').glob('*.py'):
            shutil.copy2(source,root/'sources'/source.name)
        shutil.copy2(project/'scripts'/'cluster_config.py', root/'sources'/'cluster_config.py')
        with (root/'trials.csv').open('w',newline='',encoding='utf-8') as out:
            writer=csv.writer(out)
            writer.writerow(['trial','run_id','status','detail'])
            for i in range(1,args.count+1):
                run_id=f'{batch}_{i:04d}'
                roles=['A','B'] if args.test in ('mr','wfr') else ['A']
                processes={}
                captures={}
                codes={}
                status='UNAVAILABLE_OR_INCONCLUSIVE'
                detail='No confirmed verdict'
                try:
                    for role in roles:
                        captures[role]=(root/f'{i:04d}_{role}.txt').open('w',encoding='utf-8')
                        command=[sys.executable,'-u',str(project/'Script_testing'/filenames[args.test]),
                                 '--config',args.config,'--causal',args.causal,
                                 '--role',role,'--run-id',run_id,'--results-dir',str(root/'raw'),
                                 '--reads',str(args.reads),'--read-interval',str(args.read_interval)]
                        processes[role]=subprocess.Popen(command,cwd=project,stdin=subprocess.DEVNULL,
                                                        stdout=captures[role],stderr=subprocess.STDOUT)
                    deadline=time.monotonic()+270
                    for role,process in processes.items():
                        codes[role]=process.wait(timeout=max(.1,deadline-time.monotonic()))
                except (OSError,subprocess.TimeoutExpired) as exc:
                    detail=repr(exc)
                finally:
                    for process in processes.values():
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                    for capture in captures.values():
                        capture.close()
                target='B' if 'B' in roles else 'A'
                def events(role):
                    result=[]
                    path=root/f'{i:04d}_{role}.txt'
                    if path.exists():
                        for line in path.read_text(encoding='utf-8',errors='replace').splitlines():
                            try:
                                row=json.loads(line)
                                if isinstance(row,dict) and row.get('run_id')==run_id and row.get('role')==role and row.get('test')==args.test and row.get('config')==args.config:
                                    result.append(row)
                            except ValueError:
                                pass
                    return result
                verdicts=[r for r in events(target) if r.get('event')=='verdict']
                expected={'NO_VIOLATION_OBSERVED':0,'VIOLATION_OBSERVED':1,'UNAVAILABLE_OR_INCONCLUSIVE':2}
                a_ok=target=='A' or (codes.get('A')==0 and any(r.get('event')=='role_complete' for r in events('A')))
                if len(verdicts)==1 and a_ok:
                    row=verdicts[0]
                    if row.get('result') in expected and codes.get(target)==expected[row['result']]:
                        status=row['result']
                        detail=row.get('error',row.get('detail',''))
                # A transient failure in either role invalidates the trial, but
                # does not stop the batch once both roles have exited normally.
                failures = [r for role in roles for r in events(role)
                            if r.get('event') == 'verdict'
                            and r.get('result') == 'UNAVAILABLE_OR_INCONCLUSIVE']
                if failures:
                    status = 'UNAVAILABLE_OR_INCONCLUSIVE'
                    detail = '; '.join(f"{r['role']}: {r.get('error', 'Inconclusive')}" for r in failures)
                fatal_rows = [r for r in failures if r.get('fatal')]
                if fatal_rows:
                    fatal_reason = 'Fatal setup/script error: ' + detail
                elif len(codes) != len(roles) or any(code not in (0, 1, 2) for code in codes.values()):
                    fatal_reason = 'Client launch/exit could not be confirmed: ' + repr(codes)
                elif any(not any(r.get('event') == 'command_trace' for r in events(role)) for role in roles):
                    fatal_reason = 'Client did not finish experiment logging; inspect role logs for launch/setup errors'
                elif status == 'UNAVAILABLE_OR_INCONCLUSIVE' and not failures:
                    fatal_reason = 'Missing or inconsistent verdict; inspect role logs'
                if fatal_reason:
                    status = 'UNAVAILABLE_OR_INCONCLUSIVE'
                    detail = fatal_reason + '; ' + detail
                counts[status]+=1
                writer.writerow([i,run_id,status,detail]); out.flush(); save()
                print(f'{i}/{args.count}: {status}',flush=True)
                if fatal_reason:
                    print('Batch stopped: ' + fatal_reason, flush=True)
                    break
            completed=fatal_reason is None and sum(counts.values())==args.count
            save()
        print('Results: '+str(root))
        return 2 if fatal_reason else 0
    finally:
        # Children run directly, so cleanup above waits for their actual exit.
        if all(process.poll() is not None for process in locals().get('processes',{}).values()):
            try:
                save()
            finally:
                lock.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(batch_main())
