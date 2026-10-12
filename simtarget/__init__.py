"""Simulated DUT: a userspace process that emulates an embedded Linux target.

It streams a realistic kernel-style boot log to a PTY, then listens on a TCP
control port for the harness's :class:`~hilharness.drivers.netctl.NetCtl`
client. Faults can be injected via environment variables or CLI flags.
"""

__all__ = ["target", "bootlog", "ecu_profile", "can_node"]
