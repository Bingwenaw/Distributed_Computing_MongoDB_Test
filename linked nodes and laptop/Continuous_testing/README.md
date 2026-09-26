# Continuous partition trials

These are new scripts. The existing `Script_testing` scripts and historical
results are unchanged. Run from the project folder, on the laptop that will
remain connected to the majority side (normally physical A).

## Run one trial

Restore all nine members first. Coordinate with the team so only one workload
runs at a time. Leave Docker and the dashboards running.

```sh
uv run --locked Continuous_testing/run.py --test mw --config C1 --causal on --scenario primary --duration 180
```

The runner sets up once, then automatically starts continuous operations. One
command starts both logical client roles for MR and WFR. Each role has its own
MongoClient and persistent session; neither sessions nor causal tokens are shared.
Both roles run as threads on the runner laptop. There is no Enter prompt.

- **0–30 seconds:** operations run with everything connected.
- **At the 30-second reminder:** disconnect the chosen laptop(s); operations continue.
- **At the 90-second reminder:** restore connectivity; operations continue.
- **At 180 seconds:** stop issuing new operations and finish any in-flight call.
  The final call may take up to the configured 20-second operation timeout.

There are no deliberate sleeps in the workload, application retries, or
post-fault waits for a primary. Database operations may block within their
normal timeouts. Reminders and the five-second progress display run separately.
The workload is sequential per role: a blocked call delays that role's next
operation. This measures that client's experience, not a constant arrival rate
or a throughput benchmark.

Use `--duration 300` for more time. Change reminder times with
`--disconnect-at 60 --restore-at 180`. These options only print reminders; they
never disconnect anything or pause/resume the workload. `--scenario baseline`
runs without partition reminders. The duration clock starts after setup and
client warmup, not when you launch the command.

## Choose the test and scenario

| Option | Values |
| --- | --- |
| `--test` | `ryw`, `mr`, `mw`, `wfr` |
| `--config` | `C1`, `C2`, `C3`, `C4` (same concerns as the original scripts) |
| `--causal` | `on` or `off`, explicitly selected |
| `--scenario` | `baseline`, `secondary`, `primary`, `majority-loss` |

The scenario is an operator-supplied label, not an automated topology check.
Confirm the topology before choosing which laptop to disconnect:

- **secondary:** disconnect C only when all its nodes are secondaries.
- **primary:** disconnect the laptop hosting the primary, keeping the runner
  connected to a voting majority. To reproduce trials 22–23, B must host the
  primary before the run. Do not disconnect the runner laptop.
- **majority-loss:** disconnect B and C, leaving the runner on A.
- **baseline:** leave all laptops connected for the whole run.

For continuity with the old trials use causal **on** for C1/C2/C4 and **off**
for C3. This changes two factors when comparing C3 against other profiles;
hold causal mode constant in additional runs if isolating concern effects.

## Record when you actually act

Reminders do not prove a partition occurred. In another terminal on the **same
runner laptop**, immediately after the action, use the printed results folder:

```sh
uv run --locked Continuous_testing/mark.py "results/continuous/PASTE_RUN_FOLDER" disconnected --note "B Wi-Fi off; primary was mongo4"
uv run --locked Continuous_testing/mark.py "results/continuous/PASTE_RUN_FOLDER" restored --note "B Wi-Fi on"
```

This records an operator report at command execution time, not the exact physical
cut time. Keep dashboard/topology evidence too. Marker commands do not interact
with the workload. Do not use **Stop Lab** to inject faults: it deletes volumes.

## What is checked

The runner inserts one document per run in `consistency_lab.continuous_trials`.
It does not reset or write `products/P001`. Setup uses all nine acknowledgements
and checks baseline visibility on each member before measurement. Setup polling
is outside the workload. The document is never reset during measurement.

