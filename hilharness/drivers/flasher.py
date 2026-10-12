"""Firmware / rootfs flashing drivers.

``Flasher`` verifies the image sha256 and stages it into the DUT sandbox (the
simulated target's "flash"). ``HwFlasher`` shells out to a configurable external
command (``dd`` / ``fastboot`` / vendor tool) for real hardware.
"""

from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FlashResult:
    image_path: str
    sha256: str
    expected_sha256: str | None
    verified: bool
    duration_s: float
    size_bytes: int
    dest: str | None = None
    command: str | None = None

    def to_dict(self) -> dict:
        return {
            "image_path": self.image_path,
            "sha256": self.sha256,
            "expected_sha256": self.expected_sha256,
            "verified": self.verified,
            "duration_s": round(self.duration_s, 4),
            "size_bytes": self.size_bytes,
            "dest": self.dest,
            "command": self.command,
        }


def sha256_file(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _read_expected_sidecar(image: Path) -> str | None:
    for suffix in (".sha256", ".sha256sum"):
        side = image.with_name(image.name + suffix)
        if side.exists():
            first = side.read_text(encoding="utf-8").strip().split()
            if first:
                return first[0].lower()
    return None


class Flasher:
    """Simulated/software flasher: verify, then stage into the sandbox."""

    def __init__(self, sandbox: Path, expected_sha256: str | None = None) -> None:
        self.sandbox = Path(sandbox)
        self.expected_sha256 = expected_sha256

    def flash(self, image_path: str | Path) -> FlashResult:
        image = Path(image_path)
        if not image.exists():
            raise FileNotFoundError(f"image not found: {image}")

        start = time.perf_counter()
        digest = sha256_file(image)
        expected = self.expected_sha256 or _read_expected_sidecar(image)
        verified = True if expected is None else (digest == expected.lower())

        self.sandbox.mkdir(parents=True, exist_ok=True)
        dest = self.sandbox / "rootfs.img"
        shutil.copyfile(image, dest)
        # Simulate the write-back / fsync time of a real flash.
        with open(dest, "ab") as fh:
            fh.flush()
        duration = time.perf_counter() - start

        return FlashResult(
            image_path=str(image),
            sha256=digest,
            expected_sha256=expected,
            verified=verified,
            duration_s=duration,
            size_bytes=image.stat().st_size,
            dest=str(dest),
        )


class HwFlasher(Flasher):
    """Real-hardware flasher that runs an external, configurable command.

    ``command`` is a shell-style template; ``{image}`` is substituted with the
    image path. The command's exit code determines success. Tests exercise this
    with a fake command (e.g. ``true`` / ``false``).
    """

    def __init__(
        self,
        sandbox: Path,
        command: str = "true",
        expected_sha256: str | None = None,
    ) -> None:
        super().__init__(sandbox, expected_sha256=expected_sha256)
        self.command = command

    def flash(self, image_path: str | Path) -> FlashResult:
        image = Path(image_path)
        _ = sha256_file(image)  # verify readability early
        rendered = self.command.replace("{image}", shlex.quote(str(image)))
        proc = subprocess.run(rendered, shell=True, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"flash command failed ({proc.returncode}): {rendered}\n{proc.stderr}"
            )
        result = super().flash(image)
        result.command = rendered
        return result
