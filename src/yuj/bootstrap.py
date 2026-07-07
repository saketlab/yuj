"""Install an env manager and runtime extras on a host from a bare login shell."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from yuj.exceptions import YujError
from yuj.fleet import Fleet, map_fleet
from yuj.shell_safety import validate_remote_path
from yuj.transport import Transport, make_transport


@dataclass(frozen=True)
class _ManagerSpec:
    """How to detect and install one environment manager."""

    check: str
    install: str
    bindir: str


ENV_MANAGERS: dict[str, _ManagerSpec] = {
    "uv": _ManagerSpec(
        check="uv --version",
        install="curl -LsSf https://astral.sh/uv/install.sh | sh",
        bindir="$HOME/.local/bin",
    ),
    "pixi": _ManagerSpec(
        check="pixi --version",
        install="curl -fsSL https://pixi.sh/install.sh | bash",
        bindir="$HOME/.pixi/bin",
    ),
    "micromamba": _ManagerSpec(
        check="micromamba --version",
        install="curl -L micro.mamba.pm/install.sh | bash",
        bindir="$HOME/.local/bin",
    ),
    "conda": _ManagerSpec(
        check="conda --version",
        install=(
            'test -d "$HOME/miniforge3" || '
            "( curl -fsSL -o /tmp/miniforge.sh "
            "https://github.com/conda-forge/miniforge/releases/latest/download/"
            "Miniforge3-Linux-x86_64.sh && "
            'bash /tmp/miniforge.sh -b -p "$HOME/miniforge3" )'
        ),
        bindir="$HOME/miniforge3/bin",
    ),
}

MARKER_NAME = ".yuj-bootstrap-marker"
DEFAULT_BOOTSTRAP_TIMEOUT = 1800.0
DEFAULT_MAX_WORKERS = 4


@dataclass(frozen=True)
class BootstrapConfig:
    """What to install on each host.

    Args:
        env_manager: One of ``uv``/``pixi``/``micromamba``/``conda``.
        python: Python version for the materialized environment.
        extras: Named recipe bundles to run (e.g. ``("OLLAMA",)``).
        env_file: Optional spec file (``requirements.txt``/``pyproject.toml``/
            ``environment.yaml``/``pixi.toml``) to materialize an env from.
        remote_dir: Deploy directory on the host, relative to ``$HOME``.
        from_tarball: Optional pre-downloaded installer/env tarball on the host
            (for offline compute nodes); extracted instead of curl-installing.
        check: Dry-run; report what would happen, install nothing.
    """

    env_manager: str = "uv"
    python: str = "3.12"
    extras: tuple[str, ...] = ()
    env_file: str | None = None
    remote_dir: str = "yuj-run"
    from_tarball: str | None = None
    check: bool = False

    def __post_init__(self) -> None:
        if self.env_manager not in ENV_MANAGERS:
            raise YujError(
                f"unknown env manager {self.env_manager!r}",
                hint=f"choose one of: {', '.join(ENV_MANAGERS)}",
            )
        validate_remote_path(self.remote_dir, label="remote_dir")
        if self.from_tarball:
            validate_remote_path(self.from_tarball, label="--from-tarball path")


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of bootstrapping one host."""

    host: str
    ok: bool
    os_pretty: str | None = None
    already_done: bool = False
    log: str = ""
    error: str | None = None


def load_recipe(name: str) -> str:
    """Return the bash for a named extra (case-insensitive), or raise."""
    filename = f"{name.lower()}.sh"
    try:
        files = resources.files("yuj.bootstrap_recipes")
        recipe = files / filename
        return recipe.read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        available = ", ".join(sorted(available_extras()))
        raise YujError(
            f"unknown bootstrap extra {name!r}",
            hint=f"available: {available} (or add {filename} to bootstrap_recipes/)",
        ) from exc


