"""Create an unprivileged worker account on each host and install its SSH key."""

from __future__ import annotations

import csv
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from yuj._shell import have_executable
from yuj._shell import run as _run
from yuj.exceptions import YujError
from yuj.fleet import Fleet, map_fleet
from yuj.keys import is_shell_safe_public_key
from yuj.transport import Transport, make_transport

DEFAULT_PROVISION_TIMEOUT = 120.0
DEFAULT_MAX_WORKERS = 4
DEFAULT_KEY_DIR = ".yuj/keys"
DEFAULT_FLEET_OUT = "provisioned-fleet.csv"

# A Linux username we are willing to splice into the remote shell. Lower-case
# start, then word characters / dashes; the same charset useradd accepts. This
# both validates the name and forecloses any shell-injection via the username.
_VALID_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


@dataclass(frozen=True)
class ProvisionConfig:
    """How to create the worker account.

    Args:
        new_user: The unprivileged account to create on each host.
        shell: Login shell for the new user.
        sudo_password: Password for the admin's ``sudo``. When ``None``, the
            admin host's own SSH password is reused (the common case on lab
            boxes); pass ``""`` to assume passwordless sudo.
        check: Dry-run; report what would happen, create nothing.
    """

    new_user: str = "yuj"
    shell: str = "/bin/bash"
    sudo_password: str | None = field(default=None, repr=False)
    check: bool = False

    def __post_init__(self) -> None:
        if not _VALID_USERNAME.match(self.new_user):
            raise YujError(
                f"invalid worker username {self.new_user!r}",
                hint="use a Linux username: lower-case, starts with a letter/_,"
                " then letters/digits/_/- (max 32 chars)",
            )


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of provisioning one host."""

    host: str
    ok: bool
    ip: str = ""
    port: int = 22
    new_user: str = ""
    created: bool = False
    log: str = ""
    error: str | None = None


def generate_keypair(private_path: str | Path, *, comment: str) -> str:
    """Generate (or reuse) an ed25519 keypair and return its public key text.

    If ``private_path`` and its ``.pub`` already exist they are reused, so
    re-running provision keeps the same fleet key. Otherwise ``ssh-keygen``
    writes a passphrase-less keypair (private key ``0600``).
    """
    private_path = Path(private_path)
    public_path = private_path.with_name(private_path.name + ".pub")
    if private_path.is_file() and public_path.is_file():
        return public_path.read_text(encoding="utf-8").strip()
    if not have_executable("ssh-keygen"):
        raise YujError(
            "ssh-keygen not found on PATH",
            hint="install openssh-client (it ships ssh-keygen)",
        )
    private_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            comment,
            "-f",
            str(private_path),
        ],
        check=True,
        timeout=30.0,
    )
    return public_path.read_text(encoding="utf-8").strip()


def build_provision_script(cfg: ProvisionConfig, public_key: str) -> str:
    """Render the idempotent bash that creates the user and installs the key.

    The script runs as root (the caller wraps it in ``sudo``). ``new_user`` is
    validated by :class:`ProvisionConfig`; ``public_key`` is validated here.
    """
    if not is_shell_safe_public_key(public_key):
        raise YujError(
            "refusing to install a malformed SSH public key",
            hint="expected an 'ssh-ed25519 AAAA... comment' line from ssh-keygen",
        )
    user = cfg.new_user  # already validated against _VALID_USERNAME
    shell = shlex.quote(cfg.shell)
    create_block = (
        f'echo "[dry-run] would: useradd -m -s {shell} {user}"'
        if cfg.check
        else f"useradd -m -s {shell} {shlex.quote(user)}"
    )
    install_block = (
        'echo "[dry-run] would install ${#PUBKEY}-byte yuj key into'
        ' $NEW_USER authorized_keys"'
        if cfg.check
        else _INSTALL_KEY_BLOCK
    )
    return _SCRIPT_TEMPLATE.format(
        user=user,
        pubkey=public_key,
        create_block=create_block,
        install_block=install_block,
        dry=" (dry-run)" if cfg.check else "",
    )


def provision(
    transport: Transport,
    cfg: ProvisionConfig,
    *,
    public_key: str,
    timeout: float = DEFAULT_PROVISION_TIMEOUT,
) -> ProvisionResult:
    """Provision the host behind ``transport`` (an admin account). Never raises."""
    host = transport.host
    sudo_pw = cfg.sudo_password
    if sudo_pw is None:
        sudo_pw = host.password or ""
    try:
        script = build_provision_script(cfg, public_key)
        # sudo -S reads the password from stdin; -p '' suppresses the prompt so
        # it can't be mistaken for command output. The script is one quoted
        # argument to bash -c.
        remote = f"sudo -S -p '' /bin/bash -c {shlex.quote(script)}"
        result = transport.run(remote, timeout=timeout, input_text=sudo_pw + "\n")
    except YujError as exc:
        return ProvisionResult(
            host=host.name, ok=False, ip=host.ip, port=host.port, error=str(exc)
        )
    if not result.ok:
        return ProvisionResult(
            host=host.name,
            ok=False,
            ip=host.ip,
            port=host.port,
            new_user=cfg.new_user,
            log=result.stdout + result.stderr,
            error=_explain_failure(result.stderr) or f"exit {result.returncode}",
        )
    return ProvisionResult(
        host=host.name,
        ok=True,
        ip=host.ip,
        port=host.port,
        new_user=cfg.new_user,
        created="USER-CREATED" in result.stdout,
        log=result.stdout,
    )


def provision_fleet(
    admin_fleet: Fleet,
    cfg: ProvisionConfig,
    *,
    key_dir: str | Path = DEFAULT_KEY_DIR,
    fleet_out: str | Path = DEFAULT_FLEET_OUT,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_PROVISION_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
    log_dir: str | Path = ".yuj/provision-logs",
) -> dict[str, ProvisionResult]:
    """Provision every host in parallel; write the keypair, logs, and a fleet CSV.

    Returns the per-host results. Outside ``--check``, a ready-to-use fleet CSV
    is written at ``fleet_out`` listing every host that succeeded with its new
    user and ``key_path``, so the output feeds straight into ``yuj bootstrap``.
    """
    if not admin_fleet:
        return {}
    key_dir = Path(key_dir)
    key_dir.mkdir(parents=True, exist_ok=True)
    private_path = (key_dir / f"{cfg.new_user}_ed25519").resolve()
    public_key = generate_keypair(private_path, comment=f"yuj-{cfg.new_user}")

    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    results = map_fleet(
        admin_fleet,
        lambda host: provision(
            make_transport(host, connect_timeout=connect_timeout),
            cfg,
            public_key=public_key,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )
    for name, result in results.items():
        (logs / f"{name}.log").write_text(
            result.log or (result.error or ""), encoding="utf-8"
        )

    if not cfg.check:
        ok = [results[h.name] for h in admin_fleet if results[h.name].ok]
        if ok:
            write_provisioned_fleet(fleet_out, ok, key_path=private_path)
    return results


def write_provisioned_fleet(
    path: str | Path, results: list[ProvisionResult], *, key_path: str | Path
) -> None:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["name", "ip", "username", "key_path", "port"])
        for r in results:
            writer.writerow([r.host, r.ip, r.new_user, str(key_path), r.port])


def _explain_failure(stderr: str) -> str | None:
    low = stderr.lower()
    if "incorrect password" in low or "sorry, try again" in low:
        return "sudo password rejected"
    if "a password is required" in low or "no tty present" in low:
        return "sudo needs a password (pass --sudo-password / --ask-sudo-pass)"
    if "is not in the sudoers" in low or "not allowed to execute" in low:
        return "admin account lacks sudo on this host"
    return stderr.strip() or None


# Installs the yuj public key into the user's authorized_keys, idempotently, with
# correct ownership and perms, and locks the password so only the key works. The
# key line is fixed text (validated by build_provision_script), never user input.
_INSTALL_KEY_BLOCK = """\
home=$(getent passwd "$NEW_USER" | cut -d: -f6)
install -d -m 700 -o "$NEW_USER" -g "$NEW_USER" "$home/.ssh"
auth="$home/.ssh/authorized_keys"
touch "$auth"
grep -qxF "$PUBKEY" "$auth" || echo "$PUBKEY" >> "$auth"
chmod 600 "$auth"
chown "$NEW_USER:$NEW_USER" "$auth"
passwd -l "$NEW_USER" >/dev/null 2>&1 || true"""


# Generated bash, run as root via sudo. {pubkey} is a validated single-line key;
# {user} is a validated Linux username; the blocks are yuj-controlled text.
_SCRIPT_TEMPLATE = """\
#!/usr/bin/env bash
# yuj provision{dry}: generated; safe to re-run.
set -uo pipefail
NEW_USER='{user}'
PUBKEY='{pubkey}'

if id -u "$NEW_USER" >/dev/null 2>&1; then
    echo "USER-EXISTS"
else
    {create_block}
    echo "USER-CREATED"
fi

{install_block}

echo "PROVISION-OK"
"""
