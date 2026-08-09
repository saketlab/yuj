"""Host and Fleet dataclasses plus CSV/YAML loaders. Passwords stay out of repr/logs."""

from __future__ import annotations

import concurrent.futures
import csv
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from yuj.exceptions import FleetError
from yuj.window import Window

if TYPE_CHECKING:  # sizing depends on status; keep the runtime import one-way
    from yuj.sizing import SizingPlan


@dataclass(frozen=True)
class Host:
    """One opportunistic SSH target.

    Exactly one auth method is expected: a ``key_path`` (preferred) or a
    ``password``. ``weight`` scales how much work this host receives relative to
    its peers (a 96 GB GPU box might be weighted higher than an 8 GB one). Set
    ``strict_host_key`` for internet-facing hosts where known_hosts verification
    should be enforced instead of the default lab-subnet trust model.
    """

    name: str
    ip: str
    user: str
    port: int = 22
    password: str | None = field(default=None, repr=False)
    key_path: str | None = None
    weight: float = 1.0
    do_not_use: bool = False
    window: str | None = None
    # set by submit --autotune; unset means use the job-wide defaults
    concurrency: int | None = None
    gpus: str = ""
    workers_per_gpu: str = ""
    local: bool = False
    strict_host_key: bool = False
    known_hosts_file: str | None = None

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise FleetError(
                f"host {self.name!r} has negative weight {self.weight}",
                hint="weights must be >= 0 (use 0 to drain a host)",
            )
        if self.port <= 0 or self.port > 65535:
            raise FleetError(f"host {self.name!r} has invalid port {self.port}")
        if self.window:
            Window.parse(self.window)  # validate per-host window format early

    @property
    def is_local(self) -> bool:
        """True when this host runs on the controller itself (no SSH).

        Either flagged ``local`` in the fleet, or addressed as loopback.
        """
        return self.local or self.ip in {"localhost", "127.0.0.1", "::1"}

    @property
    def auth_kind(self) -> str:
        """Return ``"key"``, ``"password"``, or ``"none"`` for this host."""
        if self.key_path:
            return "key"
        if self.password:
            return "password"
        return "none"

    @property
    def use_password(self) -> bool:
        """True when this host authenticates by password (needs ``sshpass``)."""
        return self.auth_kind == "password"

    def __repr__(self) -> str:
        # Explicit repr so a password can never leak even if the field flips to
        # repr=True by mistake; mirrors the dataclass fields minus the secret.
        secret = "***" if self.password else None
        return (
            f"Host(name={self.name!r}, ip={self.ip!r}, user={self.user!r}, "
            f"port={self.port}, password={secret!r}, key_path={self.key_path!r}, "
            f"weight={self.weight}, do_not_use={self.do_not_use}, "
            f"window={self.window!r}, local={self.local}, "
            f"strict_host_key={self.strict_host_key}, "
            f"known_hosts_file={self.known_hosts_file!r})"
        )


@dataclass(frozen=True)
class Fleet:
    """An ordered, immutable collection of :class:`Host` targets."""

    hosts: tuple[Host, ...]

    def __post_init__(self) -> None:
        names = [h.name for h in self.hosts]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise FleetError(f"duplicate host names in fleet: {sorted(dupes)}")

    def __len__(self) -> int:
        return len(self.hosts)

    def __iter__(self) -> Iterator[Host]:
        return iter(self.hosts)

    def __bool__(self) -> bool:
        return bool(self.hosts)

    @property
    def names(self) -> tuple[str, ...]:
        """The host names, in fleet order."""
        return tuple(h.name for h in self.hosts)

    @property
    def total_weight(self) -> float:
        """Sum of all host weights."""
        return sum(h.weight for h in self.hosts)

    @property
    def usable(self) -> Fleet:
        """Sub-fleet excluding hosts flagged ``do_not_use``."""
        return Fleet(tuple(h for h in self.hosts if not h.do_not_use))

    def get(self, name: str) -> Host:
        """Return the host named ``name``, or raise :class:`FleetError`."""
        for host in self.hosts:
            if host.name == name:
                return host
        raise FleetError(
            f"no host named {name!r} in fleet",
            hint=f"known hosts: {', '.join(self.names) or '(none)'}",
        )

    def select(self, names: Sequence[str]) -> Fleet:
        """Return a sub-fleet containing only the named hosts, in fleet order."""
        wanted = set(names)
        unknown = wanted - set(self.names)
        if unknown:
            raise FleetError(f"unknown hosts requested: {sorted(unknown)}")
        return Fleet(tuple(h for h in self.hosts if h.name in wanted))

    def with_weights(self, weights: dict[str, float]) -> Fleet:
        """Return a copy of the fleet with the given per-host weights overridden."""
        unknown = set(weights) - set(self.names)
        if unknown:
            raise FleetError(f"weights given for unknown hosts: {sorted(unknown)}")
        return Fleet(
            tuple(replace(h, weight=weights.get(h.name, h.weight)) for h in self.hosts)
        )

    def with_sizing(self, plans: Mapping[str, SizingPlan]) -> Fleet:
        """Return a copy carrying each host's sized worker count and placement."""
        unknown = set(plans) - set(self.names)
        if unknown:
            raise FleetError(f"sizing given for unknown hosts: {sorted(unknown)}")

        def sized(host: Host) -> Host:
            plan = plans.get(host.name)
            if plan is None:
                return host
            return replace(
                host,
                concurrency=plan.total_workers,
                gpus=plan.gpus_env,
                workers_per_gpu=plan.workers_per_gpu_env,
            )

        return Fleet(tuple(sized(h) for h in self.hosts))


