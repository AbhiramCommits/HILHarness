#!/usr/bin/env python3
"""Generate a small, deterministic dummy rootfs image for the simulator.

The bytes are pseudo-random but reproducible from a fixed seed, so the sha256
sidecar is stable across machines and CI.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

SIZE = 64 * 1024
SEED = b"HILHarness-rootfs-image-v1"


def gen(size: int = SIZE) -> bytes:
    out = bytearray()
    block = hashlib.sha256(SEED).digest()
    counter = 0
    while len(out) < size:
        block = hashlib.sha256(block + counter.to_bytes(4, "little")).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:size])


def main() -> int:
    dest = Path(sys.argv[1] if len(sys.argv) > 1 else "configs/rootfs.img")
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = gen()
    dest.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    dest.with_name(dest.name + ".sha256").write_text(f"{digest}  {dest.name}\n")
    print(f"wrote {dest} ({len(data)} bytes) sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
