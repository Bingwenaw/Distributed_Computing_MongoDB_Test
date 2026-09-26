"""Offline, independent replay of retained trials; never connects to MongoDB.

Run: python3 Continuous_testing/audit_results.py
Writes CONTINUOUS_TRIAL_AUDIT.json; leaves original results unchanged.
"""
import ast
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def seen(op):
    return -1 if op['value'] is None else op['value']['version']


def replay(test, operations):
    """Reconstruct dependency edges from operations, not logged check results."""
    expected = {}

    def edge(rows, required=None, observed=None, reason=None):
        key = tuple(r['operation_id'] for r in rows)
        require(key not in expected, 'Duplicate reconstructed edge')
        expected[key] = dict(required=required, observed=observed, reason=reason,
                            status='inconclusive' if reason else
                            'pass' if observed >= required else 'violation')

    selected = sorted((o for o in operations if o['role'] ==
                       ('B' if test in ('mr', 'wfr') else 'A')),
                      key=lambda o: o['started_ns'])
    previous = None
    uncertain = False
    index = 0
    while index < len(selected):
        op = selected[index]
        index += 1
        if test == 'mr':
            require(op['kind'] == 'read', 'MR reader issued a write')
            if op['status'] == 'error':
                edge([op], previous, reason='Read failed; retain previous observation')
            else:
                value = seen(op)
                if previous is not None:
                    edge([op], previous, value)
                previous = value if previous is None else max(previous, value)
        elif test == 'mw':
            require(op['kind'] == 'write', 'MW issued a read')
            if op['status'] == 'error':
                edge([op], previous, reason='Write outcome and pre-image unknown')
                uncertain = True
            else:
                if previous is not None:
                    edge([op], previous, seen(op),
                         'Intervening uncertain write prevents this dependency check'
                         if uncertain else None)
                previous, uncertain = op['sequence'], False
        elif test == 'ryw':
            require(op['kind'] == 'write', 'RYW pair does not start with write')
            if op['status'] == 'error':
                edge([op], reason='Write outcome unknown; no acknowledged dependency')
            elif index == len(selected):
                edge([op], op['sequence'], reason='Deadline before dependent read')
            else:
                read = selected[index]
                index += 1
                require(read['kind'] == 'read', 'RYW dependent operation is not read')
                edge([op, read], op['sequence'], seen(read) if read['status'] == 'ok' else None,
                     'Dependent read failed' if read['status'] == 'error' else None)
        else:
            require(op['kind'] == 'read', 'WFR pair does not start with read')
            if op['status'] == 'error':
                edge([op], reason='Read failed; no new dependency')
            elif seen(op) <= 0:
                edge([op], reason='No positive writer version observed')
            elif index == len(selected):
                edge([op], seen(op), reason='Deadline before dependent write')
            else:
                write = selected[index]
                index += 1
                require(write['kind'] == 'write' and write['dependency'] == seen(op),
                        'WFR dependency does not match preceding read')
                value = seen(write) if write['status'] == 'ok' else None
                reason = ('Dependent write outcome unknown' if write['status'] == 'error'
                          else 'Dependent write matched no document' if value == -1 else None)
                edge([op, write], seen(op), value, reason)
    return expected


def brief(op):
    starts = [c for c in op['commands'] if c['event'] == 'started']
    return dict(id=op['operation_id'], role=op['role'], kind=op['kind'],
                start_s=op['elapsed_s'], end_s=op['elapsed_s'] + op['elapsed_ms']/1000,
                duration_ms=op['elapsed_ms'], server=starts[0]['server'] if starts else None,
                dispatch_wait_ms=(starts[0]['monotonic_ns']-op['started_ns'])/1e6 if starts else None,
                status=op['status'], sequence=op.get('sequence'), dependency=op.get('dependency'),
                value=op.get('value'), error=op.get('error'))


