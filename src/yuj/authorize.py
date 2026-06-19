"""Install a public key into each host's authorized_keys."""

from __future__ import annotations

from dataclasses import dataclass

from yuj.exceptions import YujError
from yuj.fleet import Fleet, Host, map_fleet
from yuj.transport import Transport, make_transport

_INSTALL = r"""
set -eu
key=$(cat)
install -d -m 700 "$HOME/.ssh"
auth="$HOME/.ssh/authorized_keys"
touch "$auth"
chmod 600 "$auth"
if grep -qxF "$key" "$auth"; then
    echo YUJ-ALREADY
else
    printf '%s\n' "$key" >> "$auth"
    echo YUJ-INSTALLED
fi
"""


@dataclass(frozen=True)
class AuthorizeResult:
    """Outcome of installing the key on one host."""

    host: str
    ok: bool
    already: bool = False
    error: str | None = None


def authorize_key(
    transport: Transport, public_key: str, *, timeout: float = 30.0
) -> AuthorizeResult:
    """Append ``public_key`` to the host's authorized_keys (idempotent)."""
    host = transport.host.name
    try:
        result = transport.run(_INSTALL, timeout=timeout, input_text=public_key)
    except YujError as exc:
        return AuthorizeResult(host, ok=False, error=str(exc))
    if not result.ok:
        return AuthorizeResult(host, ok=False, error=result.stderr.strip() or "failed")
    return AuthorizeResult(host, ok=True, already="YUJ-ALREADY" in result.stdout)


def authorize_fleet(
    fleet: Fleet,
    public_key: str,
    *,
    connect_timeout: int = 20,
    timeout: float = 30.0,
    max_workers: int = 8,
) -> dict[str, AuthorizeResult]:
    """Install ``public_key`` on every usable host in parallel, tolerant of failures."""

    def _one(host: Host) -> AuthorizeResult:
        return authorize_key(
            make_transport(host, connect_timeout=connect_timeout),
            public_key,
            timeout=timeout,
        )

    return map_fleet(fleet.usable, _one, max_workers=max_workers)
