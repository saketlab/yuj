"""LocalTransport: run work on the controller itself, without SSH.

The machine running yuj is often also a capable worker (a 96 GB GPU box that
happens to be where you launch from). Tunnelling to it over ``ssh localhost`` is
fragile — it depends on the host accepting key auth to itself, which many
hardened sshd configs refuse. A host marked ``local`` (or pointed at
``localhost``/``127.0.0.1``) uses this transport instead: commands run through a
local shell and files are copied with local ``rsync``. The interface matches
:class:`~yuj.transport.SSHTransport` so every higher-level operation works
unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from yuj import _shell
from yuj._shell import CommandResult
from yuj.fleet import Host


class LocalTransport:
    """Run commands and copy files on the local machine (no SSH)."""

    def __init__(self, host: Host, **_ignored: object) -> None:
        """Create a local transport for ``host`` (extra kwargs ignored)."""
        self.host = host

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        """Run ``command`` in a local shell, mirroring an ``ssh host 'cmd'`` call.

        SSH non-login shells start in ``$HOME``, so a command's relative paths
        (e.g. ``mkdir -p remote_dir``) resolve under home. We ``cd "$HOME"`` first
        to match, otherwise they would resolve against the controller's cwd.
        """
        wrapped = f'cd "$HOME" || exit 1\n{command}'
        return _shell.run(
            ["bash", "-c", wrapped], timeout=timeout, input_text=input_text
        )

    def check(self, *, timeout: float = 15.0) -> bool:
        """Return True if the local shell answers a trivial command."""
        result = self.run("echo yuj-ok", timeout=timeout)
        return result.ok and "yuj-ok" in result.stdout

    def put(
        self,
        local: str | Path,
        remote: str,
        *,
        includes: Sequence[str] = (),
        excludes: Sequence[str] = (),
        timeout: float | None = None,
    ) -> CommandResult:
        """Copy ``local`` into ``remote`` (home-relative unless absolute)."""
        return self._rsync(str(local), _expand(remote), includes, excludes, timeout)

    def get(
        self,
        remote: str,
        local: str | Path,
        *,
        includes: Sequence[str] = (),
        excludes: Sequence[str] = (),
        timeout: float | None = None,
    ) -> CommandResult:
        """Copy ``remote`` (home-relative unless absolute) out to ``local``."""
        return self._rsync(_expand(remote), str(local), includes, excludes, timeout)

    @staticmethod
    def _rsync(
        source: str,
        dest: str,
        includes: Sequence[str],
        excludes: Sequence[str],
        timeout: float | None,
    ) -> CommandResult:
        argv = ["rsync", "-a"]
        argv += [f"--include={pat}" for pat in includes]
        argv += [f"--exclude={pat}" for pat in excludes]
        argv += [source, dest]
        return _shell.run(argv, timeout=timeout)


def _expand(remote: str) -> str:
    """Resolve a remote-style path locally: home-relative unless absolute.

    Preserves a trailing slash, which rsync uses to mean "contents of".
    """
    trailing = "/" if remote.endswith("/") and len(remote) > 1 else ""
    path = Path(remote)
    if not path.is_absolute():
        path = Path.home() / path
    return str(path) + trailing