| Test | Continuous operations and check |
| --- | --- |
| RYW | A writes a new version, then immediately reads once in the same session. The returned version must be at least the acknowledged write version. Pair logging happens after the read. |
| MR | A writes increasing versions continuously. B reads continuously, comparing against its highest earlier observed version, including across read errors. |
| MW | A writes increasing versions using `find_one_and_update`, returning the state immediately before the write. That prior version must retain the earlier acknowledged dependency. |
| WFR | A writes increasing versions. B reads, then writes its own follow marker using `find_one_and_update`. The pre-write version must be at least B's read dependency. B never overwrites A's counter. |

MW/WFR use the atomic pre-image returned by the write, avoiding a separate
diagnostic read after failover. This changes the measured write operation from
the old scripts; do not directly compare latency between old and new datasets.
These are bounded single-document dependency checks, not proofs of general
causal consistency, all-replica ordering, or long-term durability.

Automatic read/write retries remain disabled using the existing shared config.
A transient operation error is recorded and the workload moves to a **new**
operation; failed writes are never resubmitted. Write sequences are unique
within each role even after errors. A write error can mean the write applied
without its acknowledgement reaching the client. Therefore:

- RYW does not claim a dependency from a failed write.
- MR retains its earlier read high-water mark through errors.
- MW marks the check across an uncertain write inconclusive; a later successful
  write establishes the next known dependency without erasing that uncertainty.
- WFR records an unsuccessful dependent write as inconclusive.
- A pair cut short by the duration limit is retained as inconclusive.

Clients/sessions persist throughout. Fatal configuration/data/programming errors
stop the run; transient network, execution-timeout and write-concern errors do not.

## Results

Each run prints a unique folder under `results/continuous/` containing:

- `settings.json`: concerns, sessions, retries, timeouts, scenario and versions.
- `events.jsonl`: every measured operation's start/end, value/error, duration,
  command server/attempt trace, and each evaluated or inconclusive check.
- `start.json`, optional `markers.jsonl`: workload start and operator reports.
- `summary.json`: completed flag, operation/error/check counts and overall result.
- `sources/`: runner, marker, shared settings/inventory and dependency snapshots.

Records are flushed as operations/pairs finish. A paired RYW/WFR operation still
in progress has not yet been flushed; use Ctrl+C for a graceful stop rather than
killing the process. Command failure events capture driver attempts, while the
operation latency also includes selection/connection waiting.

`VIOLATION_OBSERVED` is sticky: later successful checks never erase it.
Otherwise any operation error, incomplete check, interrupted run, or absence of
evaluated checks yields `UNAVAILABLE_OR_INCONCLUSIVE`. Partial pass counts remain
available and must not be mistaken for complete availability. Deadline-truncated
pairs can make an otherwise healthy run inconclusive; inspect the reason counts
in `summary.json` and the event log. A clean sampled history gives `NO_VIOLATION_OBSERVED`.

Exit 0 means the duration completed (inspect the summary even if there were
errors); exit 1 means a violation was observed; exit 2 means setup/fatal failure
or interruption. Keep unsuccessful attempts. A partition label alone does not
validate actual fault timing, and no-violation observations do not prove a guarantee.

## Rerun list

Keep the old results as exploratory checkpoint-based observations. For the new
continuous protocol, rerun these historical scenarios with new run IDs:

| Original trials | Continuous replacement |
| --- | --- |
| 5 and 7–21 | All four tests × C1–C4, `--scenario secondary` (16 runs) |
| 6 | MW / C1 / causal on, `--scenario majority-loss` |
| 22 | MW / C1 / causal on, `--scenario primary` |
| 23 | MW / C3 / causal off, `--scenario primary` |

Prioritise 22–23, then MR (9, 10, 15, 19), then RYW (7, 8, 14, 18).
Run the revised workload without faults for controls, ideally for every test /
profile combination compared. Restore all nine nodes between runs. Repeat each
chosen scenario with the same planned timing; retain actual markers and every
outcome, not just successful runs.

## Offline check

```sh
uv run --locked Continuous_testing/check.py
```

This simulates stale reads, failed/uncertain writes, recovery, duration expiry,
and the threaded controller without a MongoDB connection. Live three-laptop
partition behaviour must still be validated in your lab.
