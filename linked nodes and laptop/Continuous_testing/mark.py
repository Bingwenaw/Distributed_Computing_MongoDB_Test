"""Record an operator-reported network action without touching the running workload."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder', type=Path, help='Results folder printed by run.py; use the same laptop')
    parser.add_argument('action', choices=['disconnected', 'restored', 'note'])
    parser.add_argument('--note', required=True, help='Laptop(s), observed primary, or other evidence')
    args = parser.parse_args(argv)
    if not args.note.strip():
        parser.error('Note must not be empty')
    try:
        start = json.loads((args.folder / 'start.json').read_text())
    except (OSError, ValueError) as exc:
        parser.error(f'Workload start record unavailable: {exc}')
    elapsed = (time.monotonic_ns() - start['monotonic_ns']) / 1e9
    row = dict(time=datetime.now(timezone.utc).isoformat(), elapsed_s=elapsed,
               action=args.action, note=args.note,
               source='Operator report recorded now; physical action may have occurred earlier')
    with (args.folder / 'markers.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row) + '\n')
    print(json.dumps(row))


if __name__ == '__main__':
    main()
