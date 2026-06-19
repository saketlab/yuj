"""Validation helpers for operator config interpolated into remote shells."""

from __future__ import annotations

import re

from yuj.exceptions import YujError

# Path-ish values we are willing to splice into generated shell. This permits
# common Unix paths, globs, and environment-variable references, but rejects
# quotes, whitespace, command separators, and command substitution.
_SAFE_REMOTE_PATH = re.compile(r"^[\w@%+=:,./~${}*?-]+$")
_SAFE_REMOTE_GLOB = re.compile(r"^[\w@%+=:,./~*?\[\]{}-]+$")


def validate_remote_path(value: str, *, label: str) -> str:
    """Return ``value`` if it is safe to interpolate as a remote path."""
    if not _SAFE_REMOTE_PATH.match(value):
        raise YujError(
            f"unsafe {label}: {value!r}",
            hint="use only path/variable characters (no spaces or shell metas)",
        )
    return value


def validate_remote_glob(value: str, *, label: str = "results glob") -> str:
    """Return ``value`` if it is safe to interpolate as a remote glob."""
    if not _SAFE_REMOTE_GLOB.match(value):
        raise YujError(
            f"unsafe {label}: {value!r}",
            hint="use only path/glob characters (no spaces or shell metacharacters)",
        )
    return value
