"""A short end-to-end soak exercising the full pipeline."""

from __future__ import annotations

import pytest

from hilharness.config import NetConfig, RigConfig
from hilharness.soak import run_soak


@pytest.mark.hil
@pytest.mark.sim
def test_two_cycle_soak(tmp_path):
    cfg = RigConfig.from_yaml("configs/rig_sim.yaml")
    # Use an ephemeral control port so the test never clashes with the session
    # rig (the IO checks are covered by the IO suite).
    cfg.net = NetConfig(host="127.0.0.1", port=0, timeout_s=1.0, retries=1, backoff_s=0.05)
    summary = run_soak(
        cfg,
        2,
        store_path=str(tmp_path / "hil.db"),
        out_json=str(tmp_path / "soak.json"),
        can_load_s=0.4,
        stress_s=0.3,
    )
    assert summary["cycles"] == 2
    assert summary["clean"] == 2
    assert summary["boot_ms_p95"] > 0
    assert (tmp_path / "soak.json").exists()
