# yuj example: R analysis

Run an R script per item across borrowed machines — **no root, no pre-installed
R**. yuj bootstraps R via [micromamba](https://mamba.readthedocs.io/) and
installs packages from conda-forge and CRAN.

```bash
$EDITOR fleet.csv      # your hosts
yuj bootstrap          # install micromamba + R + packages (once, idempotent)
yuj deploy             # copy worker.R + items.txt + r-packages.txt
yuj submit             # start the watchdog (runs: Rscript worker.R <item>)
yuj status --watch 30
yuj pull --loop 60     # results land in central/<item>.csv
```

## Files

`fleet.csv` (hosts) · `yuj.yaml` (job config) · `environment.yaml` (conda R env)
· `r-packages.txt` (extra CRAN packages) · `worker.R` (your analysis) ·
`items.txt` (one item per line).

## Customising

Edit `worker.R`. The item is `commandArgs(trailingOnly = TRUE)[[1]]`; write to
`file.path(Sys.getenv("YUJ_OUT", "results"), paste0(item, ".csv"))` and wrap
per-item work in `tryCatch`. Add packages to `environment.yaml` (preferred) or
`r-packages.txt`, then re-run `yuj bootstrap`.
