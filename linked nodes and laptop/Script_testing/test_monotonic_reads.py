"""MR: A writes 10 -> 11 -> 12; B reads repeatedly in B's own session.
Check B's observed versions never decrease. Skipping 11 is allowed.
Run both roles on one laptop with the same unique --run-id and results directory.
"""
import time
from common import run, start_session


def test(e):
    with e.session() as session:
        if e.args.role == 'A':
            e.setup()
            e.write(session, {'$set': {'version': 10, 'stock': 90}}, 'A_write_10')
            e.signal('a1')
            e.wait('b_read')
            for version in (11, 12):
                e.write(session, {'$set': {'version': version, 'stock': 100-version}}, f'A_write_{version}')
                time.sleep(.2)
            e.signal('writer_done')
            e.log('role_complete', note='Use Client B verdict for MR')
            return 0
        e.wait('a1')
        previous = e.observed_version(e.read_a1(session))
        e.pause()  # Failure between B's first and subsequent reads.
        e.signal('b_read')
        holds = not e.mr_violation
        for i in range(e.args.reads):
            current = e.observed_version(e.read(session, f'B_read_{i+2}'))
            if current < previous:
                holds = False
            previous = current
            time.sleep(e.args.read_interval)
        e.wait('writer_done')
        return e.verdict(holds, 'B versions must never decrease; freshness is not required')


if __name__ == '__main__':
    raise SystemExit(run('mr', test))