def audit(folder):
    settings = json.loads((folder/'settings.json').read_text())
    summary = json.loads((folder/'summary.json').read_text())
    start = json.loads((folder/'start.json').read_text())
    with (folder/'events.jsonl').open() as stream:
        events = [json.loads(line) for line in stream]
    operations = [e for e in events if e['event'] == 'operation']
    checks = [e for e in events if e['event'] == 'check']
    by_id = {o['operation_id']: o for o in operations}
    require(len(by_id) == len(operations), 'Duplicate operation IDs')
    require(settings['run_id'] == summary['run_id'] == folder.name, 'Run ID mismatch')
    require(summary['completed'] and summary['error'] is None, 'Incomplete run')
    require(not settings['retry_reads'] and not settings['retry_writes'], 'Retries enabled')
    require(settings['read_preference'] == 'secondary', 'Unexpected read preference')
    profile = {'C1': ('majority', 'majority'), 'C2': ('majority', 1),
               'C3': ('local', 1), 'C4': ('local', 'majority')}[settings['config']]
    require(tuple(settings['profile'][k] for k in ('read_concern','write_concern')) == profile,
            'Concern profile mismatch')
    require([settings[k] for k in ('server_selection_timeout_ms', 'operation_timeout_ms',
                                  'write_concern_timeout_ms')] == [15000, 20000, 10000],
            'Unexpected timeout settings')
    control = {}
    for name in ('baseline_ready', 'workload_start', 'workload_end'):
        matches = [e for e in events if e['event'] == name]
        require(len(matches) == 1, 'Missing/duplicate '+name)
        control[name] = matches[0]
    require(set(control['baseline_ready']['hosts']) ==
            {f'mongo{i}.lab.test:{29016+i}' for i in range(1, 10)}, 'Incomplete baseline')
    require(control['workload_end']['completed'], 'Workload end incomplete')
    require(events[0]['event'] == 'baseline_ready' and events[-1]['event'] == 'workload_end',
            'Unexpected log boundaries')
    roles = {'A', 'B'} if settings['test'] in ('mr', 'wfr') else {'A'}
    require({o['role'] for o in operations} == roles, 'Unexpected roles')
    counts, reasons, attempts, servers = Counter(), Counter(), Counter(), {}
    recoveries, gaps = [], {}
    for role in roles:
        rows = sorted((o for o in operations if o['role'] == role), key=lambda o:o['started_ns'])
        require([o['operation_id'] for o in rows] == [f'{role}-{i}' for i in range(1,len(rows)+1)],
                'Operation numbering gap or reordering')
        require(all(a['ended_ns'] <= b['started_ns'] for a,b in zip(rows,rows[1:])),
                'Concurrent operations in a single session')
        seq = [o['sequence'] for o in rows if o['kind'] == 'write']
        require(all(a < b for a,b in zip(seq,seq[1:])), 'Repeated/reversed write sequence')
        if role == 'A':
            require(seq == list(range(1,len(seq)+1)), 'A write sequence gap')
        gap = max(((b['started_ns']-a['ended_ns'])/1e6 for a,b in zip(rows,rows[1:])),default=0)
        gaps[role] = gap
        waiting = []
        for o in rows:
            if o['status'] == 'error':
                waiting.append(o['operation_id'])
            elif waiting:
                recoveries.append(dict(errors=waiting, next_success=brief(o)))
                waiting=[]
    offsets=[]
    for op in operations:
        require(op['status'] in ('ok','error') and not op.get('fatal'), 'Fatal/unknown status')
        require(op['ended_ns'] >= op['started_ns'], 'Negative operation duration')
        require(abs((op['ended_ns']-op['started_ns'])/1e6-op['elapsed_ms']) < 1e-6, 'Duration mismatch')
        require(abs((op['started_ns']-start['monotonic_ns'])/1e9-op['elapsed_s']) < 1e-6, 'Elapsed mismatch')
        require(0 <= op['elapsed_s'] < settings['duration']+.01, 'Operation began after deadline')
        counts[op['status']]+=1
        counts[f"{op['role']}_{op['kind']}_{op['status']}"]+=1
        starts=[c for c in op['commands'] if c['event']=='started']
        attempts[len(starts)]+=1
        require(len(starts)==1, 'Not exactly one command attempt')
        require(len(op['commands'])==2 and op['commands'][1]['event'] in ('succeeded','failed'),
                'Incomplete command trace')
        require(op['commands'][1]['request']==starts[0]['request'], 'Command request mismatch')
        require(op['started_ns'] <= starts[0]['monotonic_ns'] <= op['ended_ns'], 'Dispatch outside operation')
        expected_cmd = ('find' if op['kind']=='read' else 'findAndModify'
                        if settings['test']=='mw' or (settings['test']=='wfr' and op['role']=='B') else 'update')
        require(starts[0]['command']==expected_cmd, 'Unexpected database command')
        if op['status']=='ok':
            require(op['commands'][1]['event']=='succeeded', 'Successful op with failed command')
            value=op['value']
            if starts[0]['command']=='update':
                require(value['acknowledged'] and value['matched']==1, 'Unacknowledged/unmatched write')
            elif value is not None:
                require(value['_id']==folder.name and type(value['version']) is int and value['version']>=0,
                        'Unexpected test document')
            else:
                require(False, 'Missing document in successful operation')
        if 'write_to_read_gap_ms' in op:
            prev=by_id[f"{op['role']}-{int(op['operation_id'].split('-')[1])-1}"]
            require(abs(op['write_to_read_gap_ms']-(op['started_ns']-prev['ended_ns'])/1e6)<1e-6,
                    'Pair gap mismatch')
        key=f"{op['role']}_{op['kind']}_{op['status']}"
        servers.setdefault(key,Counter())[starts[0]['server']]+=1
        offsets.append((datetime.fromisoformat(op['time'])-datetime.fromisoformat(start['time'])).total_seconds()-op['elapsed_s'])
    expected = replay(settings['test'], operations)
    require(len(expected)==len(checks), 'Check count mismatch')
    matched=set()
    violations=[]
    phases={name:Counter() for name in ('before_30','30_to_60','after_60')}
    for check in checks:
        key=tuple(check['operations'])
        require(key in expected and key not in matched, 'Unknown/duplicate check edge')
        matched.add(key)
        require(all(check[k]==v for k,v in expected[key].items()), 'Incorrect check result')
        require(check['role']==by_id[key[-1]]['role'], 'Check role mismatch')
        counts[check['status']]+=1
        if check.get('reason'): reasons[check['reason']]+=1
        op=by_id[key[-1]]
        phases['before_30' if op['elapsed_s']<30 else '30_to_60' if op['elapsed_s']<60 else 'after_60'][check['status']]+=1
        if check['status']=='violation':
            violations.append(dict(required=check['required'],observed=check['observed'],
                                   deficit=check['required']-check['observed'],
                                   operations=list(key),operation=brief(op)))
        if check.get('reason','') and check['reason'].startswith('Deadline'):
            require(op['elapsed_s']+op['elapsed_ms']/1000 >= settings['duration'], 'Premature deadline check')
    require(counts==Counter(summary['counts']), 'Summary counters differ from raw log')
    require(reasons==Counter(summary['inconclusive_reasons']), 'Reason counts differ')
    result=('VIOLATION_OBSERVED' if counts['violation'] else
            'UNAVAILABLE_OR_INCONCLUSIVE' if counts['error'] or counts['inconclusive'] or not counts['pass']
            else 'NO_VIOLATION_OBSERVED')
    require(result==summary['result'], 'Verdict mismatch')
    begin,end=control['workload_start'],control['workload_end']
    wall=(datetime.fromisoformat(end['time'])-datetime.fromisoformat(begin['time'])).total_seconds()
    monotonic=end['elapsed_s']-begin['elapsed_s']
    require(end['elapsed_s']>=settings['duration'], 'Duration not completed')
    reminders=[e for e in events if e['event']=='reminder']
    require(len(reminders)==(0 if settings['scenario']=='baseline' else 2), 'Reminder count mismatch')
    sources={f.name:digest(f) for f in sorted((folder/'sources').iterdir())}
    require(set(sources)=={'run.py','mark.py','config.py','cluster_config.py','pyproject.toml','uv.lock'},
            'Missing/unexpected source snapshots')
    tree=ast.parse((folder/'sources'/'run.py').read_text())
    worker=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='workload')
    require(not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and
                    n.func.attr in ('sleep','wait') for n in ast.walk(worker)),
            'Deliberate worker sleep/wait found')
    success_writes=sorted((o for o in operations if o['kind']=='write' and o['status']=='ok'),
                         key=lambda o:o['started_ns'])
    transitions=[]
    for op in success_writes:
        item=brief(op)
        if not transitions or item['server']!=transitions[-1]['server']:
            transitions.append(item)
    adjacent=0
    last=None
    for op in sorted((o for o in operations if o['kind']=='read' and o['status']=='ok'),key=lambda o:o['started_ns']):
        if last is not None and seen(op)<last: adjacent+=1
        last=seen(op)
    return dict(run_id=folder.name,settings=settings,summary=summary,integrity='PASS',
                started_utc=begin['time'],ended_utc=end['time'],
                initial_primary=control['baseline_ready']['primary'], counts=dict(counts),
                reasons=dict(reasons),operations=len(operations),checks=len(checks),
                attempts=dict(attempts),servers=servers,write_transitions=transitions,
                errors=[brief(o) for o in operations if o['status']=='error'],recoveries=recoveries,
                first_violation=violations[0] if violations else None,
                largest_violation=max(violations,key=lambda v:v['deficit']) if violations else None,
                adjacent_read_drops=adjacent,nominal_phases=phases,
                wall_duration_s=wall,monotonic_duration_s=monotonic,
                wall_monotonic_offset_range_s=max(offsets)-min(offsets),max_interoperation_gap_ms=gaps,
                longest_operation=brief(max(operations,key=lambda o:o['elapsed_ms'])),
                longest_success=brief(max((o for o in operations if o['status']=='ok'),key=lambda o:o['elapsed_ms'])),
                reminders=reminders,markers_present=(folder/'markers.jsonl').exists(),
                source_hashes=sources,
                evidence_hashes={name:digest(folder/name) for name in
                                 ('events.jsonl','settings.json','summary.json','start.json')})


