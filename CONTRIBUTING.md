# Contributing

## Local test loop

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
make native              # build native/bootprobe (+ can_node on Linux)
make lint test           # ruff + black + mypy, then the HIL suites
make cov                 # same, with the 80% coverage gate enforced
```

`make lint test` is the single local entrypoint and mirrors CI. Useful extras:

```bash
pytest -m "hil and sim" -q                     # HIL suites only
pytest tests/suite_boot -q -m "hil and sim"    # one suite
hil run --suite io --config configs/rig_sim.yaml
```

## Conventions

- Python 3.11+, typed (mypy non-strict). Keep `hilharness/` coverage ≥ 80%.
- Format with `black`, lint with `ruff` — `make format` fixes both.
- Keep the sim/hw split behind driver interfaces; never special-case the sim
  target inside validators.
- New metrics go through `MetricsStore`; new gates go through
  `hilharness/report/release.py` so the report and CI stay in sync.

## Adding a test

1. Prefer a fast unit test for pure logic (`tests/test_*.py`), marked
   `@pytest.mark.hil @pytest.mark.sim`.
2. For rig-level behaviour, add to the relevant suite under `tests/suite_*` and
   use the `booted` / `net` / `shell` / `can_bus` / `can_node` fixtures.
3. Tests that need real hardware must be marked `@pytest.mark.hw` and skipped
   when the hardware is absent.

## Commits

Small, focused commits with a clear message. CI runs lint, typecheck, the HIL
suites with coverage, and the soak + release gates on every push and PR.
