"""Tests for config signing and manifest verification."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from polyedge.config_signing import (
    ConfigTamperError,
    compute_file_hash,
    generate_manifest,
    verify_manifest,
)

OPERATOR_KEY = "test-operator-key-2025"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Create a minimal config directory with all required files."""
    (tmp_path / "config.yaml").write_text("key: value\n")
    (tmp_path / "evidence_sources.json").write_text('{"sources": []}\n')
    (tmp_path / "injection_patterns.json").write_text(
        '{"pattern_set_version": "1.0.0", "patterns": []}\n'
    )
    (tmp_path / "model_pricing.json").write_text('{"models": {}}\n')
    return tmp_path


def test_compute_file_hash(tmp_path: Path) -> None:
    """SHA-256 hash is deterministic for same content."""
    f = tmp_path / "test.txt"
    f.write_text("hello world")
    h1 = compute_file_hash(f)
    h2 = compute_file_hash(f)
    assert h1 == h2
    assert len(h1) == 64  # hex SHA-256


def test_manifest_generation(config_dir: Path) -> None:
    """Generate manifest succeeds and creates manifest.json."""
    manifest = generate_manifest(config_dir, OPERATOR_KEY)
    assert "file_hashes" in manifest
    assert "signature" in manifest
    assert len(manifest["file_hashes"]) == 4
    assert (config_dir / "manifest.json").exists()


def test_manifest_verification_passes(config_dir: Path) -> None:
    """Verify manifest passes for untampered config."""
    generate_manifest(config_dir, OPERATOR_KEY)
    assert verify_manifest(config_dir, OPERATOR_KEY) is True


def test_manifest_tamper_detection(config_dir: Path) -> None:
    """Modifying a config file after signing triggers ConfigTamperError."""
    generate_manifest(config_dir, OPERATOR_KEY)
    # Tamper with config.yaml
    (config_dir / "config.yaml").write_text("tampered: true\n")
    with pytest.raises(ConfigTamperError, match="Hash mismatch"):
        verify_manifest(config_dir, OPERATOR_KEY)


def test_manifest_wrong_key(config_dir: Path) -> None:
    """Wrong operator key fails signature verification."""
    generate_manifest(config_dir, OPERATOR_KEY)
    with pytest.raises(ConfigTamperError, match="signature verification failed"):
        verify_manifest(config_dir, "wrong-key")


def test_manifest_missing_file(config_dir: Path) -> None:
    """Missing a required config file triggers ConfigTamperError."""
    generate_manifest(config_dir, OPERATOR_KEY)
    os.remove(config_dir / "model_pricing.json")
    with pytest.raises(ConfigTamperError, match="missing"):
        verify_manifest(config_dir, OPERATOR_KEY)


def test_manifest_missing_manifest_file(config_dir: Path) -> None:
    """Missing manifest.json triggers ConfigTamperError."""
    with pytest.raises(ConfigTamperError, match="not found"):
        verify_manifest(config_dir, OPERATOR_KEY)
