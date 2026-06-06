"""Scaffold templates for ``yuj init --template {bare,python,r}``."""

from __future__ import annotations

from yuj.exceptions import YujError

TEMPLATES = ("bare", "python", "r")

_FLEET_CSV = """\
# yuj fleet inventory: one opportunistic SSH target per row.
# Columns: username,ip,name,password   (password optional; prefer key_path)
# Optional extra columns: key_path,weight,port,do_not_use
# KEEP THIS FILE OUT OF VERSION CONTROL; it holds credentials.
username,ip,name,password
you,10.0.0.1,lab-desk-1,
you,10.0.0.2,lab-desk-2,
"""

_ITEMS = "".join(f"item{i}\n" for i in range(1, 6))

_BARE_YAML = """\
# yuj.yaml: project defaults for the yuj CLI.
fleet: fleet.csv
job: mybatch
remote_dir: yuj-run
results_glob: ~/yuj-run/results/*
stall_min: 90
# work_command receives the item as its last argument on each invocation:
work_command: bash $HOME/yuj-run/worker.sh
# input_file: items.txt  # one item per line; each item is passed as $1
# output_dir: results    # relative to remote_dir; resume skips done items
# output_suffix: .out    # output filename = <item><output_suffix>
deploy:
  code: [worker.sh]   # synced on every deploy
  payload: []         # heavy data, e.g. [pdfs/, data/]; skip re-send with --no-payload
"""

_PYTHON_YAML = """\
# yuj.yaml: Python job. Flow: yuj bootstrap → deploy → submit → status → pull
fleet: fleet.csv
job: mypyjob
remote_dir: yuj-run
results_glob: ~/yuj-run/results/*.out
stall_min: 30
work_command: python3 $HOME/yuj-run/worker.py
input_file: items.txt
output_dir: results
output_suffix: .out
bootstrap:
  env_manager: uv          # installs uv (no root) and a venv
  python: "3.12"
deploy:
  code: [worker.py, items.txt]  # synced on every deploy
  payload: []   # heavy data, e.g. [models/, data/]; skip re-send with --no-payload
"""

_PYTHON_WORKER = '''\
#!/usr/bin/env python3
"""yuj Python worker: process ONE item, write its output. Must be resume-safe
(yuj skips items whose output already exists). The item is the last argument."""

import os
import sys
from pathlib import Path


def main() -> None:
    item = sys.argv[1]
    out_dir = Path(os.environ.get("YUJ_OUT", "results"))
    out_dir.mkdir(parents=True, exist_ok=True)
    # --- replace this with your real work ---
    (out_dir / f"{item}.out").write_text(f"processed {item}\\n")


if __name__ == "__main__":
    main()
'''

_R_YAML = """\
# yuj.yaml: R job. Flow: yuj bootstrap → deploy → submit → status → pull
fleet: fleet.csv
job: myrjob
remote_dir: yuj-run
results_glob: ~/yuj-run/results/*.csv
stall_min: 30
work_command: Rscript $HOME/yuj-run/worker.R
input_file: items.txt
output_dir: results
output_suffix: .csv
bootstrap:
  # micromamba installs R (r-base) and conda-forge R packages with NO root and
  # NO compiler setup. The R extra then installs any extra CRAN packages listed
  # in r-packages.txt into ~/.yuj-rlib.
  env_manager: micromamba
  env_file: environment.yaml
  extras: [R]
deploy:
  code: [worker.R, items.txt, r-packages.txt]  # synced on every deploy
  payload: []   # heavy data, e.g. [data/]; skip re-send with --no-payload
"""

_R_WORKER = """\
#!/usr/bin/env Rscript
# yuj R worker: process ONE item, write its output. Must be resume-safe
# (yuj skips items whose output already exists). The item is the last argument.

args <- commandArgs(trailingOnly = TRUE)
item <- args[[1]]
outdir <- Sys.getenv("YUJ_OUT", "results")
dir.create(outdir, showWarnings = FALSE, recursive = TRUE)

# Example using a package from environment.yaml; replace with your analysis.
suppressMessages(library(data.table))
dt <- data.table(item = item, n_char = nchar(item))
fwrite(dt, file.path(outdir, paste0(item, ".csv")))
"""

_ENVIRONMENT_YAML = """\
# Conda/micromamba environment for the R job. conda-forge has thousands of
# r-* packages (no compiling, no root). Add what your worker needs here.
name: yuj-r
channels: [conda-forge]
dependencies:
  - r-base>=4.3
  - r-data.table
"""

_R_PACKAGES = """\
# Extra CRAN packages installed by the `R` bootstrap extra into ~/.yuj-rlib.
# One package per line; lines starting with '#' are ignored. Prefer adding
# r-<name> to environment.yaml (conda-forge) when available; it's faster and
# needs no compiler. Use this file for CRAN-only packages.
# jsonlite
# stringr
"""

_BARE_WORKER = """\
#!/usr/bin/env bash
# yuj worker: process ONE item ($1), write its output. Must be resume-safe.
set -euo pipefail
item="$1"
out_dir="${YUJ_OUT:-results}"
mkdir -p "$out_dir"
# --- replace this with your real work ---
echo "processed $item" > "$out_dir/$item.out"
"""


def scaffold_files(template: str) -> dict[str, str]:
    """Return ``{filename: contents}`` for ``yuj init --template <template>``."""
    if template not in TEMPLATES:
        raise YujError(
            f"unknown template {template!r}",
            hint=f"choose one of: {', '.join(TEMPLATES)}",
        )
    files = {"fleet.csv": _FLEET_CSV}
    if template == "bare":
        files["yuj.yaml"] = _BARE_YAML
        files["worker.sh"] = _BARE_WORKER
    elif template == "python":
        files["yuj.yaml"] = _PYTHON_YAML
        files["worker.py"] = _PYTHON_WORKER
        files["items.txt"] = _ITEMS
    elif template == "r":
        files["yuj.yaml"] = _R_YAML
        files["worker.R"] = _R_WORKER
        files["items.txt"] = _ITEMS
        files["environment.yaml"] = _ENVIRONMENT_YAML
        files["r-packages.txt"] = _R_PACKAGES
    return files
