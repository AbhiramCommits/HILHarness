#!/usr/bin/env python3
"""Run the HIL soak. Thin wrapper around ``hilharness.soak.run_soak``.

Also available as ``hil soak --cycles N``.
"""

from __future__ import annotations

import argparse

from hilharness.config import RigConfig
from hilharness.soak import run_soak


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cycles", type=int, default=50)
    ap.add_argument("--config", default="configs/rig_sim.yaml")
    args = ap.parse_args()
    cfg = RigConfig.from_yaml(args.config)
    summary = run_soak(cfg, args.cycles)
    print(
        f"soak complete: run_id={summary['run_id']} cycles={summary['cycles']} "
        f"clean={summary['clean_rate']}% boot_p95={summary['boot_ms_p95']:.1f}ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