def self_check():
    # A regression after a read error must retain the earlier high-water mark.
    def read(n,status,value):
        return dict(role='B',operation_id=f'B-{n}',started_ns=n,kind='read',status=status,
                    value={'version':value})
    result=replay('mr',[read(1,'ok',10),read(2,'error',0),read(3,'ok',9)])
    require(result[('B-3',)]['status']=='violation' and result[('B-3',)]['required']==10,
            'Auditor self-check failed')
    for test,role,kinds in [('ryw','A',('write','read')),('wfr','B',('read','write'))]:
        rows=[dict(role=role,operation_id=f'{role}-1',started_ns=1,kind=kinds[0],status='ok',sequence=10,value={'version':10}),
              dict(role=role,operation_id=f'{role}-2',started_ns=2,kind=kinds[1],status='ok',dependency=10,value={'version':9})]
        require(next(iter(replay(test,rows).values()))['status']=='violation','Missed stale dependency')
    rows=[dict(role='A',operation_id=f'A-{n}',started_ns=n,kind='write',sequence=n,status=status,value={'version':v})
          for n,status,v in [(1,'ok',0),(2,'error',0),(3,'ok',0),(4,'ok',3)]]
    result=replay('mw',rows)
    require([v['status'] for v in result.values()]==['inconclusive','inconclusive','pass'],
            'Uncertain-write handling self-check failed')


