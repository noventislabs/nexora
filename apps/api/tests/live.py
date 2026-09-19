"""Helpers for opt-in tests that call a real third-party API.

These are *not* part of the hermetic unit suite. They run only when a real credential
is present in the environment or in the repository ``.env``, and they are skipped
otherwise so the suite stays green on a machine with no credentials.

Run them with:  pytest -m live
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def live_credential(name: str) -> str | None:
    """Read a credential for a live test, from the process env or the repo .env.

    ``conftest`` deliberately blanks these in ``os.environ`` to keep unit tests
    hermetic, so this reads the ``.env`` file directly rather than through settings.
    """
    value = os.environ.get(f"NEXORA_LIVE_{name}") or ""
    if value.strip():
        return value.strip()

    if not _ENV_FILE.is_file():
        return None
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() == name and raw.strip():
            return raw.strip()
    return None
