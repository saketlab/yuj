"""Subprocess wrappers and SSH/rsync argv builders. Secrets go via env, never argv."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from yuj.exceptions import CommandTimeout, TransportError

# Shared SSH options; host-key verification is configured per host.
_COMMON_SSH_OPTS: tuple[str, ...] = (
    "-o",
    "NumberOfPasswordPrompts=1",
    "-o",
    "LogLevel=ERROR",
)


def _ssh_options(*, strict_host_key: bool, known_hosts_file: str | None) -> list[str]:
    opts = list(_COMMON_SSH_OPTS)
    if strict_host_key:
        opts += ["-o", "StrictHostKeyChecking=yes"]
        if known_hosts_file:
            opts += ["-o", f"UserKnownHostsFile={known_hosts_file}"]
    else:
        opts += ["-o", "StrictHostKeyChecking=no"]
        opts += ["-o", "UserKnownHostsFile=/dev/null"]
    return opts


@dataclass(frozen=True)
class CommandResult:
    """The outcome of a finished subprocess: exit code, captured I/O, duration."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True if the command exited zero."""
        return self.returncode == 0


def have_executable(name: str) -> bool:
    return shutil.which(name) is not None


def run(
    args: Sequence[str],
    *,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    input_text: str | None = None,
    check: bool = False,
) -> CommandResult:
    """Run ``args`` as a subprocess (never via a shell) and capture its output.

    Args:
        args: The command as an argv list. Never a shell string.
        timeout: Seconds before the process is killed and :class:`CommandTimeout`
            is raised.
        env: Environment for the child. When ``None`` the parent's is inherited.
        input_text: Optional text written to the child's stdin.
        check: If True, raise :class:`TransportError` on a non-zero exit.

    Returns:
        A :class:`CommandResult` with the exit code and captured streams.

    Raises:
        CommandTimeout: If the command exceeds ``timeout``.
        TransportError: If ``check`` is True and the command exits non-zero.
    """
    try:
        completed = subprocess.run(  # noqa: S603 - argv list, never shell=True
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=dict(env) if env is not None else None,
            input=input_text,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandTimeout(
            f"command timed out after {timeout}s: {_safe_join(args)}",
            hint="raise the timeout, or check whether the host is reachable",
        ) from exc
    except FileNotFoundError as exc:
        raise TransportError(
            f"executable not found: {args[0]!r}",
            hint=f"install {args[0]!r} or ensure it is on PATH",
        ) from exc

    result = CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )
    if check and not result.ok:
        raise TransportError(
            f"command failed (exit {result.returncode}): {_safe_join(args)}\n"
            f"{result.stderr.strip()}"
        )
    return result


def ssh_command(
    *,
    user: str,
    host: str,
    remote_command: str,
    port: int = 22,
    key_path: str | None = None,
    connect_timeout: int = 20,
    force_password: bool = False,
    strict_host_key: bool = False,
    known_hosts_file: str | None = None,
    extra_opts: Sequence[str] = (),
) -> list[str]:
    """Build the ``ssh`` argv to run ``remote_command`` on a host.

    The remote command is appended as a single argument; the remote login shell
    parses it, so nothing is interpolated into a *local* shell. When ``key_path``
    is given, key auth is used; otherwise (or with ``force_password``) password
    auth is forced. The password itself is never part of this argv; it is fed to
    ``sshpass`` via the environment by the caller.
    """
    argv: list[str] = [
        "ssh",
        *_ssh_options(
            strict_host_key=strict_host_key, known_hosts_file=known_hosts_file
        ),
    ]
    argv += ["-o", f"ConnectTimeout={connect_timeout}"]
    argv += ["-p", str(port)]
    if key_path:
        argv += ["-i", key_path, "-o", "PubkeyAuthentication=yes"]
    if force_password or not key_path:
        argv += ["-o", "PubkeyAuthentication=no"]
    argv += list(extra_opts)
    argv += [f"{user}@{host}", remote_command]
    return argv


def rsync_command(
    *,
    source: str,
    destination: str,
    ssh_host: str | None = None,
    port: int = 22,
    key_path: str | None = None,
    connect_timeout: int = 20,
    force_password: bool = False,
    strict_host_key: bool = False,
    known_hosts_file: str | None = None,
    includes: Sequence[str] = (),
    excludes: Sequence[str] = (),
    extra_opts: Sequence[str] = (),
) -> list[str]:
    """Build an ``rsync -az --partial`` argv, optionally over SSH.

    ``source`` and ``destination`` are passed verbatim; exactly one of them is
    expected to be a remote ``host:path`` spec that the caller assembles via
    :func:`remote_spec`. Include/exclude filters are applied in order, matching
    the ``--include`` ... ``--exclude`` precedence rsync itself uses.
    """
    argv: list[str] = ["rsync", "-az", "--partial"]
    if ssh_host is not None:
        ssh_opts = [
            "ssh",
            *_ssh_options(
                strict_host_key=strict_host_key, known_hosts_file=known_hosts_file
            ),
            "-o",
            f"ConnectTimeout={connect_timeout}",
        ]
        ssh_opts += ["-p", str(port)]
        if key_path:
            ssh_opts += ["-i", key_path, "-o", "PubkeyAuthentication=yes"]
        if force_password or not key_path:
            ssh_opts += ["-o", "PubkeyAuthentication=no"]
        argv += ["-e", " ".join(ssh_opts)]
    for pattern in includes:
        argv += [f"--include={pattern}"]
    for pattern in excludes:
        argv += [f"--exclude={pattern}"]
    argv += list(extra_opts)
    argv += [source, destination]
    return argv


def remote_spec(user: str, host: str, path: str) -> str:
    return f"{user}@{host}:{path}"


def with_sshpass(
    argv: Sequence[str],
    *,
    use_password: bool,
) -> list[str]:
    """Prefix ``argv`` with ``sshpass -e`` when password auth is in play.

    ``sshpass -e`` reads the password from the ``SSHPASS`` environment variable,
    so the secret never appears in the argv (and thus never in ``ps`` output or
    in anything we might log). The caller is responsible for placing the password
    in ``SSHPASS`` in the child environment.
    """
    if not use_password:
        return list(argv)
    return ["sshpass", "-e", *argv]


def _safe_join(args: Sequence[str]) -> str:
    """argv never contains secrets by construction."""
    return " ".join(args)
