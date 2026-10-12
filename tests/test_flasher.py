"""Unit tests for the flasher (sha256 verification and hw command)."""

from __future__ import annotations

import hashlib

import pytest

from hilharness.drivers.flasher import Flasher, HwFlasher, sha256_file


def _make_image(tmp_path, data: bytes = b"rootfs-bytes"):
    img = tmp_path / "rootfs.img"
    img.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    img.with_name("rootfs.img.sha256").write_text(f"{digest}  rootfs.img\n")
    return img, digest


@pytest.mark.hil
@pytest.mark.sim
def test_flash_verifies_sha256(tmp_path):
    img, digest = _make_image(tmp_path)
    result = Flasher(tmp_path / "sandbox").flash(img)
    assert result.verified is True
    assert result.sha256 == digest
    assert result.duration_s >= 0
    assert (tmp_path / "sandbox" / "rootfs.img").exists()


@pytest.mark.hil
@pytest.mark.sim
def test_flash_detects_mismatch(tmp_path):
    img, _ = _make_image(tmp_path)
    img.with_name("rootfs.img.sha256").write_text("0" * 64 + "  rootfs.img\n")
    result = Flasher(tmp_path / "sandbox").flash(img)
    assert result.verified is False


@pytest.mark.hil
@pytest.mark.sim
def test_flash_missing_image_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Flasher(tmp_path / "sandbox").flash(tmp_path / "nope.img")


@pytest.mark.hil
@pytest.mark.sim
def test_hw_flasher_runs_command(tmp_path):
    img, _ = _make_image(tmp_path)
    result = HwFlasher(tmp_path / "sandbox", command="true # {image}").flash(img)
    assert result.verified is True
    assert result.command is not None


@pytest.mark.hil
@pytest.mark.sim
def test_hw_flasher_failure_raises(tmp_path):
    img, _ = _make_image(tmp_path)
    with pytest.raises(RuntimeError):
        HwFlasher(tmp_path / "sandbox", command="false").flash(img)


@pytest.mark.hil
@pytest.mark.sim
def test_sha256_file(tmp_path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"abc")
    assert sha256_file(p) == hashlib.sha256(b"abc").hexdigest()
