# CLI reference

All commands read settings from `yuj.yaml` in the current directory; flags
override. Run any command with `--help` for the full option list.

## `yuj init`

Scaffold a ready-to-edit project. Never clobbers existing files.

```bash
yuj init [DIRECTORY] [--template {bare,python,r,r-python}]
```

| Template | Creates |
|----------|---------|
| `bare` | `fleet.csv`, `yuj.yaml`, `worker.sh` |
| `python` | + `worker.py`, `items.txt` |
| `r` | + `worker.R`, `items.txt`, `environment.yaml`, `r-packages.txt` |
| `r-python` | + `worker.sh`, `worker.py`, `worker_rpy2.py`, `worker.R`, `items.txt`, `environment.yaml`, `requirements.txt` |

`r-python` runs uv (Python) and micromamba (a conda-forge R env) side by side,
with `pyreadr` in the uv venv and `rpy2` in the micromamba env. See the
[R users guide](https://yuj.saketlab.org/r-users/) for details.

```bash
yuj init my-job --template r
```

## `yuj provision`

Create an unprivileged worker user on every host and save its credentials. This
is the one step that needs privilege: `--fleet` here is an **admin** fleet whose
accounts can `sudo`.

```bash
yuj provision [--fleet ADMIN_CSV] [--hosts a,b,...] [--user yuj]
              [--ask-sudo-pass] [--key-dir .yuj/keys] [--out provisioned-fleet.csv]
              [--check] [--max-workers 4]
```

yuj generates one ed25519 keypair, installs the public key in each new user's
`~/.ssh/authorized_keys` (key auth only, no password is set), locks the
account's password, and writes a ready-to-use worker fleet CSV.

`--user` names the worker account to create (default `yuj`). For sudo, yuj
reuses the admin's SSH password unless you pass `--ask-sudo-pass` to be prompted
separately. `--key-dir` sets where the generated private key lands (default
`.yuj/keys/`) and `--out` names the worker fleet CSV it writes (default
`provisioned-fleet.csv`). `--check` is a dry run: it reports what would happen
and creates nothing.

The sudo password travels over SSH stdin (`sudo -S`), never on a command line.
Re-running is idempotent: the keypair is reused, existing users are left in
place, and the public key is appended only if missing. Feed the result straight
into bootstrap:

```bash
yuj provision --fleet admin.csv --user yuj
yuj bootstrap --fleet provisioned-fleet.csv
```

## `yuj bootstrap`

Install an environment manager + extras on every host (idempotent, no root).

```bash
yuj bootstrap [--fleet PATH] [--hosts a,b,...] [--env-manager uv|pixi|micromamba|conda]
              [--python 3.12] [--extras OLLAMA,R] [--env-file PATH]
              [--from-tarball REMOTE_PATH] [--check] [--max-workers 4]
```

`--env-manager` picks which manager to install. `--extras` takes a comma-list of
named bundles: `R`, `OLLAMA`, `SHELLCHECK`, `RCLONE`. `--env-file` points at
an environment spec already deployed with your project. `--from-tarball` uses a
pre-staged remote tarball instead of fetching an installer live, which is the
reproducible/offline path for pinned bootstrap assets. `--check` does a dry run,
installing nothing. Keep `--max-workers` low (default 4) so parallel SSH logins
don't trip fail2ban.

## `yuj deploy`

Rsync code + payload to every host.

```bash
yuj deploy [--fleet PATH] [--hosts a,b,...] [--no-payload]
```

`--no-payload` skips heavy shared data (e.g. a multi-GB cache) and sends only
the code, the lightweight redistribution path for re-runs.

Aborts before sending anything if a `deploy.code` or `deploy.payload` path in
`yuj.yaml` doesn't exist locally.

## `yuj submit`

Install the self-healing watchdog + cron on every host and start the job.

```bash
yuj submit [--fleet PATH] [--hosts a,b,...] [--no-start]
           [--no-canary] [--canary-timeout SECONDS]
```

Before installing fleet-wide, a **canary** runs the work command once on one
reachable host, skipping already-done items like the real loop. If it fails
fast (bad path, missing dep, import error) submit aborts instead of installing
a crash-looping watchdog everywhere. A command still running at
`--canary-timeout` (default 90s) passes.

`--no-start` installs the scripts + cron but doesn't launch the watchdog. Use
it for staging. `--no-canary` skips the dry run. To run the canary on its own
without submitting, see `yuj canary`.

## `yuj canary`

Dry-run the work command once on one host without installing anything. Use it to
check a `work_command` before launching the whole fleet, or to debug a job that
fails on the hosts.

```bash
yuj canary [--fleet PATH] [--hosts a,b,...] [--canary-timeout SECONDS]
```

Picks the first reachable host (narrow it with `--hosts`); needs the code
already on the host (`yuj deploy` first). Exits non-zero if the command fails
fast. This is the same check `yuj submit` runs, minus the install.

## `yuj run`

Deploy then submit in one step, the everyday shorthand for `yuj deploy && yuj
submit`.

```bash
yuj run [--fleet PATH] [--hosts a,b,...] [--no-payload] [--no-start]
        [--no-canary] [--canary-timeout SECONDS]
```

`--no-payload` skips heavy data already on each host (the lightweight re-run
path); `--no-start` installs the watchdog without launching it; `--no-canary`
skips the pre-submit dry run.

## `yuj scatter`

Weighted-split a work list and write each host only its own slice of items,
so no two hosts process the same item. Without scatter, every host reads the
full `input_file` (safe, thanks to resume-by-output, but redundant).

```bash
yuj scatter [--fleet PATH] [--hosts a,b,...] [--input LIST]
            [--into FILENAME] [--exclude DONE_FILE] [--by DIMENSION]
```

`--input` is the work list to split (otherwise `scatter.input` from `yuj.yaml`).
`--into` is the per-host filename it writes (otherwise `scatter.into`, then
`input_file`). `--exclude` points at a file of items to drop before splitting,
handy for skipping work that's already done.

By default item counts follow each host's static `weight` (largest-remainder
method); zero-weight and `do_not_use` hosts get nothing. Re-run any time to
re-split, for example after editing weights or growing the fleet. Run `yuj
scatter` before `yuj submit`.

