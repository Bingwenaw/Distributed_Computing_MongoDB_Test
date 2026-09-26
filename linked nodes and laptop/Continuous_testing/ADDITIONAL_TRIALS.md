# Additional exploratory trials

These six runs extend the completed 35-run matrix. Use the existing runner;
do not replace earlier data or modify the original scripts. Every invocation
creates a new results folder. Keep every outcome, including no violation,
errors, and incomplete checks. Do not repeat only until a violation appears.

| Order | Test | Config | Causal | Scenario | Purpose |
| --- | --- | --- | --- | --- | --- |
| E1 | RYW | C2 | off | baseline | Compare with the retained C2 causal-on baseline. |
| E2 | RYW | C2 | off | secondary | Compare with the retained C2 causal-on secondary partition. |
| E3 | RYW | C2 | off | primary | Observe write/read dependencies across primary disconnection. |
| E4 | RYW | C3 | off | primary | Extend the C3 baseline/secondary results to primary disconnection. |
| E5 | WFR | C3 | off | primary | Look for read dependencies absent at the subsequent write after failover. |
| E6 | WFR | C1 | on | primary | Strong-concern reference under the same primary-disconnection scenario. |

Use 120 seconds for every run. Partition reminders are at 30 seconds for
disconnection and 60 seconds for restoration. No workload pauses, manual
checkpoints, retries, or waits for a new primary are added.

Start E1 with all nodes connected and awake:

```sh
caffeinate -i uv run --locked Continuous_testing/run.py \
  --duration 120 \
  --test ryw --config C2 --causal off --scenario baseline
```

Review each run before starting the next. For E2, disconnect C only while
its members are secondaries. For E3–E6, the primary must be on B or C before
the run: disconnect that laptop only, leaving A and the other laptop connected.
Never disconnect runner A to simulate primary loss. The last completed
majority-loss run recovered on mongo2/A, so its primary placement must be
rechecked before the primary trials. Do not reconfigure or force an election
during measurement. Record initial primary placement and any timing deviations
in each review. Keep Docker running and restore all nodes between runs.

## Interpretation limits

- C2 RYW causal-off has not been tested in the completed matrix. Its results
  must remain separate from the earlier causal-on runs.
- Missing guarantees allow violations; they do not require a violation in a
  particular run. Primary disconnection may produce errors instead of a
  successful operation that violates a dependency.
- WFR already uses two independent clients/sessions on A. Its check compares
  the read version with the primary's atomic pre-image at the dependent write.
  An ordinary stale secondary read does not alone violate this check.
- WFR failure exposure depends on actually reading a dependency that the
  later serving primary lacks. Whole-laptop disconnection can make the old
  primary's data unreachable, so E5 may still show no violation. Merely adding
  duration or changing concern settings does not ensure this history occurs.
- The increasing numeric version is a bounded proxy, not full write-history
  provenance. After rollback, a later larger version can mask the absence of
  an earlier write. No-violation findings do not prove all dependencies were
  retained. A stronger history-aware checker and a separately designed fault
  protocol would be a distinct experiment, not an interpretation of these logs.
- E5 versus E6 changes both concerns and causal mode; it is a reference
  comparison, not isolation of either factor. Existing baselines are reused,
  with primary placement and elapsed time between runs documented as limits.
- Reminders are not measured physical fault times. Retain reported timing
  deviations and avoid treating unequal fault windows as identical.

The initial extension is one run per listed combination. Any subsequent
repetitions should use a fixed count chosen before examining their outcomes.