def available_extras() -> list[str]:
    """List the extra bundles shipped with yuj (upper-cased names)."""
    files = resources.files("yuj.bootstrap_recipes")
    return [p.name[:-3].upper() for p in files.iterdir() if p.name.endswith(".sh")]


def build_bootstrap_script(cfg: BootstrapConfig) -> str:
    """Render the idempotent bootstrap bash script for ``cfg``."""
    spec = ENV_MANAGERS[cfg.env_manager]
    remote = shlex.quote(cfg.remote_dir)
    marker = f"$HOME/{cfg.remote_dir}/{MARKER_NAME}"
    recipes = "\n".join(
        f'echo "== extra: {name} =="\n{load_recipe(name)}' for name in cfg.extras
    )
    install_block = (
        f'echo "[dry-run] would install {cfg.env_manager} via: {spec.install}"'
        if cfg.check
        else spec.install
    )
    tarball = ""
    if cfg.from_tarball and not cfg.check:
        validate_remote_path(cfg.from_tarball, label="--from-tarball path")
        # Double quotes allow $HOME expansion without word splitting.
        tarball = (
            f'if [ -f "{cfg.from_tarball}" ]; then '
            f'echo "extracting {cfg.from_tarball}"; '
            f'tar -xf "{cfg.from_tarball}" -C "$HOME"; fi'
        )
    env_materialize = _env_materialize(cfg) if not cfg.check else "true"
    env_sh = _env_sh_body(cfg, spec)
    return _SCRIPT_TEMPLATE.format(
        manager=cfg.env_manager,
        check_cmd=spec.check,
        install_block=install_block,
        bindir=spec.bindir,
        remote=remote,
        remote_dir=cfg.remote_dir,
        marker=marker,
        tarball=tarball,
        env_materialize=env_materialize,
        recipes=recipes,
        env_sh=env_sh,
        dry=" (dry-run)" if cfg.check else "",
        write_marker=(
            "true"
            if cfg.check
            else f'date "+bootstrapped %F %T {cfg.env_manager}" > "$MARKER"'
        ),
    )


def bootstrap(
    transport: Transport,
    cfg: BootstrapConfig,
    *,
    timeout: float = DEFAULT_BOOTSTRAP_TIMEOUT,
) -> BootstrapResult:
    """Bootstrap the host behind ``transport``. Never raises for op failures."""
    host = transport.host.name
    try:
        os_pretty = _detect_os(transport, timeout=min(timeout, 30.0))
        script = build_bootstrap_script(cfg)
        result = transport.run(script, timeout=timeout)
    except YujError as exc:
        return BootstrapResult(host=host, ok=False, error=str(exc))
    already = "ALREADY-BOOTSTRAPPED" in result.stdout
    if not result.ok:
        return BootstrapResult(
            host=host,
            ok=False,
            os_pretty=os_pretty,
            log=result.stdout + result.stderr,
            error=result.stderr.strip() or f"exit {result.returncode}",
        )
    return BootstrapResult(
        host=host,
        ok=True,
        os_pretty=os_pretty,
        already_done=already,
        log=result.stdout,
    )


def bootstrap_fleet(
    fleet: Fleet,
    cfg: BootstrapConfig,
    *,
    connect_timeout: int = 20,
    timeout: float = DEFAULT_BOOTSTRAP_TIMEOUT,
    max_workers: int = DEFAULT_MAX_WORKERS,
    log_dir: str | Path = ".yuj/bootstrap-logs",
) -> dict[str, BootstrapResult]:
    """Bootstrap every host in parallel (capped), one log file per host."""
    if not fleet:
        return {}
    logs = Path(log_dir)
    logs.mkdir(parents=True, exist_ok=True)
    results = map_fleet(
        fleet,
        lambda host: bootstrap(
            make_transport(host, connect_timeout=connect_timeout),
            cfg,
            timeout=timeout,
        ),
        max_workers=max_workers,
    )
    for name, result in results.items():
        (logs / f"{name}.log").write_text(
            result.log or (result.error or ""), encoding="utf-8"
        )
    return results


