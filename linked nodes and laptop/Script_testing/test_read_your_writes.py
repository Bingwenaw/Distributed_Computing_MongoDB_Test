"""First-read RYW, with no logging I/O between write and read."""
import time
from common import run, Inconclusive

def test(e):
    if e.args.role != 'A':
        raise ValueError('RYW uses A only')
    e.setup()
    e.warm_reads()
    with e.session() as session:
        begin = time.perf_counter_ns()
        result = e.col.update_one({'_id':'P001'}, {'$set':{'version':10,'stock':90}}, session=session)
        acknowledged = time.perf_counter_ns()
        if not result.acknowledged or result.matched_count != 1:
            raise Inconclusive('Measured write did not confirm one matched document')
        e.pause()
        read_start = time.perf_counter_ns()
        doc = e.col.find_one({'_id':'P001'}, session=session)
        read_end = time.perf_counter_ns()
        e.log('measured_pair', write_ms=(acknowledged-begin)/1e6,
              write_to_read_gap_ms=(read_start-acknowledged)/1e6,
              read_ms=(read_end-read_start)/1e6, document=doc)
        version=e.observed_version(doc)
        return e.verdict(version >= 10, 'First read must reflect acknowledged version 10 in this isolated trial')

if __name__ == '__main__':
    raise SystemExit(run('ryw', test))
