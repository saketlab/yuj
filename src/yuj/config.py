"""Typed project configuration parsed from ``yuj.yaml``.

:class:`ProjectConfig` declares the settings schema and its defaults in one
place; ``from_mapping`` is the single point that reads the loaded YAML, so no
command re-derives key names or defaults from a raw dict.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from yuj.status import DEFAULT_STALL_MIN

DEFAULT_RESULTS_GLOB = "~/*"
DEFAULT_REMOTE_DIR = "yuj-run"
DEFAULT_JOB = "yuj"


def _opt_str(value: Any) -> str | None:
    """Coerce a truthy value to ``str``; treat empty/absent as ``None``."""
    return str(value) if value else None


def _flag(value: Any) -> bool:
    """Coerce a YAML scalar to bool; ``"false"``/``"no"``/``"0"`` stay False."""
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


def _section(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    """Return ``data[key]`` as a plain dict, or ``{}`` if missing/not a mapping."""
    value = data.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class ProjectConfig:
    """The settings in ``yuj.yaml``, with defaults applied once at parse time.

    Scalar fields are the job-wide settings every command shares. The
    operation-specific sections (``deploy``/``scatter``/``authorize``/
    ``bootstrap``/``sizing``) are kept as raw mappings, since each command parses
    its own section into its own typed config (``DeployPlan``, ``WorkerProfile``,
    …).
    """

    fleet: str | None = None
    job: str = DEFAULT_JOB
    remote_dir: str = DEFAULT_REMOTE_DIR
    results_glob: str = DEFAULT_RESULTS_GLOB
    stall_min: int = DEFAULT_STALL_MIN
    work_command: str | None = None
    input_file: str | None = None
    output_dir: str | None = None
    output_suffix: str = ""
    active_window: str | None = None
    off_window_command: str | None = None
    concurrency: int = 1
    courtesy: bool = False
    deploy: Mapping[str, Any] = field(default_factory=dict)
    scatter: Mapping[str, Any] = field(default_factory=dict)
    authorize: Mapping[str, Any] = field(default_factory=dict)
    bootstrap: Mapping[str, Any] = field(default_factory=dict)
    sizing: Mapping[str, Any] = field(default_factory=dict)

    @property
    def input_source(self) -> str | None:
        """The work-list path, with ``scatter.input`` overriding ``input_file``."""
        return self.scatter.get("input") or self.input_file

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ProjectConfig:
        """Build a :class:`ProjectConfig` from a loaded ``yuj.yaml`` mapping."""
        return cls(
            fleet=_opt_str(data.get("fleet")),
            job=str(data.get("job", DEFAULT_JOB)),
            remote_dir=str(data.get("remote_dir", DEFAULT_REMOTE_DIR)),
            results_glob=str(data.get("results_glob", DEFAULT_RESULTS_GLOB)),
            stall_min=int(data.get("stall_min", DEFAULT_STALL_MIN)),
            work_command=_opt_str(data.get("work_command")),
            input_file=_opt_str(data.get("input_file")),
            output_dir=_opt_str(data.get("output_dir")),
            output_suffix=str(data.get("output_suffix", "")),
            active_window=_opt_str(data.get("active_window")),
            off_window_command=_opt_str(data.get("off_window_command")),
            concurrency=int(data.get("concurrency", 1)),
            courtesy=_flag(data.get("courtesy")),
            deploy=_section(data, "deploy"),
            scatter=_section(data, "scatter"),
            authorize=_section(data, "authorize"),
            bootstrap=_section(data, "bootstrap"),
            sizing=_section(data, "sizing"),
        )