```bash
yuj scatter --fleet /home/saket/github/ingestion-v2/b20_users.csv \
            --input accessions.txt --into work.txt
```

### Smart mode: `--by`

`--by` ignores the static weights and splits by live measured capacity, so a
host that can take more load gets more items. Pick the dimension your work is
bound by:

| `--by` | Split proportional to | Bound by |
|--------|-----------------------|----------|
| `cores` | CPU core count | compute |
| `mem` | total RAM | memory |
| `gpu` | total GPU VRAM | GPU jobs |
| `disk` | free space at `remote_dir` | large outputs |
| `download` | measured download speed | fetching data |

`yuj scatter --by` probes the fleet first, then splits. If a host is unreachable
or can't report the chosen dimension (for example, no `curl` for
`--by download`), yuj drops it from the split and names it in a warning instead
of handing it an equal share. It prints the resolved per-host weights so you can
check the split.

```bash
# download-bound genomics fetch: give faster-downloading boxes more accessions
yuj scatter --fleet /home/saket/github/ingestion-v2/b20_users.csv \
            --input accessions.txt --into work.txt --by download

# GPU inference: weight by total VRAM
yuj scatter -f /home/saket/github/ingestion-v2/b20_users.csv --by gpu
```

The download test uses NCBI's FTP (`ftp.ncbi.nlm.nih.gov`) by default; override
it with `scatter.bench_url` in `yuj.yaml`. See [`yuj fleet bench`](#yuj-fleet-bench)
to inspect those numbers before you scatter.

## `yuj authorize`

Install your SSH public key on every host so future logins are key-based (no
passwords, no fail2ban risk). Uses each host's *current* auth once to append the
key to `~/.ssh/authorized_keys`.

```bash
yuj authorize [--fleet PATH] [--hosts a,b,...] [--key PUBKEY | --generate PATH]
```

`--key` is the public key to install (otherwise `authorize.key` from
`yuj.yaml`). `--generate` makes a passphrase-less keypair at the given path
first, then installs it.

Idempotent: the key is appended only if missing. Afterwards, point `key_path` at
the private key in `fleet.csv` and delete the passwords:

```bash
yuj authorize --generate .yuj/keys/fleet_ed25519   # make + install a fresh key
# then set key_path=.yuj/keys/fleet_ed25519 in fleet.csv and drop passwords
```

## `yuj status`

Fleet-wide dashboard.

```bash
yuj status [--fleet PATH] [--results-glob GLOB] [--stall-min N] [--total N]
           [--watch N] [--html PATH] [--open]
```

| Column | Meaning |
|--------|---------|
| state | see table below |
| cpu | Core count + model |
| gpu | GPU name + memory |
| mem | RAM in GB |
| load | 1-min load average (green < 0.7/core, yellow < 1.2, red ≥ 1.2) |
| outputs | Count of result files matching `results_glob` |
| age | Minutes since newest output (stall detection) |
| owner | `⚠ alice` if a human is at the console |

| State | Meaning |
|-------|---------|
| `● producing` | Fresh output within `--stall-min` |
| `● stalled` | Watchdog running but no fresh output |
| `✖ dead` | Job installed (cron present) but watchdog gone |
| `○ idle` | Reachable, no job running |
| `● down` | Unreachable |
| `⊘ excluded` | Host flagged `do_not_use` |

When the total is known, the title shows a tqdm-style progress bar and an ETA:

```text
yuj fleet   13%|██▉                   | 4830/36782 · ETA ~11h at 820/hr
```

The total is counted automatically from `scatter.input` (or `input_file`), so a
scattered job shows the bar with no extra flags. Pass `--total N` to override.
The rate is measured between runs, so the ETA appears on the second `yuj status`
(or the second `--watch` refresh).

`--watch 30` enters live mode, refreshing every 30 seconds. Press Ctrl-C to exit.

`--html PATH` writes the dashboard to a single self-contained HTML file instead
of printing the table. Add `--watch N` to re-probe every N seconds (the page
reloads itself) and `--open` to open it in a browser.

```bash
yuj status --total 36782 --html status.html --watch 30 --open
```

Without `--watch` it writes once and exits.

## `yuj fleet probe`

Same as `yuj status` but always one-shot (no live mode).

```bash
yuj fleet probe [--fleet PATH] [--results-glob GLOB]
```

## `yuj fleet bench`

Benchmark and rank the fleet by throughput, best host first.

```bash
yuj fleet bench [--fleet PATH] [--hosts a,b,...] [--sort DIMENSION]
                [--no-download] [--url URL] [--max-time SECONDS]
```

`--sort` picks the ranking axis: `cores`, `mem`, `gpu`, `disk`, or `download`
(default `cores`). The download-speed test runs by default; skip it with
`--no-download` for an instant hardware-only view (`--sort download` always runs
it). `--url` overrides the download target (default NCBI FTP), `--max-time` caps
the test per host.

```bash
# rank by download speed against NCBI FTP
yuj fleet bench --fleet /home/saket/github/ingestion-v2/b20_users.csv --sort download

# instant hardware ranking, no network test
yuj fleet bench -f /home/saket/github/ingestion-v2/b20_users.csv --sort gpu --no-download
```

The `↓` marks the sorted column (ranking is descending, best host first).

```text
yuj fleet bench  (by download)
 #  host      cores   RAM           GPU  disk free  ↓ download
 1  gpu-01       32  251G  2x A100-80GB      1800G    118 MB/s  ██████████████
 2  node-07      16   62G            -       400G      61 MB/s  ████████
 -  dead-2   connection timed out
fleet totals: 48 cores · 313G RAM · 2200G disk free · 179 MB/s aggregate ↓ · 2 GPU(s)  across 2 host(s)
```

## `yuj pull`

Rsync outputs from the fleet back to a local directory.

```bash
yuj pull [--fleet PATH] [--hosts a,b,...] [--dest DIR] [--per-host]
```

`--dest`/`-d` is the local directory that receives results (default `results`).
`--per-host` keeps each host's output under `dest/<host>/` instead of merging it
all into one place.

 To pull on a schedule, re-run it under `watch` or cron:

```bash
watch -n 60 yuj pull       # pull every 60 seconds
```

## `yuj diagnose`

Classify why hosts are (un)reachable.

```bash
yuj diagnose [--fleet PATH] [--hosts a,b,...]
```

| Status | Meaning |
|--------|---------|
| `✓ ok` | Auth succeeded |
| `auth refused` | Bad password/key or fail2ban ban |
| `banner fail` | TCP connects, SSH drops (classic fail2ban) |
| `sshd down` | Port 22 closed / refused |
| `net down` | No route / timeout |

## `yuj storage`

Show disk headroom on each host.Use it before `deploy` to
spot hosts with a full `/` or no room for outputs.

```bash
yuj storage [--fleet PATH] [--hosts a,b,...] [--work-dir PATH]
```

The green header line per host reports free space at the work directory
(`remote_dir` from `yuj.yaml` by default).

A `★` in the **work** column marks the partition that directory lives on,
so you can see the disk your outputs land.

Pass `--work-dir ~` if you haven't deployed yet. `df` can't measure a
`remote_dir` that doesn't exist, so it shows `? free at ... (path missing?)`.

```text
┃ host      ┃ filesystem     ┃ mount ┃ size ┃ avail ┃ use% ┃ work ┃
┡━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━┩
│ box       │ 1.5T free at ~ │       │      │       │      │      │
│           │ /dev/nvme0n1p3 │ /     │ 232G │  206G │   7% │      │
│           │ /dev/sda1      │ /home │ 1.8T │  1.5T │   4% │  ★   │
```

## `yuj exec`

Run a shell command on every host in parallel.

```bash
yuj exec "COMMAND" [--fleet PATH] [--hosts a,b,...] [--include-down] [--timeout S] [--raw]
```

Runs in each host's default shell (the command should be in quotes). Targets usable hosts;
`--include-down` also includes hosts flagged `do_not_use`. `--raw` prints each
host's full stdout/stderr instead of a one-line-per-host table.

## `yuj decommission`

Remove the yuj job from a host, now or scheduled.

```bash
yuj decommission HOST [--fleet PATH] [--at "WHEN"] [--remove-dir]
yuj decommission all [--fleet PATH] [--at "WHEN"] [--remove-dir]   # or --all
```

`all` (or `--all`) tears down every usable host in parallel (hosts flagged
`do_not_use` are skipped; name one explicitly to decommission it). Teardown
touches only this job's cron and processes, so it is a no-op on hosts that never
ran it.

`--at` accepts relative delays (`+90 seconds`, `+2 hours`) or absolute times
(`"9am tomorrow"`, `"17:00"`) via the host's `at` command.

`--remove-dir` also deletes the deploy directory (`~/yuj-run` by default).

:::{admonition} Job-scoped teardown
:class: warning
Decommission removes **only yuj's** cron entry and processes, identified by
the job name. Any production job running on the same host is untouched.
:::

## Global options

`--fleet PATH` overrides the fleet file (default `fleet.csv`, or `fleet` set in
`yuj.yaml`). `--hosts a,b` restricts the run to named hosts and refuses any
marked `do_not_use`. `--help` shows help for any command.