if __name__=='__main__':
    self_check()
    reports=[]
    failures=[]
    for folder in sorted((ROOT/'results'/'continuous').iterdir()):
        if not folder.is_dir():
            continue
        try:
            reports.append(audit(folder))
            print('PASS',folder.name,flush=True)
        except Exception as exc:
            failures.append(dict(run_id=folder.name,error=str(exc)))
            print('FAIL',folder.name,str(exc),flush=True)
    expected={(test,config,'off' if config=='C3' else 'on',scenario)
              for test in ('mr','mw','ryw','wfr') for config in ('C1','C2','C3','C4')
              for scenario in ('baseline','secondary')}
    expected.update({('mw','C1','on','primary'),('mw','C3','off','primary'),
                     ('mw','C1','on','majority-loss'),('ryw','C2','off','baseline'),
                     ('ryw','C2','off','secondary'),('ryw','C2','off','primary'),
                     ('ryw','C3','off','primary'),('wfr','C3','off','primary'),
                     ('wfr','C1','on','primary')})
    actual=[tuple(r['settings'][k] for k in ('test','config','causal','scenario')) for r in reports]
    chronology=sorted(reports,key=lambda r:r['started_utc'])
    global_checks=dict(expected_combinations=len(expected),actual_combinations=len(set(actual)),
                       missing_combinations=sorted(expected-set(actual)),
                       unexpected_combinations=sorted(set(actual)-expected),
                       duplicate_combinations=[k for k,v in Counter(actual).items() if v>1],
                       identical_source_snapshots=len({json.dumps(r['source_hashes'],sort_keys=True) for r in reports})==1,
                       no_overlapping_runs=all(a['ended_utc']<b['started_utc'] for a,b in zip(chronology,chronology[1:])))
    for key in ('missing_combinations','unexpected_combinations','duplicate_combinations'):
        if global_checks[key]:
            failures.append(dict(run_id='dataset',error=key,details=global_checks[key]))
    for key in ('identical_source_snapshots','no_overlapping_runs'):
        if not global_checks[key]:
            failures.append(dict(run_id='dataset',error=key))
    output=dict(scope='Offline log integrity and bounded consistency replay; physical fault timing is not certified',
                global_checks=global_checks,runs=reports,failures=failures)
    (ROOT/'CONTINUOUS_TRIAL_AUDIT.json').write_text(json.dumps(output,indent=2)+'\n')
    print(f'{len(reports)} passed; {len(failures)} failed')
    raise SystemExit(bool(failures))
