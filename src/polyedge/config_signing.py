"""Config signing and manifest verification (spec §22, §5.4 step 1).

Implements HMAC-SHA256 manifest signing.  On verification failure the process
MUST halt — callers should catch ConfigTamperError and exit non-zero.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any, Dict, Tuple

logger = logging.getLogger(__name__)

# Files that must be present in the manifest
MANIFEST_FILES = (
    "config.yaml",
    "evidence_sources.json",
    "injection_patterns.json",
    "model_pricing.json",
)


class ConfigTamperError(Exception):
    """Raised when manifest verification fails."""


def compute_file_hash(path: Path) -> str:
    """Return the SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_hashes(config_dir: Path) -> Dict[str, str]:
    """Compute deterministic file hashes for all manifest files."""
    hashes = {}  # type: Dict[str, str]
    for fname in sorted(MANIFEST_FILES):
        fpath = config_dir / fname
        if not fpath.is_file():
            raise ConfigTamperError("Required config file missing: {}".format(fpath))
        hashes[fname] = compute_file_hash(fpath)
    return hashes


def _compute_signature(hashes: Dict[str, str], operator_key: str) -> str:
    """HMAC-SHA256 signature over the canonical hash payload."""
    canonical = "\n".join("{}={}".format(k, v) for k, v in sorted(hashes.items()))
    sig = hmac.new(
        operator_key.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return sig


def generate_manifest(config_dir: Path, operator_key: str) -> Dict[str, Any]:
    """Generate a signed manifest for the config directory.

    Returns the manifest dict and also writes it to config_dir/manifest.json.
    """
    config_dir = Path(config_dir)
    hashes = _canonical_hashes(config_dir)
    signature = _compute_signature(hashes, operator_key)

    manifest = {
        "schema_version": "polyedge.manifest.v2.5",
        "file_hashes": hashes,
        "signature": signature,
    }  # type: Dict[str, Any]

    manifest_path = config_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    logger.info("Manifest written to %s", manifest_path)
    return manifest


def verify_manifest(config_dir: Path, operator_key: str) -> bool:
    """Verify the signed manifest against current config files.

    Raises ConfigTamperError on any mismatch.  Returns True on success.
    """
    config_dir = Path(config_dir)
    manifest_path = config_dir / "manifest.json"

    if not manifest_path.is_file():
        raise ConfigTamperError("Manifest file not found: {}".format(manifest_path))

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Verify structure
    if "file_hashes" not in manifest or "signature" not in manifest:
        raise ConfigTamperError("Manifest missing required fields")

    stored_hashes = manifest["file_hashes"]
    stored_sig = manifest["signature"]

    # Recompute hashes
    current_hashes = _canonical_hashes(config_dir)

    # Compare file hashes
    for fname in sorted(MANIFEST_FILES):
        if fname not in stored_hashes:
            raise ConfigTamperError("Manifest missing hash for: {}".format(fname))
        if stored_hashes[fname] != current_hashes[fname]:
            raise ConfigTamperError(
                "Hash mismatch for {}: manifest={}... current={}...".format(
                    fname, stored_hashes[fname][:16], current_hashes[fname][:16],
                )
            )

    # Verify signature
    expected_sig = _compute_signature(current_hashes, operator_key)
    if not hmac.compare_digest(stored_sig, expected_sig):
        raise ConfigTamperError("Manifest signature verification failed")

    logger.info("Config manifest verified OK")
    return True
