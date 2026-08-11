"""Scaffold templates for ``yuj init --template {bare,python,r,r-python}``."""

from __future__ import annotations

from yuj.exceptions import YujError

TEMPLATES = ("bare", "python", "r", "r-python")

_FLEET_CSV = """\
# yuj fleet inventory: one opportunistic SSH target per row.
# Columns: username,ip,name,password   (password optional; prefer key_path)
# Optional extra columns: key_path,weight,port,do_not_use,window,local
#   window: per-host run window "HH:MM-HH:MM" (wraps midnight), e.g. 19:00-09:30
#   for a borrowed desktop usable only at night. Blank = always on; omit the
#   column entirely to inherit active_window from yuj.yaml.
#   local: true if this host IS the controller (runs work directly, no SSH);
#   also auto-detected when ip is localhost/127.0.0.1.
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
# active_window: "19:00-09:30"   # job-wide run window; per-host 'window' in
#                                # fleet.csv overrides it (e.g. always-on boxes).
# off_window_command: "pkill -f myserver"  # extra cleanup when leaving the window
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

_RPY_YAML = """\
# yuj.yaml: R + Python job using pyreadr and rpy2.
# Flow: yuj deploy → yuj bootstrap → submit → status → pull  (deploy first so
# bootstrap sees environment.yaml / requirements.txt on each host).
fleet: fleet.csv
job: myrpyjob
remote_dir: yuj-run
results_glob: ~/yuj-run/results/*.csv
stall_min: 30
work_command: bash $HOME/yuj-run/worker.sh
input_file: items.txt
output_dir: results
output_suffix: .csv
bootstrap:
  # uv builds the venv from requirements.txt; the micromamba extra builds the
  # conda-forge env from environment.yaml. worker.sh calls each interpreter.
  env_manager: uv
  python: "3.12"
  env_file: requirements.txt
  extras: [micromamba]
deploy:
  code: [worker.sh, worker.py, worker_rpy2.py, worker.R, items.txt,
         requirements.txt, environment.yaml]
  payload: []   # heavy data, e.g. [data/]; skip re-send with --no-payload
"""

_RPY_WORKER_SH = """\
#!/usr/bin/env bash
# yuj R + Python worker for ONE item ($1). Resume-safe: yuj skips items whose
# results/<item>.csv already exists.
#   1. R (micromamba) writes an .rds
#   2. pyreadr (uv venv) reads it
#   3. rpy2 (micromamba env) calls R in-process
set -euo pipefail
item="$1"
micromamba run -n yuj-rpy Rscript worker.R "$item"
python worker.py "$item"
micromamba run -n yuj-rpy python worker_rpy2.py "$item"
"""

_RPY_WORKER_R = """\
#!/usr/bin/env Rscript
# yuj step 1 (R via micromamba): write work/<item>.rds for the Python side.
args <- commandArgs(trailingOnly = TRUE)
item <- args[[1]]
dir.create("work", showWarnings = FALSE)
suppressMessages(library(data.table))
saveRDS(data.table(item = item, n_char = nchar(item)),
        file.path("work", paste0(item, ".rds")))
"""

_RPY_WORKER_PY = '''\
#!/usr/bin/env python3
"""yuj step 2 (pyreadr, uv venv): read work/<item>.rds, write results/<item>.csv."""

import os
import sys
from pathlib import Path

import pyreadr


def main() -> None:
    item = sys.argv[1]
    out = Path(os.environ.get("YUJ_OUT", "results"))
    out.mkdir(parents=True, exist_ok=True)
    df = pyreadr.read_r(f"work/{item}.rds")[None]  # None key = the unnamed object
    df.to_csv(out / f"{item}.csv", index=False)


if __name__ == "__main__":
    main()
'''

_RPY_WORKER_RPY2 = '''\
#!/usr/bin/env python3
"""yuj rpy2 demo (micromamba env): drive a live R session from Python.

Unlike worker.py (pyreadr, which just reads the file), this runs real R: it calls
R's own readRDS, then any R function on the result. Run it with the micromamba
env's Python, where rpy2 is linked to R: `micromamba run -n yuj-rpy python ...`.
"""

import sys

import rpy2.robjects as ro


def main() -> None:
    item = sys.argv[1]
    dt = ro.r["readRDS"](f"work/{item}.rds")  # R reads its own .rds
    n_rows = ro.r["nrow"](dt)[0]  # call any R function on the R object
    print(f"rpy2: {item}.rds has {n_rows} row(s)")


if __name__ == "__main__":
    main()
'''

_RPY_ENVIRONMENT_YAML = """\
# conda-forge env for the R side (no root, no compiler). rpy2 goes here, not in
# requirements.txt: conda-forge links it to this env's R, so it imports cleanly.
# Pip-installing rpy2 against a separate R gives R_HOME / libR.so errors.
# The name is what worker.sh passes to `micromamba run -n`.
name: yuj-rpy
channels: [conda-forge]
dependencies:
  - r-base>=4.3
  - r-data.table
  - rpy2
"""

_RPY_REQUIREMENTS = """\
# Pure-Python deps for the uv venv. pyreadr reads .rds/.RData with no R.
# R-linked packages like rpy2 go in environment.yaml instead.
pyreadr
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
    elif template == "r-python":
        files["yuj.yaml"] = _RPY_YAML
        files["worker.sh"] = _RPY_WORKER_SH
        files["worker.py"] = _RPY_WORKER_PY
        files["worker_rpy2.py"] = _RPY_WORKER_RPY2
        files["worker.R"] = _RPY_WORKER_R
        files["items.txt"] = _ITEMS
        files["environment.yaml"] = _RPY_ENVIRONMENT_YAML
        files["requirements.txt"] = _RPY_REQUIREMENTS
    return files
