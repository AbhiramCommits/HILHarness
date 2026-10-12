# HIL Release-Readiness Report

- Generated: 2026-10-12T20:33:17+00:00
- Rig: `infotainment-sim` (target `sim`)
- Run ID: 1
- Verdict: **GO**

## Gates

| Metric | Measured | Gate | Result | Baseline | Delta |
|---|---:|---:|:--:|---:|---:|
| Boot time p95 | 5352.07 ms | <= 15000 ms | PASS | 5352.07 | +0.0% |
| Driver init p95 | 546.45 ms | <= 1500 ms | PASS | 546.45 | +0.0% |
| CAN frame loss (max) | 0.00 % | <= 1 % | PASS | 0.00 | - |
| Fault detection rate | 100.00 % | >= 80 % | PASS | 100.00 | +0.0% |
| Target CPU peak | 173.90 % | informational | - | 173.90 | +0.0% |
| Target RSS peak | 50.19 MiB | informational | - | 50.19 | +0.0% |
| Suite pass rate | 100.00 % | >= 95 % | PASS | 100.00 | +0.0% |
| Clean boot rate | 100.00 % | >= 100 % | PASS | 100.00 | +0.0% |

### Regression check (threshold 10%)

No regressions detected against the baseline.

## Soak summary

- Cycles: 50
- Clean boots: 46 (92.0%)
- Boot p50/p95/max: 5245.7 / 5352.1 / 7547.3 ms

## Driver init latency (p95)

| Driver | p50 ms | p95 ms | max ms | n |
|---|---:|---:|---:|---:|
| audio | 210.7 | 216.2 | 217.3 | 40 |
| bt | 330.5 | 342.4 | 342.7 | 40 |
| camera | 410.2 | 425.8 | 426.3 | 40 |
| display | 140.6 | 145.0 | 145.6 | 40 |
| usb | 93.8 | 98.4 | 98.7 | 40 |
| wifi | 541.6 | 559.1 | 561.6 | 40 |

## Verdict

**GO**
