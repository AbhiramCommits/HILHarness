"""``hil`` command-line entrypoint."""

from __future__ import annotations

import json
import statistics
import sys
import time

import click

from .config import RigConfig
from .rig import Rig
from .validators.boot import BootResult, validate_boot

DEFAULT_CONFIG = "configs/rig_sim.yaml"


def _pctl(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = q * (len(ordered) - 1)
    lo = int(idx)
    frac = idx - lo
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def _load(config: str) -> RigConfig:
    return RigConfig.from_yaml(config)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="hilharness")
def main() -> None:
    """HILHarness: flash, power-cycle, and validate an embedded Linux DUT."""


@main.command()
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
@click.option("--hw-command", default=None, help="External flash command for target: hw")
def flash(config: str, hw_command: str | None) -> None:
    """Flash the configured image and verify its sha256."""
    cfg = _load(config)
    cfg.ensure_dirs()
    rig = Rig(cfg)
    try:
        result = rig.flasher(hw_command=hw_command).flash(cfg.image_path)
    finally:
        rig.close()

    click.echo(f"image      : {result.image_path}")
    click.echo(f"size       : {result.size_bytes} bytes")
    click.echo(f"sha256     : {result.sha256}")
    if result.expected_sha256:
        click.echo(f"expected   : {result.expected_sha256}")
    click.echo(f"verified   : {'OK' if result.verified else 'MISMATCH'}")
    click.echo(f"flash time : {result.duration_s * 1000:.1f} ms")
    click.echo(f"staged at  : {result.dest}")
    if not result.verified:
        raise SystemExit(2)


@main.command("power-cycle")
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
@click.option("--faults", default="", help="Simulated fault spec (sim target only)")
def power_cycle(config: str, faults: str) -> None:
    """Power the DUT off and back on through the relay."""
    cfg = _load(config)
    cfg.ensure_dirs()
    rig = Rig(cfg, faults=faults)
    try:
        click.echo(f"powering off DUT ({cfg.power.driver} relay, off_ms={cfg.power.off_ms})")
        rig.power_off()
        click.echo("DUT off")
        import time

        time.sleep(cfg.power.off_ms / 1000.0)
        click.echo("powering on DUT")
        rig.power_on()
        alive = rig.wait_alive(timeout=5.0)
        click.echo(f"DUT on (process alive: {alive})")
        if not alive:
            raise SystemExit(1)
    finally:
        rig.close()
    click.echo("power cycle OK")


@main.command()
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
@click.option("--cycles", default=10, show_default=True, type=int)
@click.option("--faults", default="", help="Simulated fault spec (sim target only)")
@click.option("--boot-timeout", default=15.0, show_default=True, type=float)
def boot(config: str, cycles: int, faults: str, boot_timeout: float) -> None:
    """Flash, power-cycle and validate N consecutive boots."""
    cfg = _load(config)
    cfg.ensure_dirs()
    rig = Rig(cfg, faults=faults)
    results: list[BootResult] = []
    wall0 = time.perf_counter()
    try:
        flasher = rig.flasher()
        click.echo(
            f"{'cycle':>5}  {'clean':>5}  {'boot_ms':>9}  {'missing':<12} {'failed':<12} src"
        )
        for i in range(1, cycles + 1):
            flash = flasher.flash(cfg.image_path)
            if not flash.verified:
                click.echo(f"  flash verify FAILED on cycle {i}", err=True)
                raise SystemExit(3)
            rig.power_cycle()
            reader = rig.console(cycle=i)
            reader.capture = ""
            try:
                reader.read_until(r"hil-stage userspace ready", timeout=boot_timeout)
            except TimeoutError:
                reader.drain(quiet_for=0.5, max_total=2.0)
            res = validate_boot(reader.capture, cfg.expected_drivers)
            results.append(res)
            click.echo(
                f"{i:>5}  {str(res.clean_bootchain):>5}  {res.total_boot_ms:>9.1f}  "
                f"{','.join(res.missing_drivers):<12} "
                f"{','.join(res.failed_drivers + res.timeout_drivers):<12} {res.source}"
            )
            rig.power_off()
    finally:
        rig.close()

    wall = time.perf_counter() - wall0
    totals = [r.total_boot_ms for r in results]
    passed = sum(1 for r in results if r.clean_bootchain)
    click.echo("-" * 60)
    click.echo(f"cycles      : {len(results)}")
    click.echo(f"clean boots : {passed}/{len(results)}")
    if totals:
        click.echo(f"boot p50    : {_pctl(totals, 0.50):.1f} ms")
        click.echo(f"boot p95    : {_pctl(totals, 0.95):.1f} ms")
        click.echo(f"boot max    : {max(totals):.1f} ms")
        click.echo(f"boot mean   : {statistics.fmean(totals):.1f} ms")
    click.echo(f"wall clock  : {wall:.2f} s")
    if passed != len(results):
        raise SystemExit(1)


@main.command("show-config")
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
def show_config(config: str) -> None:
    """Print the resolved rig configuration as JSON."""
    cfg = _load(config)
    click.echo(json.dumps(cfg.model_dump(), indent=2))


if __name__ == "__main__":
    sys.exit(main())
