"""MW: A completes A1/version 10, then writes A2/version 11 in one session.
B is not required: A2 atomically records the version present BEFORE applying A2.
Check that this server-computed prior_version is 10. A final version of 11 alone
would not prove the dependency. This is a bounded single-document check, not
a proof of every possible monotonic-write history or long-term durability.
"""
from common import run, start_session


def test(e):
    if e.args.role != 'A':
        raise ValueError('MW uses only Client A; witness replaces optional B observer')
    e.setup()
    with e.session() as session:
        e.write(session, {'$set': {'version': 10, 'stock': 90}}, 'A_write_A1')
        e.pause()
        # Pipeline RHS reads pre-update fields; no dependency in the filter,
        # no transaction and no client-side conditional that would enforce it.
        e.write(session, [{'$set': {'prior_version': '$version',
                 'witness_label': 'A2', 'version': 11, 'stock': 89}}], 'A_write_A2')
    return e.verdict(e.witness('A2') == 10, 'A2 must have been applied after A1/version 10')


if __name__ == '__main__':
    raise SystemExit(run('mw', test))
