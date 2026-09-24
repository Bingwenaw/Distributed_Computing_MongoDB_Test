"""WFR: A writes A1/version 10. B reads it, then writes B1/version 11.
B's read and write reuse B's session; A and B do NOT share sessions or causal
tokens. Check B1 atomically observed version 10 at its application point.
The client-supplied dependency describes intent; prior_version is server evidence.
"""
from common import run, start_session


def test(e):
    with e.session() as session:
        if e.args.role == 'A':
            e.setup()
            e.write(session, {'$set': {'version': 10, 'stock': 90}}, 'A_write_A1')
            e.signal('a1')
            e.log('role_complete', note='Use Client B verdict for WFR')
            return 0
        e.wait('a1')
        seen = e.read_a1(session)
        e.pause()  # Inject failure AFTER B reads A1, BEFORE B writes B1.
        e.write(session, [{'$set': {'prior_version': '$version',
                 'read_dependency': seen['version'], 'witness_label': 'B1',
                 'version': 11, 'stock': seen['stock']-1}}], 'B_write_B1')
    return e.verdict(e.witness('B1') == seen['version'], 'B1 must be applied to the state B read (version 10)')


if __name__ == '__main__':
    raise SystemExit(run('wfr', test))
