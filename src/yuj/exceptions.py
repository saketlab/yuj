"""Exception hierarchy for yuj. All errors derive from YujError; most carry a hint."""

from __future__ import annotations


class YujError(Exception):
    """Base class for all yuj errors, with an optional actionable hint."""

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n  hint: {self.hint}" if self.hint else base


class FleetError(YujError):
    """The fleet inventory could not be loaded or is invalid."""


class SplitError(YujError):
    """Work could not be split across the fleet (bad weights, no hosts, etc.)."""


class TransportError(YujError):
    """An SSH/rsync operation against a host failed."""


class AuthError(TransportError):
    """Authentication to a host was refused (wrong password/key, or fail2ban)."""


class CommandTimeout(TransportError):
    """A remote command or transfer exceeded its timeout."""