def load_from_csv(path: str | Path) -> Fleet:
    """Load a fleet from a ``username,ip,name,password`` CSV.

    Required columns: a user column (``username`` or ``user``), an address column
    (``ip`` or ``host``), and a name column (``name`` or ``machine``). Optional:
    ``password``, ``key_path``, ``weight``, ``port``, ``strict_host_key``, and
    ``known_hosts_file``. Blank lines and rows whose name is empty are skipped.
    """
    path = Path(path)
    if not path.is_file():
        raise FleetError(f"fleet CSV not found: {path}")

    hosts: list[Host] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise FleetError(f"fleet CSV is empty: {path}")
        fields = {(name or "").strip() for name in reader.fieldnames}
        user_col = _first_present(fields, ("username", "user"))
        addr_col = _first_present(fields, ("ip", "host"))
        name_col = _first_present(fields, ("name", "machine"))
        if not (user_col and addr_col and name_col):
            raise FleetError(
                f"fleet CSV {path} missing required columns",
                hint="need a user (username/user), address (ip/host), and name column",
            )
        for lineno, row in enumerate(reader, start=2):
            clean = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
            name = clean.get(name_col, "")
            if not name:
                continue
            hosts.append(
                _row_to_host(clean, user_col, addr_col, name_col, path, lineno)
            )

    if not hosts:
        raise FleetError(f"no usable hosts found in {path}")
    return Fleet(tuple(hosts))


def load_from_yaml(path: str | Path) -> Fleet:
    """Load a fleet from a YAML file with a ``machines:`` list.

    Top-level keys ``user``, ``port``, ``key_path``, and ``weight`` act as
    defaults; each entry under ``machines`` may override them and must supply at
    least ``name`` and ``ip``.
    """
    path = Path(path)
    if not path.is_file():
        raise FleetError(f"fleet YAML not found: {path}")
    try:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise FleetError(f"could not parse fleet YAML {path}: {exc}") from exc
    if not isinstance(data, dict) or "machines" not in data:
        raise FleetError(f"fleet YAML {path} must be a mapping with a 'machines' list")

    defaults = {
        "user": data.get("user"),
        "port": data.get("port", 22),
        "key_path": data.get("key_path"),
        "weight": data.get("weight", 1.0),
        "window": data.get("window"),
        "strict_host_key": data.get("strict_host_key", False),
        "known_hosts_file": data.get("known_hosts_file"),
    }
    machines = data.get("machines") or []
    if not isinstance(machines, list):
        raise FleetError(f"'machines' in {path} must be a list")

    hosts: list[Host] = []
    for idx, entry in enumerate(machines):
        if not isinstance(entry, dict):
            raise FleetError(f"machine #{idx} in {path} is not a mapping")
        name = entry.get("name")
        ip = entry.get("ip") or entry.get("host")
        user = entry.get("user", defaults["user"])
        if not name or not ip or not user:
            raise FleetError(
                f"machine #{idx} in {path} needs name, ip, and user",
                hint="set a top-level 'user:' default or per-machine 'user:'",
            )
        hosts.append(
            Host(
                name=str(name),
                ip=str(ip),
                user=str(user),
                port=int(entry.get("port", defaults["port"])),
                password=entry.get("password"),
                key_path=entry.get("key_path", defaults["key_path"]),
                weight=float(entry.get("weight", defaults["weight"])),
                do_not_use=_truthy(entry.get("do_not_use")),
                window=entry.get("window", defaults["window"]),
                local=_truthy(entry.get("local")),
                strict_host_key=_truthy(
                    entry.get("strict_host_key", defaults["strict_host_key"])
                ),
                known_hosts_file=entry.get(
                    "known_hosts_file", defaults["known_hosts_file"]
                ),
            )
        )
    if not hosts:
        raise FleetError(f"no machines listed in {path}")
    return Fleet(tuple(hosts))


def map_fleet[T](
    fleet: Fleet, fn: Callable[[Host], T], *, max_workers: int
) -> dict[str, T]:
    """Run ``fn(host)`` across the fleet in parallel; return ``{host.name: result}``.

    Workers are capped at ``min(max_workers, len(fleet))``. Exceptions from ``fn``
    propagate from the worker thread, so each caller decides whether to catch
    per-host failures inside ``fn`` (the usual choice) or let one abort the batch.
    """
    if not fleet:
        return {}
    workers = max(1, min(max_workers, len(fleet)))
    out: dict[str, T] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fn, host): host.name for host in fleet}
        for future in concurrent.futures.as_completed(futures):
            out[futures[future]] = future.result()
    return out


def _row_to_host(
    row: dict[str, str],
    user_col: str,
    addr_col: str,
    name_col: str,
    path: Path,
    lineno: int,
) -> Host:
    """Convert one cleaned CSV row into a :class:`Host`."""
    try:
        weight = float(row["weight"]) if row.get("weight") else 1.0
        port = int(row["port"]) if row.get("port") else 22
    except ValueError as exc:
        raise FleetError(f"{path}:{lineno}: bad numeric value ({exc})") from exc
    return Host(
        name=row[name_col],
        ip=row[addr_col],
        user=row[user_col],
        port=port,
        password=row.get("password") or None,
        key_path=row.get("key_path") or None,
        weight=weight,
        do_not_use=_truthy(row.get("do_not_use")),
        window=row.get("window") or None,
        local=_truthy(row.get("local")),
        strict_host_key=_truthy(row.get("strict_host_key")),
        known_hosts_file=row.get("known_hosts_file") or None,
    )


def _truthy(value: str | bool | None) -> bool:
    """Interpret a CSV/YAML cell as a boolean (``true/1/yes/y`` are True)."""
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def _first_present(fields: set[str], candidates: Sequence[str]) -> str | None:
    for candidate in candidates:
        if candidate in fields:
            return candidate
    return None
