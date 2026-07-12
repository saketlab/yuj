"""Local pre-flight checks for ``yuj deploy``: fail fast before touching hosts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from yuj.config import ProjectConfig


def local_preflight(config: ProjectConfig, *, push_payload: bool) -> list[str]:
    """Return human-readable problems with ``deploy``'s locally-referenced files.

    An empty list means every path ``deploy`` will send is present.
    ``push_payload`` gates the (potentially heavy) ``deploy.payload`` paths so a
    ``--no-payload`` run isn't blocked on data it won't send.
    """
    problems: list[str] = []

    for path in config.deploy.get("code", []):
        if not Path(path).exists():
            problems.append(f"deploy.code path does not exist: {path}")

    if push_payload:
        for path in config.deploy.get("payload", []):
            if not Path(path).exists():
                problems.append(f"deploy.payload path does not exist: {path}")

    return problems
