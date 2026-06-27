"""SSH public-key validation shared by authorize and provision.

The two callers transmit a key differently. ``authorize`` pipes it over stdin
(any comment is fine), while ``provision`` embeds it single-quoted in a generated
remote script (so it must be shell-safe). Both notions of "a usable public key"
live here so they agree on the recognised key types.
"""

from __future__ import annotations

import re
from pathlib import Path

from yuj.exceptions import YujError

# Recognised OpenSSH public-key type prefixes.
_PUBKEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")
# A key line whose charset is also shell-safe (no quotes/backticks/$), so a
# validated key can be embedded single-quoted in a generated remote script.
_SHELL_SAFE = re.compile(r"^[\w+/= @.\-:]+$")


def read_public_key(path: str | Path) -> str:
    """Read an SSH public-key file and check it looks like one."""
    text = Path(path).read_text(encoding="utf-8").strip()
    if not text.startswith(_PUBKEY_PREFIXES):
        raise YujError(
            f"{path} does not look like an SSH public key",
            hint="point --key at a .pub file (e.g. ~/.ssh/yuj_fleet.pub)",
        )
    return text


def is_shell_safe_public_key(key: str) -> bool:
    """True if ``key`` looks like a public key and is safe to embed in a shell."""
    return key.startswith(_PUBKEY_PREFIXES) and bool(_SHELL_SAFE.match(key))
