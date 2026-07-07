# R users

You don't need root access, a pre-installed R, or a cluster admin. yuj bootstraps R via [micromamba](https://mamba.readthedocs.io/) (a small conda implementation) and installs your packages from conda-forge and CRAN.

## How it works

```
laptop (yuj init --template r)
  │
  ├── yuj bootstrap ──→ installs micromamba on each host (no root)
  │                      micromamba creates the conda env (r-base, r-data.table, …)
  │                      R extra installs CRAN packages into ~/.yuj-rlib
  │
  ├── yuj deploy    ──→ copies worker.R, items.txt, r-packages.txt
  │
  ├── yuj submit    ──→ watchdog calls  Rscript worker.R <item>  per item
  │                      skips items whose .csv already exists
  │
  └── yuj pull      ──→ gathers all *.csv back to central/
```

## Getting started

```bash
yuj init --template r
$EDITOR fleet.csv           # add host IPs + passwords (or key paths)
yuj bootstrap               # install R on every host (~5 min first time, no-op after)
yuj deploy && yuj submit
yuj status --watch 30
yuj pull --loop 60
```

No SSHing in, no `conda activate`, no sudo.

## What gets created

`yuj init --template r` creates six files:

| File | Edit |
|------|------|
| `fleet.csv` | Your borrowed hosts |
| `yuj.yaml` | Usually fine as-is |
| `worker.R` | Replace the analysis block |
| `items.txt` | Replace with your input items |
| `environment.yaml` | Add conda-forge R packages |
| `r-packages.txt` | Add CRAN-only packages |

## Writing your worker

The item is the last command-line argument:

```r
args <- commandArgs(trailingOnly = TRUE)
item <- args[[1]]
```

Write output to `$YUJ_OUT/<item>.csv` (yuj sets `$YUJ_OUT`):

```r
out_dir <- Sys.getenv("YUJ_OUT", "results")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
out <- file.path(out_dir, paste0(item, ".csv"))
fwrite(result_dt, out)
```

Catch errors per-item so one failure doesn't abort the whole batch:

```r
result <- tryCatch({
  data.table(item = item, value = compute(item))
}, error = function(e) {
  data.table(item = item, value = NA, error = conditionMessage(e))
})
fwrite(result, out)
```

## Installing packages

### conda-forge (preferred)

Add packages to `environment.yaml`. conda-forge has pre-built binaries for most popular R packages, so there's no compiler or source build:

```yaml
dependencies:
  - r-base>=4.3
  - r-tidyverse
  - r-biocmanager
  - r-seurat
  - bioconductor-deseq2
```

Then `yuj bootstrap` to pick up new packages (only installs what's new).

### CRAN via r-packages.txt

For packages not on conda-forge:

```text
scMerge
tradeSeq
```

yuj's R extra installs these into `~/.yuj-rlib` on each host. Your worker already loads this library:

```r
.libPaths(c(Sys.getenv("R_LIBS_USER", "~/.yuj-rlib"), .libPaths()))
```

## Calling R from Python (pyreadr and rpy2)

`yuj init --template r-python` scaffolds a job that runs R and Python together.
For example, consider [pyreadr](https://pypi.org/project/pyreadr/) for reading
R libraries in python and [rpy2](https://pypi.org/project/rpy2/)
```bash
micromamba run -n yuj-rpy Rscript worker.R "$item"      # R writes work/<item>.rds
python worker.py "$item"                                # uv venv: pyreadr reads it
micromamba run -n yuj-rpy python worker_rpy2.py "$item" # env python: rpy2 runs R
```

### The worked example

**`environment.yaml`** — R and rpy2 from conda-forge:
```yaml
name: yuj-rpy
channels: [conda-forge]
dependencies:
  - r-base>=4.3
  - r-data.table
  - rpy2
```

**`requirements.txt`** — pyreadr in the venv:
```text
pyreadr
```

**`worker.R`** — R produces an `.rds` for the item:
```r
args <- commandArgs(trailingOnly = TRUE)
item <- args[[1]]
dir.create("work", showWarnings = FALSE)
suppressMessages(library(data.table))
saveRDS(data.table(item = item, n_char = nchar(item)),
        file.path("work", paste0(item, ".rds")))
```

**`worker.py`** — pyreadr reads that `.rds` with no R involved, writes the result:
```python
import os, sys
from pathlib import Path
import pyreadr

item = sys.argv[1]
out = Path(os.environ.get("YUJ_OUT", "results"))
out.mkdir(parents=True, exist_ok=True)
df = pyreadr.read_r(f"work/{item}.rds")[None]  # None key = the unnamed object
df.to_csv(out / f"{item}.csv", index=False)
```

**`worker_rpy2.py`** — rpy2 does the same read *through R*, then calls an R function
on the result (run it with the micromamba env's Python):
```python
import sys
import rpy2.robjects as ro

item = sys.argv[1]
dt = ro.r["readRDS"](f"work/{item}.rds")  # R reads its own .rds
n_rows = ro.r["nrow"](dt)[0]              # call any R function on the R object
print(f"rpy2: {item}.rds has {n_rows} row(s)")
```

Run the job (deploy first, so bootstrap sees the env files on each host):
```bash
yuj deploy       # send worker.*, environment.yaml, requirements.txt
yuj bootstrap    # build the uv venv (pyreadr) and the micromamba env (R + rpy2)
yuj submit
yuj pull
```

## Combining results on the controller

```r
library(data.table)
results <- rbindlist(
  lapply(list.files("central", "\\.csv$", full.names = TRUE), fread),
  fill = TRUE          # handle error rows with missing columns
)
print(results)
```

## Troubleshooting

**"Rscript not found"**
: R wasn't bootstrapped on that host, or the `env.sh` PATH is wrong. Re-run `yuj bootstrap` and check `yuj status`.

**"there is no package called 'X'"**
: Add `r-X` to `environment.yaml` (preferred) or `X` to `r-packages.txt`, then `yuj bootstrap` again.

**"cannot allocate vector of size N"**
: The item needs more RAM than available. Reduce chunk size or mark that host `do_not_use: true` in `fleet.csv`.

**Host shows `● stalled`**
: The watchdog is up but output has gone stale. yuj will restart the run-loop after `stall_min` minutes. Run `yuj diagnose` to see what's happening on the host.
