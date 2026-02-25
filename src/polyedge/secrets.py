"""Secrets loader with file-permission enforcement (spec §22.2, §5.4 step 2).

Verifies all required secret files exist and are NOT world-readable.
On any failure: raises InsecureSecretsError so the caller halts.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

REQUIRED_SECRETS = (
    "LOCAL_STATE_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "OPENROUTER_API_KEY",
    "POLYMARKET_API_KEY",
)


class InsecureSecretsError(Exception):
    """Raised when secrets files are missing or have insecure permissions."""


def _check_permissions(path: Path) -> None:
    """Verify a file is not world-readable (spec §22.2)."""
    st = os.stat(path)
    mode = st.st_mode

    if mode & stat.S_IROTH:
        raise InsecureSecretsError(
            "Secret file is world-readable (others +r): {}  "
            "mode={}.  Run: chmod o-r {}".format(path, oct(mode), path)
        )

    if mode & stat.S_IWOTH:
        raise InsecureSecretsError(
            "Secret file is world-writable (others +w): {}  "
            "mode={}.  Run: chmod o-w {}".format(path, oct(mode), path)
        )


def load_secrets(secret_dir: str) -> Dict[str, str]:
    """Load all required secrets from individual files in secret_dir.

    Each file is named after its secret key (e.g. LOCAL_STATE_SECRET)
    and contains the raw secret value (whitespace stripped).

    Raises InsecureSecretsError if any file is missing or insecure.
    """
    secret_path = Path(secret_dir)

    if not secret_path.is_dir():
        raise InsecureSecretsError("Secrets directory does not exist: {}".format(secret_path))

    secrets = {}  # type: Dict[str, str]
    errors = []  # type: List[str]

    for name in REQUIRED_SECRETS:
        fpath = secret_path / name
        if not fpath.is_file():
            errors.append("Missing required secret file: {}".format(fpath))
            continue

        try:
            _check_permissions(fpath)
        except InsecureSecretsError as e:
            errors.append(str(e))
            continue

        value = fpath.read_text(encoding="utf-8").strip()
        if not value:
            errors.append("Secret file is empty: {}".format(fpath))
            continue

        secrets[name] = value

    if errors:
        msg = "Secrets validation failed:\n  " + "\n  ".join(errors)
        raise InsecureSecretsError(msg)

    logger.info(
        "All %d secrets loaded from %s",
        len(secrets),
        secret_path,
    )
    return secrets
