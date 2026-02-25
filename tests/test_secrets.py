"""Tests for secrets loader and permission checks."""

import os
import stat
import sys
from pathlib import Path

import pytest

from polyedge.secrets import InsecureSecretsError, load_secrets, REQUIRED_SECRETS


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    """Create a secrets directory with all required files and safe permissions."""
    for name in REQUIRED_SECRETS:
        fpath = tmp_path / name
        fpath.write_text(f"secret-value-for-{name}")
        # Set to owner-only read/write (0600)
        os.chmod(fpath, stat.S_IRUSR | stat.S_IWUSR)
    return tmp_path


def test_secrets_load_success(secrets_dir: Path) -> None:
    """All secrets load when files exist with correct permissions."""
    secrets = load_secrets(secrets_dir)
    assert len(secrets) == len(REQUIRED_SECRETS)
    for name in REQUIRED_SECRETS:
        assert name in secrets
        assert secrets[name] == f"secret-value-for-{name}"


def test_secrets_missing_file(secrets_dir: Path) -> None:
    """Missing a required secret file raises InsecureSecretsError."""
    os.remove(secrets_dir / "TELEGRAM_BOT_TOKEN")
    with pytest.raises(InsecureSecretsError, match="Missing required secret"):
        load_secrets(secrets_dir)


def test_secrets_empty_file(secrets_dir: Path) -> None:
    """Empty secret file raises InsecureSecretsError."""
    (secrets_dir / "LOCAL_STATE_SECRET").write_text("")
    os.chmod(secrets_dir / "LOCAL_STATE_SECRET", stat.S_IRUSR | stat.S_IWUSR)
    with pytest.raises(InsecureSecretsError, match="empty"):
        load_secrets(secrets_dir)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission checks not applicable on Windows",
)
def test_secrets_world_readable(secrets_dir: Path) -> None:
    """World-readable secret file raises InsecureSecretsError."""
    fpath = secrets_dir / "LOCAL_STATE_SECRET"
    # Make world-readable
    os.chmod(fpath, stat.S_IRUSR | stat.S_IWUSR | stat.S_IROTH)
    with pytest.raises(InsecureSecretsError, match="world-readable"):
        load_secrets(secrets_dir)


def test_secrets_missing_directory(tmp_path: Path) -> None:
    """Non-existent secrets directory raises InsecureSecretsError."""
    with pytest.raises(InsecureSecretsError, match="does not exist"):
        load_secrets(tmp_path / "nonexistent")
