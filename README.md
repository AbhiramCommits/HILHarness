# HILHarness

A Hardware-in-the-Loop (HIL) test harness and validation framework for an
embedded Linux infotainment/ECU-style target. On every commit it flashes a
firmware/rootfs image to a device-under-test (DUT), power-cycles it through a
relay, and asserts a clean bootchain and driver bring-up — plus CAN, UART and
TCP/IP coverage — then produces a release-readiness report with explicit
pass/fail gates.

Everything in this repository runs with **zero physical hardware**: a simulated
target process, a virtual CAN bus and a PTY serial line stand in for the real
board. The drivers sit behind interfaces, so moving to a Raspberry Pi + GPIO
relay + `can0` + `/dev/ttyUSB0` is a config swap (`configs/rig_hw_raspi.yaml`),
not a rewrite.

Every number in the [Results](#4-results) section below was produced by a command
actually run during this build (see the provenance note next to each figure).

---

## 1. What it is

A HIL release gate:

- **Flash** an image (sha256-verified) and stage it into the DUT sandbox.
- **Power-cycle** the DUT through a relay (`sim` process kill/start, or a real
  GPIO relay).
- **Validate the bootchain** from the console transcript: stage timings,
  per-driver probe latency, missing/failed/hung drivers, and `FAIL|timeout|oops|panic`
  lines. Parsing is done by a compiled C `bootprobe` with a pure-Python fallback.
- **Generate + decode CAN** over SocketCAN (vcan when available, otherwise
  python-can `virtual`), assert DBC signals, measure frame loss from a rolling
  counter, and detect five injected fault classes.
- **Control the DUT** over serial/UART and Ethernet/TCP-IP, with headless pytest
  suites.
- **Collect metrics** (boot time, driver latencies, CAN loss, CPU/RSS under
  load), persist them in SQLite, detect regressions against a stored baseline,
  and expose them to Grafana through a Prometheus exporter.
- **Gate releases** with a GO / NO-GO verdict written to
  `reports/release_readiness.md` and `.json`.

## 2. Architecture

```
                     ┌──────────────────────────── GitHub Actions CI ───────────────────────────┐
                     │  lint+typecheck · build native · pytest HIL suites · soak + gates       │
                     └───────────────────────────────────┬────────────────────────────────────┘
                                                         │  hil run / hil soak / hil report
                                                         ▼
   ┌──────────────────────────────────────────────── HILHarness (hilharness/) ─────────────────┐
   │  config.py (pydantic RigConfig ← YAML)                                                     │
   │                                                                                            │
   │  drivers/            power.py   console.py   canbus.py   netctl.py   flasher.py            │
   │    │  interfaces       │           │            │           │            │                  │
   │    ├─ sim impl ────────┴───────────┴────────────┴───────────┴────────────┤                  │
   │    └─ hw impl  (GpioRelay / serial / can0 / TCP / dd|fastboot)            │                  │
   │                                                                           │                  │
   │  validators/  boot.py (C bootprobe/py fallback)   can.py (cantools + faults)                 │
   │  metrics/     store.py (SQLite)   collect.py (psutil)   prom.py (textfile)                   │
   │  report/      release.py (gates + GO/NO-GO)   regress.py (baseline deltas)                   │
   └───────────────┬───────────────────────────────────────────────────────────┬───────────────┘
                   │ PTY stdout/stdin            TCP line protocol :9000        │  scrape / push
                   ▼                                        ▼                    ▼
        ┌───────────────────────┐              ┌──────────────────────┐   ┌────────────────────┐
        │  DUT                  │              │  native/             │   │ Prometheus +       │
        │  sim: simtarget/*     │              │   bootprobe.c (any OS)│   │ Pushgateway +      │
        │  hw:  real board      │              │   can_node.c (Linux) │   │ Grafana            │
        └───────────────────────┘              └──────────────────────┘   └────────────────────┘
                   │                                   │
                   └──────── relay (sim kill/start | GPIO) ────────────────┘
```

Driver-interface swap:

| Capability | Simulated (default) | Real hardware |
|---|---|---|
| Power | `SimRelay` starts/kills `simtarget.target` | `GpioRelay` drives a relay on BCM GPIO |
| Console | PTY master (target writes to the slave) | `pyserial` on `/dev/ttyUSB*` |
| CAN | python-can `virtual` bus + `simtarget/can_node.py` | SocketCAN `can0`/`vcan0` + `native/can_node.c` |
| Network | in-process TCP control server in the target | control daemon on the DUT |
| Flash | sha256 verify + copy into `results/dut_root/` | `HwFlasher` shells out to `dd`/`fastboot` |

The C components: `native/bootprobe.c` is portable and runs wherever the harness
runs; `native/can_node.c` binds SocketCAN and only builds on Linux (elsewhere it
compiles to a stub that points you at the Python node).

## 3. How to run

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make native                 # builds native/bootprobe (+ can_node on Linux)
bash scripts/setup_vcan.sh  # non-fatal: vcan if Linux supports it, else virtual bus

hil power-cycle  --config configs/rig_sim.yaml   # off/on a cycle
hil flash        --config configs/rig_sim.yaml   # sha256-verified flash
hil boot --cycles 10 --config configs/rig_sim.yaml
hil can-test --duration 30 --config configs/rig_sim.yaml
hil run --suite all --config configs/rig_sim.yaml     # junit.xml + results JSON
hil soak --cycles 50 --config configs/rig_sim.yaml    # full cycles, persists metrics
hil report --config configs/rig_sim.yaml              # release gates, exit≠0 on fail
hil baseline-update                                    # store current metrics as baseline
hil prom --pushgateway http://localhost:9091           # export/push gauges
docker compose up -d                                   # Prometheus :9090 + Grafana :3000
```

`make lint test` runs ruff + black + mypy + the HIL pytest suites locally;
`make cov` enforces the 80% coverage gate. `make native soak report dash` wrap
the individual steps.

### Hardware bring-up (`configs/rig_hw_raspi.yaml`)

1. **Relay** — wire a relay/opto board to BCM GPIO17 (pin 11). `power.gpio_pin: 17`,
   `active_high: false` for an active-low opto-isolated board. `SimRelay` is
   swapped for `GpioRelay` automatically when `power.driver: gpio`.
2. **Console** — a CP210x/FTDI USB-UART appears as `/dev/ttyUSB0` at 115200 8N1;
   set `console.driver: serial`, `console.device: /dev/ttyUSB0`.
3. **CAN** — bring up `can0` at 500 kbit/s:
   `sudo ip link set can0 up type can bitrate 500000`, then `can.interface: can0`.
   The harness prefers SocketCAN and drives `native/can_node.c`.
4. **Ethernet** — point `net.host`/`net.port` at the DUT control daemon.
5. **Flash** — `hil flash --config configs/rig_hw_raspi.yaml --hw-command "dd if={image} of=/dev/sdX bs=4M conv=fsync"`.

Then run the identical `hil run` / `hil soak` / `hil report` commands.

## 4. Results

All figures below come from this build (`reports/release_readiness.md`,
`reports/release_readiness.json`, `results/soak_summary.json`,
`results/hil_results.json`, and the CLI output of `hil can-test`). Report run ID
`2`, generated `2026-10-12`.

| Metric | Measured | Gate | Result | Source command |
|---|---:|---|:--:|---|
| Power cycles run (soak) | 50 | — | — | `hil soak --cycles 50` |
| Clean-boot rate (non-fault cycles) | 100.0% | ≥ 100% | PASS | `hil soak` / `hil report` |
| Clean boots (all cycles incl. injected faults) | 46/50 (92.0%) | — | — | `hil soak` |
| Boot time p50 | 5267.8 ms | — | — | `hil soak` |
| Boot time p95 | 5363.3 ms | ≤ 15000 ms | PASS | `hil report` |
| Boot time max | 7647.0 ms¹ | — | — | `hil report` |
| Driver init p95 (all drivers) | 549.4 ms | ≤ 1500 ms | PASS | `hil report` |
| CAN frames rx / tx (30 s clean) | 3102 / 3102 | — | — | `hil can-test --duration 30` |
| CAN frame loss (clean) | 0.000% | ≤ 1% | PASS | `hil can-test` |
| CAN frame loss (drop-rate 0.2 demo) | 18.849% | — | detected | `hil can-test --drop-rate 0.2` |
| CAN period mean / jitter (30 s) | 20.81 ms / ±77.9 ms max² | — | — | `hil can-test` |
| CAN period jitter max (soak cycles) | 26.98 ms | — | — | `hil soak` |
| Fault-injection detection (soak, 10 injected) | 10/10 (100%) | ≥ 80% | PASS | `hil soak` |
| CAN fault classes detected (unit tests) | 5/5 (100%) | — | — | `pytest tests/test_can_traffic.py` |
| Target CPU peak under load | 153.0% | info | — | `hil soak` |
| Target RSS peak under load | 50.23 MiB | info | — | `hil soak` |
| Suite test count / pass rate | 75 / 100.0% | ≥ 95% | PASS | `hil run --suite all` |
| Coverage (`hilharness`) | 84.58% | ≥ 80% | PASS | `make cov` |
| Soak wall clock (50 cycles) | 195.2 s | — | — | `hil soak` |
| **Verdict** | **GO** | | **PASS** | `hil report` (exit 0) |

¹ The 7647 ms max is the deliberately injected `slow_boot` fault cycle (it is
slow but otherwise healthy, so it counts as a clean boot). Healthy cycles top
out at 5375.6 ms.
² The `±77.9 ms` is a single scheduler/GC outlier in the 30 s stream; the mean
period is 20.81 ms and the per-cycle max jitter across the 50-cycle soak is
26.98 ms.

Per-driver probe latency (p95 over 40 clean cycles, from
`reports/release_readiness.md`):

| Driver | p50 (ms) | p95 (ms) | max (ms) |
|---|---:|---:|---:|
| display | 139.0 | 145.4 | 145.6 |
| usb | 94.3 | 98.2 | 98.7 |
| audio | 208.1 | 216.7 | 217.4 |
| bt | 331.9 | 342.4 | 342.9 |
| camera | 410.1 | 424.4 | 426.0 |
| wifi | 538.6 | 560.7 | 561.3 |

Gate thresholds vs measured (from the report):

| Gate | Threshold | Measured | Result |
|---|---:|---:|:--:|
| Boot time p95 | ≤ 15000 ms | 5363.30 ms | PASS |
| Driver init p95 | ≤ 1500 ms | 549.36 ms | PASS |
| CAN frame loss (max) | ≤ 1 % | 0.00 % | PASS |
| Fault detection rate | ≥ 80 % | 100.00 % | PASS |
| Suite pass rate | ≥ 95 % | 100.00 % | PASS |
| Clean boot rate | ≥ 100 % | 100.00 % | PASS |

An intentionally injected baseline regression (e.g. setting the archived
`boot_ms_p95` to 1000 ms) drives `hil report` to `NO-GO` and exit code 1 — this
is exercised by `tests/test_report_gates.py::test_report_regression_makes_nogo`.

## 5. Gates, regression policy, limitations, next steps

**Gates.** Four thresholds live in `configs/rig_*.yaml` under `gates:`
`max_boot_ms`, `max_driver_init_ms`, `max_frame_loss_pct`,
`min_fault_detection_pct`, plus fixed gates on suite pass rate (≥95%) and clean
boot rate (100% over non-fault cycles). Any failing gate makes `hil report` exit
1; CI surfaces the report as the job summary and fails the job.

**Regression policy.** `hil baseline-update` stores the current metrics in
`reports/baseline.json`. `hil report` flags any metric worse than baseline by
more than 10% (configurable via `--regression-pct`). Boot, driver, CAN-loss,
fault-detection, suite and clean-boot metrics participate; the host-dependent
CPU/RSS metrics are reported but excluded from regression checks so CI on a
different machine does not produce spurious NO-GOs.

**Limitations.**

- The default target is a **simulated process**, not real silicon. Timings are
  driven by a configurable profile with jitter (and injectable faults), so they
  exercise the whole pipeline but are not a substitute for board-level
  characterisation.
- CAN runs on python-can's `virtual` bus unless SocketCAN is available; the
  virtual bus is lossless, so non-zero loss is only seen under injected faults.
- CPU/RSS are measured with `psutil` against the target process and are
  machine-dependent.
- The C `can_node` is Linux-only (SocketCAN); it is not compiled on macOS.

**Next steps.** Add a real Raspberry Pi runner, promote the inline Grafana alert
rules to provisioned unified alerts, add power-rail current measurement, and
wire hardware-in-the-loop runs into the release pipeline via a self-hosted
runner.

## Repository layout

```
hilharness/        harness package (config, drivers, validators, metrics, report, cli, rig, soak)
simtarget/         simulated DUT process, boot-log generator, Python CAN node
native/            bootprobe.c, can_node.c, Makefile
configs/           rig_sim.yaml, rig_hw_raspi.yaml, infotainment.dbc, rootfs.img
tests/             unit tests + suite_boot/ suite_can/ suite_io/ (markers: hil, sim, hw)
dashboards/        Grafana dashboard JSON, Prometheus config, provisioning
reports/           release_readiness.md/.json, baseline.json (committed)
results/           runtime artifacts (git-ignored, except committed reports)
scripts/           setup_vcan.sh, soak.py, make_image.py
```

## License

MIT — see [LICENSE](LICENSE).
