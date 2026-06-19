"""SSHTransport: run commands and move files on one host (subprocess or paramiko)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from yuj import _shell
from yuj._shell import CommandResult
from yuj.exceptions import AuthError, TransportError
from yuj.fleet import Host


@runtime_checkable
class Transport(Protocol):
    """The behaviour every transport (SSH or local) provides to higher layers.

    deploy/submit/scatter/pull/probe depend only on this, so a
    :class:`~yuj.local.LocalTransport` and an :class:`SSHTransport` are
    interchangeable.
    """

    host: Host

    def run(
        self,
        command: str,
        *,
        timeout: float | None = ...,
        input_text: str | None = ...,
    ) -> CommandResult:
        """Run ``command`` on the target and capture its output."""
        ...

    def check(self, *, timeout: float = ...) -> bool:
        """Return True if the target answers a trivial command."""
        ...

    def put(
        self,
        local: str | Path,
        remote: str,
        *,
        includes: Sequence[str] = ...,
        excludes: Sequence[str] = ...,
        timeout: float | None = ...,
    ) -> CommandResult:
        """Copy ``local`` up to ``remote`` on the target."""
        ...

    def get(
        self,
        remote: str,
        local: str | Path,
        *,
        includes: Sequence[str] = ...,
        excludes: Sequence[str] = ...,
        timeout: float | None = ...,
    ) -> CommandResult:
        """Copy ``remote`` down to ``local``."""
        ...


def make_transport(
    host: Host, *, connect_timeout: int = 20, backend: str = "subprocess"
) -> Transport:
    """Return the right transport for ``host``: local if it's the controller.

    A host flagged ``local`` (or addressed as loopback) gets a
    :class:`~yuj.local.LocalTransport`; everything else an :class:`SSHTransport`.
    """
    if host.is_local:
        from yuj.local import LocalTransport

        return LocalTransport(host)
    return SSHTransport(host, connect_timeout=connect_timeout, backend=backend)


_AUTH_FAIL_MARKERS = (
    "permission denied",
    "authentication failed",
    "too many authentication failures",
)


def is_auth_failure(text: str) -> bool:
    """Return True if ``text`` reads like an SSH authentication failure.

    Encapsulates the auth-failure marker set so callers (e.g. probe diagnosis)
    can recognise a refused login without depending on how transport spells it.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in _AUTH_FAIL_MARKERS)


