# Concepts

## The run model

A **batch** is a list of string items: accessions, sample IDs, URLs, filenames, anything. yuj splits the list across hosts, runs your command once per item, and checks completion by looking for the output file, never a job log.

```
batch = [item1, item2, …, itemN]

per host:
  for item in host_items:
      if output_file_exists(item): skip
      run: work_command  item
```

This works for shell scripts, Python, R, Julia, or any compiled binary callable from a command line.

:::{admonition} yuj only fits embarrassingly parallel work
:class: warning
Each item runs on its own, on one host, with no communication between items and no shared memory. yuj never moves data between hosts mid-run. If your work needs nodes to exchange state while it runs (MPI, a shared address space, a distributed reduction, or a job where item B depends on item A's in-memory result), yuj is the wrong tool. Split the batch into independent items, or use a real cluster scheduler.
:::

## Resume by output file

When a worker crashes mid-write, the only reliable way to know an item is done is to check whether the output file exists. yuj uses this exclusively, not a job log or database. In practice:

- A worker that crashes halfway through writing an item leaves no output file and gets retried on the next watchdog restart.
- Two hosts can process the same item; whichever finishes last wins. Safe because `yuj pull` merges into a single directory.
- Changing `output_suffix` changes what yuj considers done. Don't change it mid-run.

## The watchdog chain

```
cron (every 15 min)
  └─ ensure.sh   → is the watchdog running?
                      yes → exit 0
                      no  → launch watchdog (setsid + &)

watchdog.sh  (runs continuously)
  ├─ every INTERVAL seconds: is the run-loop alive?
  │     dead → relaunch
  │     alive but newest output > STALL_MIN min old → kill + relaunch
  │     alive, recent output → log "ok"
  └─ exits when stop sentinel exists

run-loop.sh
  └─ for item in items.txt:
         if output exists: skip
         work_command item
```

Because supervision lives in cron on the remote host, a controller reboot doesn't touch running jobs. Hosts keep going. The controller reconnects with `yuj pull` and picks up where results left off.

## Isolation from production

When multiple jobs run on the same host, yuj uses the **job name** to make every artifact unique:

| Artifact | Pattern |
|----------|---------|
| Scripts | `~/<remote_dir>/<job>.yuj-run.sh`, `<job>.yuj-watchdog.sh`, `<job>.yuj-ensure.sh` |
| Cron entry | contains `<job>.yuj-ensure.sh` |
| Stop sentinel | `/tmp/<job>.yuj.stop` |
| Results | controlled by `results_glob` in `yuj.yaml` |

`yuj decommission` kills processes and removes the cron entry by matching the job-unique script names. It cannot touch a production job running beside it.

## Weighted distribution

yuj uses the **largest-remainder (Hamilton) method** to split items proportionally to per-host weights, so item counts sum exactly to the batch size with none lost.

```csv
# fleet.csv
username,ip,name,password,weight
alice,10.0.0.1,gpu-rig,p,4     # 4× weight → gets 4× more items
alice,10.0.0.2,laptop,p,1
```

`yuj scatter` applies this split, writing each host only its own slice so no two
hosts process the same item. Without it, every host reads the full list (still
correct, since resume-by-output deduplicates, just redundant).

When the static `weight` column doesn't reflect reality, `yuj scatter --by
<dimension>` measures live capacity (cores, RAM, GPU VRAM, free disk, or
download speed) and splits by that instead. `yuj fleet bench` shows the same
ranking without scattering.

## Autotuning workers per host

Autotuning sets how many workers a host runs at once to maximise
the throughout.

```yaml
# yuj.yaml
concurrency: 4     # used when --autotune is off
sizing:
  ram_gb: 7.0      # peak RSS per worker
  vram_mb: 6000    # resident VRAM per worker
  cores: 8         # threads a worker uses
```

```bash
yuj submit --autotune
```

```text
┃ host         ┃ workers ┃ limited by ┃ gpus  ┃
│ gpubox1      │      12 │ cores      │ 0 1 2 │
│ gpubox2      │      11 │ ram        │ 0 1 2 │
│ old-box      │ skipped │ gpus-busy  │ -     │
```

The worker count is the lowest of three ceilings:

```
by_ram   = mem_available × 0.8 / ram_gb      (0.8: peak sits above steady state)
by_cores = (cores − load1) / cores           (live load counts against you)
by_vram  = Σ cards: min(max_per_gpu, (free_mb − gpu_reserve_mb) / vram_mb)
```
The freest card is filled first. The remaining `sizing:` keys are in the
[configuration reference](config.md#sections).

## Courtesy: sharing the GPUs

Courtsey model allows us to work on a shared GPU box while
respecting other users' jobs.

```yaml
# yuj.yaml
courtesy: true
concurrency: 12    # spread across whatever cards are free
```

```bash
yuj submit --courtesy    # or turn it on for one submit
```

Every watchdog tick re-reads which cards carry someone else's compute, however
little memory they hold, and rewrites the worker's `GPUS`/`WORKERS_PER_GPU`
before relaunching.
An owner starting work on card 1 gets it back on the next
tick. When they finish, yuj takes it again. If
every card is busy the job pauses and waits, and it also pauses when it cannot
read the GPU state at all (a driver error looks the same as a busy card, and
waiting is the polite reading). A host with no `nvidia-smi` runs unrestricted.

## do_not_use

Mark decommissioned or off-limits hosts with `do_not_use: true`:

```csv
username,ip,name,password,do_not_use
alice,10.0.0.3,old-box,p,true
```

- `yuj deploy` (default: all hosts) silently skips them.
- `yuj deploy --hosts old-box` (explicit) refuses with a non-zero exit.

## Quiet hours (run windows)

A borrowed desktop is often only fair game at night. A **window** is a daily
span like `19:00-09:30` during which a host may run work; outside it the
watchdog pauses the job so the owner gets their machine back, and resumes
automatically when the window reopens. Windows may wrap past midnight.

Set a job-wide default in `yuj.yaml`, or override it per host in `fleet.csv`:

```yaml
# yuj.yaml
active_window: "19:00-09:30"            # default for every host
off_window_command: "pkill -f myserver" # optional extra cleanup when pausing
```

```csv
# fleet.csv: a per-host window overrides the job-wide default
username,ip,name,password,window
alice,10.0.0.1,lab-desk,p,19:00-09:30   # only at night
alice,10.0.0.2,server,p,                # blank = always on
```

A blank `window` cell keeps that host running around the clock while the rest
observe quiet hours. Drop the column entirely and every host inherits
`active_window`.

`off_window_command` runs when a host leaves its window, for tidying up anything
your worker leaves behind (a model server, a scratch process) before handing the
machine back.

## Local host

The machine you launch yuj from is often a capable worker too. Mark it `local` and
yuj runs work on it **directly, through a local shell with no SSH**:

```csv
username,ip,name,password,local
you,localhost,this-box,,true
```

A host is treated as local when `local` is true, or when its `ip` is
`localhost`/`127.0.0.1`. This sidesteps the fragile "`ssh localhost` to yourself"
path that many hardened sshd configs refuse. Every other command (`deploy`,
`scatter`, `status`, `pull`, …) works on a local host unchanged.

## Provisioning

Everything else in yuj runs as an ordinary user, but *creating* that user needs
root. `yuj provision` is the optional first step for when you have an admin login
that can `sudo` but no dedicated worker account yet:

```bash
yuj provision --fleet admin.csv --user yuj
```

It connects as the admin, and via `sudo`:

1. creates the worker user (`useradd -m`) if absent,
2. installs a yuj-generated SSH public key in the user's `authorized_keys`,
3. locks the account's password, so only the key works.

One ed25519 keypair is generated on the controller for the whole fleet; the
private key stays under `.yuj/keys/` (gitignored) and a `provisioned-fleet.csv`
points each host's `key_path` at it. The admin's sudo password rides SSH stdin
(`sudo -S`), never a command line. Because the new account is key-only, it never
trips fail2ban, the same reason the rest of yuj prefers keys.

## Politeness

yuj runs on machines that belong to other people:

- `yuj status` shows an owner-present warning when someone is logged into the console (detected via `who`).
- `yuj decommission --at "9am tomorrow"` schedules a teardown so you hand machines back at a set time.
- `--max-workers 4` in bootstrap limits concurrent SSH probes to avoid tripping subnet-level IDS rules.
