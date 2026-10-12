# Runbook: triaging a failed HIL gate

When a gate fails, `hil report` prints the offending metric and writes a `NO-GO`
verdict to `reports/release_readiness.md`. CI fails the job and attaches the
report, `results/junit.xml` and `results/hil_metrics.prom` as artifacts.

## 1. See what failed

```bash
hil report --config configs/rig_sim.yaml     # exit code 1, table + verdict
cat reports/release_readiness.md
```

The **Gates** table shows measured vs threshold. The **Regression check** table
shows metrics worse than `reports/baseline.json` by more than 10%. The
`junit.xml` lists the failing pytest cases.

## 2. Pull the right artifact per gate

| Failing gate | Look at |
|---|---|
| `boot_ms_p95` / driver init | console transcript `results/transcripts/cycle_XXXX.log` for the offending cycle |
| `clean_boot_rate` / missing or failed drivers | the transcript's `hil-driver` lines and `bootprobe` output |
| `can_frame_loss_pct` | the CAN stream stats and findings (`hil can-test`) |
| `fault_detection_pct` | soak `can_fault_detected` / `boot_fault_detected` measurements |
| `suite_pass_rate` | `results/junit.xml` failing test names + attached transcript |
| regression | `reports/baseline.json` vs current values |

Transcripts, CAN logs and the SQLite store live under `results/`:

```bash
ls results/transcripts/
sqlite3 results/hil.db "select metric,count(*),round(max(value),2) from measurements \
  where run_id=(select max(run_id) from runs) group by metric;"
```

## 3. Reproduce a single cycle

```bash
# one boot, printing the transcript path
RIG=configs/rig_sim.yaml
hil flash --config $RIG
hil power-cycle --config $RIG
hil boot --cycles 1 --config $RIG

# inject the suspect fault to prove detection
hil boot --cycles 1 --config $RIG --faults "fail:audio"
hil can-test --duration 10 --config $RIG --drop-rate 0.2
```

The transcript for a cycle is attached to any failing test report automatically
(the `pytest_runtest_makereport` hook in `tests/conftest.py`).

## 4. Decide

- **Transient** (scheduler/GC spike, port contended): re-run
  `hil soak --cycles 50` and confirm `hil report` returns `GO`.
- **Real regression**: fix the DUT/firmware, then refresh the baseline when the
  new numbers are the intended steady state:
  ```bash
  hil soak --cycles 50 && hil baseline-update && hil report
  ```
- **Gate too tight for the environment**: adjust the thresholds in
  `configs/rig_*.yaml` (documented in the PR) rather than weakening the check.

## 5. Grafana

```bash
docker compose up -d
hil prom --pushgateway http://localhost:9091
# Grafana http://localhost:3000 -> HIL -> HIL Overview
```

Panels for boot time (p50/p95), driver init latency, CAN loss, CPU/RSS, suite
pass rate and fault detection make trends obvious; inline alerts fire at
`boot_ms_p95 > 15000` and `can_frame_loss_pct > 1`.
