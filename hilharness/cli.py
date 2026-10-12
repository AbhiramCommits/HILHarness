"""``hil`` command-line entrypoint."""

from __future__ import annotations

import json
import statistics
import sys
import time

import click

from .config import RigConfig
from .drivers.canbus import CanBus
from .rig import Rig
from .validators.boot import BootResult, validate_boot
from .validators.can import detect_faults

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


def _start_can_node(bus: CanBus, faults_spec: dict):
    """Start the Python node on a virtual bus, or the C node on SocketCAN."""
    if bus.backend == "socketcan":
        import shutil
        import subprocess

        c_node = shutil.which("can_node") or "native/can_node"
        args = [c_node, "--iface", bus.cfg.interface]
        for key, val in faults_spec.items():
            if val:
                args += [f"--{key.replace('_', '-')}", str(val)]
        return subprocess.Popen(args)
    from simtarget.can_node import CanFaults, PyCanNode

    node = PyCanNode(bus.channel, bus.cfg.dbc_path, faults=CanFaults(**faults_spec))
    node.start()
    return node


@main.command("can-test")
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
@click.option("--duration", default=30.0, show_default=True, type=float)
@click.option("--drop-rate", default=0.0, type=float, help="Inject frame drops (0..1)")
@click.option("--stale-signal", default=None, help="Freeze a signal value")
@click.option("--bad-dlc", default=None, type=int, help="Truncate frames to this DLC")
@click.option("--out-of-range", default=None, help="Push a signal out of its DBC range")
@click.option("--silence-ms", default=0, type=int, help="Silence the bus for this long")
def can_test(
    config: str,
    duration: float,
    drop_rate: float,
    stale_signal: str | None,
    bad_dlc: int | None,
    out_of_range: str | None,
    silence_ms: int,
) -> None:
    """Generate and decode CAN traffic; report loss, jitter and faults."""
    cfg = _load(config)
    bus = CanBus(cfg.can)
    faults = {
        "drop_rate": drop_rate,
        "stale_signal": stale_signal,
        "bad_dlc": bad_dlc,
        "out_of_range": out_of_range,
        "silence_ms": silence_ms,
    }
    click.echo(f"backend    : {bus.backend} (channel {bus.channel})")
    node = _start_can_node(bus, faults)
    try:
        time.sleep(0.3)
        click.echo(f"streaming  : {duration:.1f}s ...")
        frames = bus.stream(duration)
    finally:
        if hasattr(node, "stop"):
            node.stop()
        elif hasattr(node, "terminate"):
            node.terminate()

    by_name: dict[str, int] = {}
    for f in frames:
        name = f.message_name or f"0x{f.arbitration_id:X}"
        by_name[name] = by_name.get(name, 0) + 1
    tx = getattr(node, "tx_counts", {})
    total_tx = sum(tx.values()) if isinstance(tx, dict) else len(frames)

    click.echo(f"frames rx  : {len(frames)}")
    click.echo(f"frames tx  : {total_tx}")
    for name, count in sorted(by_name.items()):
        click.echo(f"  {name:16} {count:>6} rx")

    loss = bus.sequence_loss_stats(frames)
    click.echo(
        f"frame loss : {loss['loss_pct']:.3f}% "
        f"({loss['lost']} lost / {loss['delivered'] + loss['lost']} expected)"
    )

    veh = [f for f in frames if f.arbitration_id == 0x100]
    if len(veh) >= 2:
        ts = sorted(f.timestamp for f in veh)
        periods = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:], strict=False)]
        mean = statistics.fmean(periods)
        jitter = max(abs(p - mean) for p in periods)
        click.echo(f"period     : mean {mean:.2f} ms, jitter +/-{jitter:.2f} ms (n={len(ts)})")

    findings = detect_faults(frames, db=bus.dbc)
    click.echo(f"faults     : {len(findings)} finding(s)")
    for f in findings:
        click.echo(f"  {f.kind:18} {f.message:14} {f.detail}")
    bus.close()


def _parse_junit(path: str) -> dict:
    import xml.etree.ElementTree as ET

    tree = ET.parse(path)
    root = tree.getroot()
    suites = root.findall("testsuite") or [root]
    cases = []
    tests = failures = errors = skipped = 0
    for suite in suites:
        tests += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        for case in suite.findall("testcase"):
            status = "passed"
            message = ""
            if case.find("failure") is not None:
                status = "failed"
                message = case.find("failure").get("message", "")[:200]
            elif case.find("error") is not None:
                status = "error"
                message = case.find("error").get("message", "")[:200]
            elif case.find("skipped") is not None:
                status = "skipped"
            cases.append(
                {
                    "name": f"{case.get('classname', '')}::{case.get('name', '')}",
                    "status": status,
                    "duration_s": float(case.get("time", 0.0)),
                    "message": message,
                }
            )
    passed = tests - failures - errors - skipped
    return {
        "tests": tests,
        "passed": passed,
        "failed": failures,
        "errors": errors,
        "skipped": skipped,
        "pass_rate": round(100.0 * passed / tests, 2) if tests else 0.0,
        "time_s": round(float(root.get("time", 0.0) or (suites[0].get("time", 0.0))), 3),
        "cases": cases,
    }


@main.command()
@click.option(
    "--suite",
    type=click.Choice(["all", "boot", "can", "io"]),
    default="all",
    show_default=True,
)
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
@click.option("--markers", default="hil and sim", show_default=True)
@click.option("--junit", default="results/junit.xml", show_default=True)
@click.option("--json-out", default="results/hil_results.json", show_default=True)
def run(suite: str, config: str, markers: str, junit: str, json_out: str) -> None:
    """Run the pytest HIL suites headlessly and write junit + JSON results."""
    from pathlib import Path

    import pytest

    Path(junit).parent.mkdir(parents=True, exist_ok=True)
    target = "tests" if suite == "all" else f"tests/suite_{suite}"
    args = ["-q", "-m", markers, "--config", config, f"--junitxml={junit}", target]
    click.echo(f"hil run: suite={suite} markers={markers!r} -> {target}")
    code = pytest.main(args)

    results = {"suite": suite, "markers": markers, "exit_code": int(code)}
    if Path(junit).exists():
        results.update(_parse_junit(junit))
    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    click.echo(
        f"results    : {results.get('passed', 0)} passed, {results.get('failed', 0)} failed, "
        f"{results.get('errors', 0)} errors, {results.get('skipped', 0)} skipped "
        f"(pass rate {results.get('pass_rate', 0.0)}%)"
    )
    click.echo(f"junit      : {junit}")
    click.echo(f"json       : {json_out}")
    raise SystemExit(code)


@main.command("show-config")
@click.option("--config", default=DEFAULT_CONFIG, show_default=True)
def show_config(config: str) -> None:
    """Print the resolved rig configuration as JSON."""
    cfg = _load(config)
    click.echo(json.dumps(cfg.model_dump(), indent=2))


if __name__ == "__main__":
    sys.exit(main())
