# CLI reference

All commands read settings from `yuj.yaml` in the current directory; flags
override. Run any command with `--help` for the full option list.

## `yuj init`

Scaffold a ready-to-edit project. Never clobbers existing files.

```bash
yuj init [DIRECTORY] [--template {bare,python,r}]
```

| Template | Creates |
|----------|---------|
| `bare` | `fleet.csv`, `yuj.yaml`, `worker.sh` |
| `python` | + `worker.py`, `items.txt` |
| `r` | + `worker.R`, `items.txt`, `environment.yaml`, `r-packages.txt` |

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

| Flag | Description |
|------|-------------|
| `--user` | Worker username to create (default `yuj`) |
| `--ask-sudo-pass` | Prompt for the admin's sudo password; by default the admin's SSH password is reused |
| `--key-dir` | Where the generated private key is stored (default `.yuj/keys/`) |
| `--out` | Path for the generated worker fleet CSV (default `provisioned-fleet.csv`) |
| `--check` | Dry-run: report what would happen, create nothing |

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
              [--python 3.12] [--extras OLLAMA,R] [--check] [--max-workers 4]
```

| Flag | Description |
|------|-------------|
| `--env-manager` | Environment manager to install |
| `--extras OLLAMA,R` | Named bundles to install (R, OLLAMA, SHELLCHECK, RCLONE) |
| `--check` | Dry-run: print what would be done, install nothing |
| `--max-workers` | Max parallel hosts (default 4; keep low to avoid fail2ban) |

## `yuj deploy`

Rsync code + payload to every host.

```bash
yuj deploy [--fleet PATH] [--hosts a,b,...] [--no-payload]
```

`--no-payload` skips heavy shared data (e.g. a multi-GB cache) and sends only
the code, the lightweight redistribution path for re-runs.

## `yuj submit`

Install the self-healing watchdog + cron on every host and start the job.

```bash
yuj submit [--fleet PATH] [--hosts a,b,...] [--no-start]
```

`--no-start` installs the scripts + cron but doesn't launch the watchdog. Use
it for staging.

## `yuj status`

Fleet-wide dashboard.

```bash
yuj status [--fleet PATH] [--results-glob GLOB] [--stall-min N] [--watch N]
```

| Column | Meaning |
|--------|---------|
| state | `● producing` · `● stalled` · `○ idle` · `● down` |
| cpu | Core count + model |
| gpu | GPU name + memory |
| mem | RAM in GB |
| load | 1-min load average (green < 0.7/core, yellow < 1.2, red ≥ 1.2) |
| outputs | Count of result files matching `results_glob` |
| age | Minutes since newest output (stall detection) |
| owner | `⚠ alice` if a human is at the console |

`--watch 30` enters live mode, refreshing every 30 seconds. Press Ctrl-C to exit.

## `yuj fleet probe`

Same as `yuj status` but always one-shot (no live mode).

```bash
yuj fleet probe [--fleet PATH] [--results-glob GLOB]
```

## `yuj pull`

Rsync outputs from the fleet back to a central directory.

```bash
yuj pull [--once | --loop N] [--fleet PATH] [--dest DIR]
```

`--loop 60` runs every 60 seconds (press Ctrl-C to stop).

## `yuj diagnose`

Classify why hosts are (un)reachable, with fail2ban awareness.

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

## `yuj decommission`

Remove the yuj job from a host, now or scheduled.

```bash
yuj decommission HOST [--fleet PATH] [--at "WHEN"] [--remove-dir]
```

`--at` accepts relative delays (`+90 seconds`, `+2 hours`) or absolute times
(`"9am tomorrow"`, `"17:00"`) via the host's `at` command.

`--remove-dir` also deletes the deploy directory (`~/yuj-run` by default).

!!! warning "Job-scoped teardown"
    Decommission removes **only yuj's** cron entry and processes, identified by
    the job name. Any production job running on the same host is untouched.

## `yuj stop HOST`

Touch the stop sentinel (`/tmp/<job>.yuj.stop`) for a graceful shutdown. The
watchdog exits on its next check interval.

## Global options

| Option | Description |
|--------|-------------|
| `--fleet PATH` | Override the fleet file (default: `fleet.csv` or `yuj.yaml:fleet`) |
| `--hosts a,b` | Run only on named hosts; refuses `do_not_use` hosts |
| `--help` | Show help for any command |
