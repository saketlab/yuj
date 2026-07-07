"""Result value objects for host probing and diagnosis.

These are the typed results :mod:`yuj.probe` produces and the CLI and renderer
consume. They live in their own module so presentation code can depend on the
types without importing the probe operation itself.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_STALL_MIN = 90


@dataclass(frozen=True)
class HostStatus:
    """A point-in-time snapshot of one host."""

    name: str
    ip: str
    reachable: bool
    hostname: str | None = None
    cpu_model: str | None = None
    nproc: int | None = None
    mem_gb: int | None = None
    gpu: str | None = None
    load1: float | None = None
    n_outputs: int | None = None
    newest_age_min: int | None = None
    watchdog_running: bool = False
    cron_installed: bool = False
    console_user: str | None = None
    error: str | None = None
    excluded: bool = False

    @property
    def owner_present(self) -> bool:
        """True if a human appears to be logged in at the console (be polite!)."""
        return bool(self.console_user)

    def state(self, stall_threshold_min: int = DEFAULT_STALL_MIN) -> str:
        """Classify: excluded/down/producing/stalled/dead/idle."""
        if self.excluded:
            return "excluded"
        if not self.reachable:
            return "down"
        age = self.newest_age_min
        if age is not None and age < stall_threshold_min:
            return "producing"
        if self.watchdog_running:
            return "stalled"
        if self.cron_installed:
            return "dead"
        if self.n_outputs:
            return "stalled"
        return "idle"


@dataclass(frozen=True)
class Diagnosis:
    """Why a host can't be reached, classified by failure stage.

    ``status`` is one of: ``ok`` (auth succeeded), ``auth_refused`` (reachable,
    SSH speaks, but credentials rejected, often fail2ban after repeated tries),
    ``banner_fail`` (TCP connects but no SSH banner, a classic fail2ban drop),
    ``sshd_down`` (port closed / connection refused), ``net_down`` (no route /
    timeout), or ``unknown``.
    """

    name: str
    ip: str
    status: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True only when authentication succeeded."""
        return self.status == "ok"
