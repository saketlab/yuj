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

## A real example: Seurat clustering

`environment.yaml`:
```yaml
name: yuj-r
channels: [conda-forge]
dependencies:
  - r-base>=4.3
  - r-data.table
  - r-seurat
  - r-hdf5r
```

`worker.R`:
```r
.libPaths(c(Sys.getenv("R_LIBS_USER", "~/.yuj-rlib"), .libPaths()))
suppressMessages({
  library(Seurat)
  library(data.table)
})

args    <- commandArgs(trailingOnly = TRUE)
sample  <- args[[1]]
out_dir <- Sys.getenv("YUJ_OUT", "results")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

result <- tryCatch({
  mat <- Read10X_h5(file.path("data", paste0(sample, ".h5")))
  seu <- CreateSeuratObject(mat, project = sample)
  seu <- NormalizeData(seu) |> FindVariableFeatures() |>
         ScaleData() |> RunPCA() |> FindNeighbors() |> FindClusters()
  data.table(sample = sample, n_cells = ncol(seu),
             n_clusters = max(seu$seurat_clusters) + 1)
}, error = function(e) {
  data.table(sample = sample, n_cells = NA, error = conditionMessage(e))
})

fwrite(result, file.path(out_dir, paste0(sample, ".csv")))
```

To push the h5 files as payload:
```yaml
# yuj.yaml
deploy:
  code: [worker.R, items.txt, r-packages.txt]
  payload: [data/]          # rsync the h5 files to each host
```

```bash
yuj bootstrap
yuj deploy       # transfers data/ + worker.R to every host
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
