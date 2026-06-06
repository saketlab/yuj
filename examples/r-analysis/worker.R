#!/usr/bin/env Rscript
# yuj R worker, run ONE analysis, write ONE output file.
#
# yuj calls this as:  Rscript worker.R <item>
# and skips any item whose output already exists (resume-safe).
#
# CONTRACT:
#   - Read: the item is commandArgs(trailingOnly=TRUE)[[1]]
#   - Write: $YUJ_OUT/<item>.csv  (yuj sets $YUJ_OUT = output_dir)
#   - Do not crash the whole batch on one bad item, catch errors and write a
#     row with an error flag instead.

# Make packages installed into ~/.yuj-rlib visible.
.libPaths(c(Sys.getenv("R_LIBS_USER", "~/.yuj-rlib"), .libPaths()))

suppressMessages({
  library(data.table)
})

args    <- commandArgs(trailingOnly = TRUE)
item    <- args[[1]]
out_dir <- Sys.getenv("YUJ_OUT", "results")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)
out     <- file.path(out_dir, paste0(item, ".csv"))

# ── Replace this block with your real analysis ────────────────────────────────
result <- tryCatch({
  # Example: compute some statistics on the item name itself.
  data.table(
    item       = item,
    n_chars    = nchar(item),
    has_digit  = grepl("[0-9]", item),
    timestamp  = format(Sys.time(), "%Y-%m-%dT%H:%M:%S")
  )
}, error = function(e) {
  data.table(item = item, n_chars = NA, has_digit = NA,
             timestamp = format(Sys.time()), error = conditionMessage(e))
})
# ─────────────────────────────────────────────────────────────────────────────

fwrite(result, out)
cat(sprintf("[yuj] wrote %s\n", out))
