"""Serial/UART and Ethernet/TCP-IP DUT control suite."""

from __future__ import annotations

import time

import pytest


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_ping(net):
    assert net.ping() is True


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_sysinfo_keys(net):
    info = net.sysinfo()
    assert "uptime_s" in info and "version" in info
    assert info["uptime_s"] >= 0


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_set_get_roundtrip(net):
    net.set("display.brightness", 80)
    assert net.get("display.brightness") == "80"
    net.set("audio.volume", 42)
    assert net.get("audio.volume") == "42"


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_unknown_key_errors(net):
    from hilharness.drivers.netctl import NetCtlError

    with pytest.raises(NetCtlError):
        net.get("does.not.exist")


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_latency_is_measured(net):
    for _ in range(5):
        net.ping()
    assert len(net.latencies) >= 5
    assert 0 < net.mean_latency_ms < 100.0


@pytest.mark.hil
@pytest.mark.sim
def test_serial_shell_echo(shell):
    out = shell.expect("echo hello-hil", "hello-hil")
    assert "hil$" in out


@pytest.mark.hil
@pytest.mark.sim
def test_serial_shell_sysinfo(shell):
    out = shell.run("sysinfo")
    assert "version" in out


@pytest.mark.hil
@pytest.mark.sim
def test_serial_shell_reports_unknown_command(shell):
    shell.expect_error("frobnicate")


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_stress_memory_grows_rss(net):
    before = net.sysinfo().get("rss_mb", 0.0)
    net.stress("mem", 60)
    peak = before
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        peak = max(peak, net.sysinfo().get("rss_mb", 0.0))
        if peak >= before + 20.0:
            break
        time.sleep(0.25)
    assert peak >= before + 20.0, f"rss did not grow: {before} -> {peak}"


@pytest.mark.hil
@pytest.mark.sim
def test_tcp_stress_cpu_burns_cpu(net, rig):
    import psutil

    pid = rig.target_pid()
    assert pid is not None
    proc = psutil.Process(pid)
    net.stress("cpu", 4)
    time.sleep(0.5)
    cpu = proc.cpu_percent(interval=1.0)
    assert cpu > 40.0, f"target CPU% under stress was {cpu}"
