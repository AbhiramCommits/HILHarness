#!/usr/bin/env bash
# Idempotently bring up a vcan interface for SocketCAN testing.
#
# Safe to run repeatedly. On non-Linux hosts (or kernels without vcan) it prints
# a notice and exits 0 so the harness can fall back to python-can's virtual bus.
set -u

IFACE="${1:-vcan0}"

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "setup_vcan: non-Linux host ($(uname -s)); skipping vcan, harness will use virtual bus"
    exit 0
fi

if ! command -v ip >/dev/null 2>&1; then
    echo "setup_vcan: 'ip' not found; skipping vcan, harness will use virtual bus"
    exit 0
fi

# Load the module (ignore failure - may be builtin or absent).
modprobe vcan 2>/dev/null || true

if ip link show "$IFACE" >/dev/null 2>&1; then
    echo "setup_vcan: $IFACE already exists"
else
    if ip link add dev "$IFACE" type vcan 2>/dev/null; then
        echo "setup_vcan: created $IFACE"
    else
        echo "setup_vcan: could not create $IFACE (no vcan support); using virtual bus"
        exit 0
    fi
fi

ip link set up "$IFACE" && echo "setup_vcan: $IFACE is up"