class SSHTransport:
    """Run commands and transfer files against one host over SSH."""

    def __init__(
        self,
        host: Host,
        *,
        connect_timeout: int = 20,
        backend: str = "subprocess",
    ) -> None:
        """Create a transport for ``host``.

        Args:
            host: The target host.
            connect_timeout: Seconds to wait for the TCP/SSH connection.
            backend: ``"subprocess"`` (ssh/rsync, the default) or ``"paramiko"``
                (pure-Python fallback; needs the ``paramiko`` extra).
        """
        if backend not in ("subprocess", "paramiko"):
            raise TransportError(f"unknown transport backend: {backend!r}")
        self.host = host
        self.connect_timeout = connect_timeout
        self.backend = backend

    def build_run_command(self, command: str) -> tuple[list[str], dict[str, str]]:
        """Return the ``(argv, env)`` to run ``command`` on the host via ssh."""
        argv = _shell.ssh_command(
            user=self.host.user,
            host=self.host.ip,
            remote_command=command,
            port=self.host.port,
            key_path=self.host.key_path,
            connect_timeout=self.connect_timeout,
            strict_host_key=self.host.strict_host_key,
            known_hosts_file=self.host.known_hosts_file,
        )
        return self._wrap_auth(argv)

    def build_put_command(
        self,
        local: str | Path,
        remote: str,
        *,
        includes: Sequence[str] = (),
        excludes: Sequence[str] = (),
    ) -> tuple[list[str], dict[str, str]]:
        """Return the ``(argv, env)`` to rsync ``local`` up to ``remote``."""
        argv = _shell.rsync_command(
            source=str(local),
            destination=_shell.remote_spec(self.host.user, self.host.ip, remote),
            ssh_host=self.host.ip,
            port=self.host.port,
            key_path=self.host.key_path,
            connect_timeout=self.connect_timeout,
            strict_host_key=self.host.strict_host_key,
            known_hosts_file=self.host.known_hosts_file,
            includes=includes,
            excludes=excludes,
        )
        return self._wrap_auth(argv)

    def build_get_command(
        self,
        remote: str,
        local: str | Path,
        *,
        includes: Sequence[str] = (),
        excludes: Sequence[str] = (),
    ) -> tuple[list[str], dict[str, str]]:
        """Return the ``(argv, env)`` to rsync ``remote`` down to ``local``."""
        argv = _shell.rsync_command(
            source=_shell.remote_spec(self.host.user, self.host.ip, remote),
            destination=str(local),
            ssh_host=self.host.ip,
            port=self.host.port,
            key_path=self.host.key_path,
            connect_timeout=self.connect_timeout,
            strict_host_key=self.host.strict_host_key,
            known_hosts_file=self.host.known_hosts_file,
            includes=includes,
            excludes=excludes,
        )
        return self._wrap_auth(argv)

    def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        input_text: str | None = None,
    ) -> CommandResult:
        """Run ``command`` on the host and return the result.

        ``input_text`` is written to the remote command's stdin. This is how a
        secret like a ``sudo -S`` password is delivered: over the SSH channel,
        never on a command line (where it would show up in ``ps``).

        Raises:
            AuthError: If the failure looks like refused authentication.
            TransportError: For other non-auth connection failures (callers that
                want the raw result should check ``result.ok`` instead; ``run``
                only raises on connection/auth failures, not on a remote command
                that merely exits non-zero).
        """
        if self.backend == "paramiko":
            return self._paramiko_run(command, timeout=timeout, input_text=input_text)
        argv, env = self.build_run_command(command)
        result = _shell.run(argv, timeout=timeout, env=env, input_text=input_text)
        self._raise_for_connection(result, command)
        return result

    def check(self, *, timeout: float = 15.0) -> bool:
        """Return True if the host answers a trivial command over SSH."""
        try:
            result = self.run("echo yuj-ok", timeout=timeout)
        except TransportError:
            return False
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
        """Rsync ``local`` up to ``remote`` on the host."""
        if self.backend == "paramiko":
            return self._paramiko_transfer(local, remote, upload=True, timeout=timeout)
        argv, env = self.build_put_command(
            local, remote, includes=includes, excludes=excludes
        )
        result = _shell.run(argv, timeout=timeout, env=env)
        self._raise_for_connection(result, "rsync put")
        return result

    def get(
        self,
        remote: str,
        local: str | Path,
        *,
        includes: Sequence[str] = (),
        excludes: Sequence[str] = (),
        timeout: float | None = None,
    ) -> CommandResult:
        """Rsync ``remote`` down to ``local``."""
        if self.backend == "paramiko":
            return self._paramiko_transfer(remote, local, upload=False, timeout=timeout)
        argv, env = self.build_get_command(
            remote, local, includes=includes, excludes=excludes
        )
        result = _shell.run(argv, timeout=timeout, env=env)
        self._raise_for_connection(result, "rsync get")
        return result

    def _wrap_auth(self, argv: list[str]) -> tuple[list[str], dict[str, str]]:
        """Prefix sshpass and build the child env carrying the secret (if any)."""
        use_password = self.host.use_password
        wrapped = _shell.with_sshpass(argv, use_password=use_password)
        env = dict(os.environ)
        if use_password and self.host.password is not None:
            env["SSHPASS"] = self.host.password
        return wrapped, env

    def _raise_for_connection(self, result: CommandResult, what: str) -> None:
        if result.ok:
            return
        stderr = result.stderr.lower()
        if is_auth_failure(stderr):
            raise AuthError(
                f"authentication refused by {self.host.name} ({self.host.ip})"
                f" during {what}",
                hint="check the password/key; repeated failures may trip fail2ban",
            )

    def _paramiko_client(self) -> Any:
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - exercised only without extra
            raise TransportError(
                "paramiko backend requested but paramiko is not installed",
                hint="install with: pip install 'yuj[paramiko]'",
            ) from exc
        client = paramiko.SSHClient()
        if self.host.strict_host_key:
            known_hosts = self.host.known_hosts_file or os.path.expanduser(
                "~/.ssh/known_hosts"
            )
            client.load_host_keys(known_hosts)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507  # nosec B507
        try:
            client.connect(
                hostname=self.host.ip,
                port=self.host.port,
                username=self.host.user,
                password=self.host.password,
                key_filename=self.host.key_path,
                timeout=self.connect_timeout,
                allow_agent=False,
                look_for_keys=bool(self.host.key_path),
            )
        except paramiko.AuthenticationException as exc:
            raise AuthError(
                f"authentication refused by {self.host.name} ({self.host.ip})"
            ) from exc
        except OSError as exc:
            raise TransportError(
                f"could not connect to {self.host.name} ({self.host.ip}): {exc}"
            ) from exc
        return client

    def _paramiko_run(
        self, command: str, *, timeout: float | None, input_text: str | None = None
    ) -> CommandResult:
        client = self._paramiko_client()
        try:
            # B601: `command` is the operator's own command (yuj's purpose is to
            # run it on the remote, like `ssh host 'cmd'`), not untrusted input.
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)  # nosec B601
            if input_text is not None:
                stdin.write(input_text)
                stdin.flush()
                stdin.channel.shutdown_write()
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
        finally:
            client.close()
        return CommandResult(returncode=code, stdout=out, stderr=err)

    def _paramiko_transfer(
        self,
        first: str | Path,
        second: str | Path,
        *,
        upload: bool,
        timeout: float | None,
    ) -> CommandResult:
        """Directory sync needs the rsync backend."""
        client = self._paramiko_client()
        try:
            sftp = client.open_sftp()
            try:
                if upload:
                    sftp.put(str(first), str(second))
                else:
                    sftp.get(str(first), str(second))
            finally:
                sftp.close()
        except OSError as exc:
            raise TransportError(f"sftp transfer failed: {exc}") from exc
        finally:
            client.close()
        return CommandResult(returncode=0, stdout="", stderr="")