def _detect_os(transport: Transport, *, timeout: float) -> str | None:
    """Return the host's PRETTY_NAME (distro) or None."""
    result = transport.run(
        ". /etc/os-release 2>/dev/null; echo $PRETTY_NAME", timeout=timeout
    )
    name = result.stdout.strip()
    return name or None


def _env_materialize(cfg: BootstrapConfig) -> str:
    """Bash that materializes an environment for the chosen manager."""
    if cfg.env_manager == "uv":
        venv = (
            f'uv venv --python {shlex.quote(cfg.python)} "$HOME/{cfg.remote_dir}/.venv"'
        )
        if cfg.env_file and cfg.env_file.endswith(("requirements.txt", ".txt")):
            ef = shlex.quote(cfg.env_file)
            return (
                f"{venv} && uv pip install --python "
                f'"$HOME/{cfg.remote_dir}/.venv" -r {ef}'
            )
        return venv
    if cfg.env_manager == "pixi" and cfg.env_file:
        return "pixi install"
    if cfg.env_file and cfg.env_file.endswith((".yaml", ".yml")):
        ef = shlex.quote(cfg.env_file)
        return f"{cfg.env_manager} env create -y -f {ef} || true"
    return "true"


def _env_sh_body(cfg: BootstrapConfig, spec: _ManagerSpec) -> str:
    """Lines for the env.sh that the watchdog sources before each run."""
    lines = [f'export PATH="{spec.bindir}:$HOME/.local/bin:$PATH"']
    if cfg.env_manager == "uv":
        venv = f"$HOME/{cfg.remote_dir}/.venv"
        lines.append(f'[ -d "{venv}" ] && . "{venv}/bin/activate"')
    extras_upper = {e.upper() for e in cfg.extras}
    if "OLLAMA" in extras_upper:
        lines.append('export OLLAMA_HOME="$HOME/.ollama"')
        lines.append('export PATH="$HOME/ollama/bin:$PATH"')
    if "R" in extras_upper:
        lines.append('export R_LIBS_USER="$HOME/.yuj-rlib"')
    if "MICROMAMBA" in extras_upper:
        prefix = "${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"
        lines.append(f'export MAMBA_ROOT_PREFIX="{prefix}"')
    return "\n".join(lines)


_SCRIPT_TEMPLATE = """\
#!/usr/bin/env bash
# yuj bootstrap{dry}: generated; safe to re-run.
set -uo pipefail
mkdir -p "$HOME/{remote}"
MARKER="{marker}"
# Put the manager's bindir on PATH *before* the idempotency check, since a
# non-interactive ssh shell won't have ~/.local/bin etc. on PATH by default.
export PATH="{bindir}:$HOME/.local/bin:$PATH"

if [ -f "$MARKER" ] && {check_cmd} >/dev/null 2>&1; then
    echo "ALREADY-BOOTSTRAPPED ($(cat "$MARKER"))"
    exit 0
fi

{tarball}

if {check_cmd} >/dev/null 2>&1; then
    echo "env manager {manager} present: $({check_cmd} 2>&1 | head -1)"
else
    echo "installing env manager {manager}..."
    {install_block}
    export PATH="{bindir}:$HOME/.local/bin:$PATH"
fi

echo "materializing environment..."
{env_materialize}

# Recipes run with cwd = the deploy dir and YUJ_REMOTE_DIR set, so they can find
# deployed files like r-packages.txt.
export YUJ_REMOTE_DIR="$HOME/{remote}"
cd "$HOME/{remote}" || true
{recipes}

echo "writing env.sh..."
cat > "$HOME/{remote}/env.sh" <<'YUJ_ENV_SH'
# yuj environment, sourced by the watchdog before every run. Generated.
{env_sh}
YUJ_ENV_SH

{write_marker}
echo "BOOTSTRAP-OK"
"""
