# Test the connected three-laptop cluster

These scripts use the shared `rs-linked` replica set: nine MongoDB nodes, three
on each laptop. They do not start Docker, connect the dashboards, or use the
separate local lab.

## Before running

1. Follow [the connection guide](../setup_connection.md) on all three laptops.
2. Wait until the dashboards show **Connected · Shared cluster**, with one
   primary and eight secondaries. Keep all laptops awake and connected.
3. Choose **one laptop** to run the tests. Open a new terminal in the project
   folder containing `pyproject.toml`; leave the dashboard terminal running.
4. Run only one trial or batch across the team at a time. Avoid manually changing
   `consistency_lab.products` during a trial.

The scripts reuse `scripts/cluster_config.py` and the hosts-file mappings you
already configured. No IP edits, passwords, extra client containers, or separate
Python dependencies are needed. Keep `Script_testing` inside this project.

The test document `consistency_lab.products/P001` is created automatically and
**replaced at the start of each trial**. Other documents are left alone. Setup
waits for all nine acknowledgements and checks baseline read visibility on every
member, including the two non-voting members. Restore all nodes before each trial.
Measured operations still use the selected C1–C4 concerns.

## First run (Mac Terminal or Windows PowerShell)

Run these commands one at a time from the project folder:

```sh
uv run --locked Script_testing/common.py --test ryw --config C1 --causal on --count 1
uv run --locked Script_testing/common.py --test mr --config C1 --causal on --count 1
uv run --locked Script_testing/common.py --test mw --config C1 --causal on --count 1
uv run --locked Script_testing/common.py --test wfr --config C1 --causal on --count 1
```

| Test | Meaning | Client processes launched |
| --- | --- | --- |
| `ryw` | Read your writes | A |
| `mr` | Monotonic reads | A and B |
| `mw` | Monotonic writes | A |
| `wfr` | Writes follow reads | A and B |

Client A/B are logical test roles, unrelated to physical laptop A/B/C. The batch
runner launches both roles on the chosen laptop, each with its own MongoDB client
and session. The database remains distributed across all three laptops. Reads
use secondary preference; they are not pinned to a particular laptop.

Both roles must share one filesystem directory for coordination. Running one
role on each of two laptops with separate `results` folders will not work.

After the first runs succeed, increase `--count`, for example:

```sh
uv run --locked Script_testing/common.py --test mr --config C3 --causal off --count 100 --reads 100 --read-interval 0.01
```

| Profile | Read concern | Write concern |
| --- | --- | --- |
| C1 | majority | majority |
| C2 | majority | 1 |
| C3 | local | 1 |
| C4 | local | majority |

Pass `--config` and `--causal` explicitly when comparing results. For compatibility
with the supplied scripts, the batch defaults remain C3 and causal off;
individual scripts default to C1 and causal off. Causal sessions are separate
per role; filesystem signals do not transfer causal tokens.

## Results and recovery

Each batch prints its folder under `results/`, containing `summary.json`,
`trials.csv`, per-role terminal logs, `raw/` JSONL logs, and source snapshots.

- `NO_VIOLATION_OBSERVED`: this sampled history passed; it does not prove a guarantee.
- `VIOLATION_OBSERVED`: the test observed a violation of its checked property.
- `UNAVAILABLE_OR_INCONCLUSIVE`: a connection, timeout, or setup problem prevented
  evaluation; inspect the role logs. An uncertain write is not counted as a violation.

The batch's violation rate excludes inconclusive trials. Batch exit code 0 means
the batch completed, even if some trials observed violations or transient errors;
read its summary. Exit code 2 means it stopped on a fatal launch/script problem.
Each trial has a 270-second process deadline; very large read counts or intervals
can exceed it. Ctrl+C stops the runner and cleans up its child processes.

`results/.batch.lock` prevents overlapping batches on the same laptop only.
After a forced terminal shutdown, if the lock remains, first confirm that all
test processes have stopped before deleting it. All laptops must still coordinate
who runs the next batch. A timeout may leave a database write with an uncertain
outcome; restore the cluster and allow it to settle before retrying.

## Manual fault checkpoints

Use individual scripts for interactive `--pause` checkpoints; the batch runner
is noninteractive. For example, run a single RYW trial:

```sh
uv run --locked Script_testing/test_read_your_writes.py --config C3 --causal off --role A --run-id ryw_fault_001 --pause
```

At the checkpoint, the owner of the chosen node can inject a fault with the
existing `faults/control.py ... --linked` controls, then you press Enter to resume.
Each owner can control only their own nodes. Afterwards, affected owners run:

```sh
uv run --locked faults/control.py restore --linked
```

Wait for all nine members to recover before the next trial. Do not use Stop Lab
to inject a fault: it deletes the lab's database volumes.

For MR or WFR, start the same individual script in two terminals **on the same
laptop**, with `--role A` and `--role B`, the same profile, causal setting, and
unique run ID. Put `--pause` on role B. Its coordination timeout defaults to 180
seconds; increase `--coord-timeout` on both roles if you need longer. Use a new
run ID for every trial; existing claim files intentionally prevent reuse.

## Offline check

```sh
uv run --locked Script_testing/check_runner.py
```

This checks setup, nine-member discovery, file coordination, result classification,
and process timeout cleanup without connecting to MongoDB. A real three-laptop
run is still needed to validate the hotspot, firewall, and live replication.
