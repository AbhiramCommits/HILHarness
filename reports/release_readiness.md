# HIL Release-Readiness Report

- Generated: 2026-10-12T20:46:48+00:00
- Rig: `infotainment-sim` (target `sim`)
- Run ID: 2
- Verdict: **GO**

## Gates

| Metric | Measured | Gate | Result | Baseline | Delta |
|---|---:|---:|:--:|---:|---:|
| Boot time p95 | 5363.30 ms | <= 15000 ms | PASS | 5352.07 | +0.2% |
| Driver init p95 | 549.36 ms | <= 1500 ms | PASS | 546.45 | +0.5% |
| CAN frame loss (max) | 0.00 % | <= 1 % | PASS | 0.00 | - |
| Fault detection rate | 100.00 % | >= 80 % | PASS | 100.00 | +0.0% |
| Target CPU peak | 153.00 % | informational | - | 173.90 | -12.0% |
| Target RSS peak | 50.23 MiB | informational | - | 50.19 | +0.1% |
| Suite pass rate | 100.00 % | >= 95 % | PASS | 100.00 | +0.0% |
| Clean boot rate | 100.00 % | >= 100 % | PASS | 100.00 | +0.0% |

### Regression check (threshold 10%)

No regressions detected against the baseline.

## Soak summary

- Cycles: 50
- Clean boots: 46 (92.0%)
- Boot p50/p95/max: 5267.8 / 5363.3 / 7647.0 ms

## Driver init latency (p95)

| Driver | p50 ms | p95 ms | max ms | n |
|---|---:|---:|---:|---:|
| audio | 208.1 | 216.7 | 217.4 | 40 |
| bt | 331.9 | 342.4 | 342.9 | 40 |
| camera | 410.1 | 424.4 | 426.0 | 40 |
| display | 139.0 | 145.4 | 145.6 | 40 |
| usb | 94.3 | 98.2 | 98.7 | 40 |
| wifi | 538.6 | 560.7 | 561.3 | 40 |

## Verdict

**GO**
