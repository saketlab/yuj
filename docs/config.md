# Configuration

Two files: `yuj.yaml` holds the job, `fleet.csv` holds the machines. Every CLI
flag that overlaps a setting wins over the file.

## `yuj.yaml`

```yaml
fleet: fleet.csv                        # inventory path (--fleet overrides)
job: mypyjob                            # names every on-host artifact
remote_dir: yuj-run                     # deploy target, relative to $HOME
work_command: python3 $HOME/yuj-run/worker.py   # gets the item as its last argument
input_file: items.txt                   # one item per line
output_dir: results                     # relative to remote_dir
output_suffix: .out                     # <item><suffix> = the resume marker
results_glob: ~/yuj-run/results/*.out   # what status counts and pull fetches
stall_min: 30                           # restart if no new output for this long
concurrency: 4                          # workers per host (see sizing: below)
active_window: "19:00-09:30"            # only run between these times
courtesy: true                          # only use GPUs nobody else is on
off_window_command: "pkill -f myserver" # cleanup when a host leaves its window
```

| Key | Default | Notes |
|-----|---------|-------|
| `fleet` | `fleet.csv` | Inventory path |
| `job` | `yuj` | Scopes scripts, cron and the stop sentinel, so two jobs coexist on one host |
| `remote_dir` | `yuj-run` | Under `$HOME` on each host |
| `work_command` | required | Required by `submit`. yuj appends the item |
| `input_file` | none | Work list. Required for `--autotune` and progress bars |
| `output_dir` | none | Where the worker writes, relative to `remote_dir` |
| `output_suffix` | `""` | Changing it mid-run changes what counts as done |
| `results_glob` | `~/*` | Counted by `status`, fetched by `pull` |
| `stall_min` | 90 | Watchdog kill threshold. Stalls are ignored for 45 min after each launch |
| `concurrency` | 1 | Parallel workers per host, unless `--autotune` sets it |
| `active_window` | none | `HH:MM-HH:MM`, may wrap midnight. Per-host `window` overrides it |
| `off_window_command` | none | Runs on the host when its window closes |
| `courtesy` | false | Share the GPUs. `--courtesy` turns it on for one submit |

### Sections

```yaml
deploy:
  code: [worker.py, items.txt]   # re-synced on every deploy
  payload: [models/, data/]      # heavy data; skipped by --no-payload

scatter:
  input: accessions.txt          # full list to split
  into: work.txt                 # per-host slice filename
  header: accession              # line prepended to each host's slice file
  bench_url: https://ftp.ncbi.nlm.nih.gov/...   # target for --by download

bootstrap:
  env_manager: uv                # uv | pixi | micromamba | conda
  python: "3.12"
  extras: [R, OLLAMA]            # R, OLLAMA, SHELLCHECK, RCLONE
  env_file: environment.yaml     # conda/micromamba spec, deployed with your code
  from_tarball: /opt/yuj/uv.tar.gz   # offline install from a pre-staged tarball

authorize:
  key: ~/.ssh/id_ed25519.pub     # public key `yuj authorize` installs

sizing:                          # per-worker cost, used by --autotune
  ram_gb: 7.0                    # peak RSS
  vram_mb: 6000                  # resident VRAM
  cores: 8                       # threads
  max_per_gpu: 8                 # cap per card
  gpu_reserve_mb: 1000           # VRAM left free on each card
  require_gpu: false             # skip hosts with no usable GPU
  own_marker: "worker.py"        # GPU processes of yours lacking this count as
                                 # foreign. Applies to --autotune only
```

## `fleet.csv`

One row per machine. `username`, `ip` and `name` are required; everything else
is optional.

```csv
username,ip,name,password,key_path,weight,window,do_not_use,local,port
alice,10.0.0.1,gpu-rig,,/home/alice/.ssh/id_ed25519,4,,,,
alice,10.0.0.2,lab-desk,s3cret,,1,19:00-09:30,,,
alice,10.0.0.3,old-box,,,1,,true,,
you,localhost,this-box,,,2,,,true,
alice,203.0.113.10,cloud-box,,~/.ssh/id_ed25519,1,,,,2222
```

| Column | Default | Meaning |
|--------|---------|---------|
| `username` | required | SSH user |
| `ip` | required | Address or hostname |
| `name` | required | Label used by `--hosts`, `pull --per-host`, and every table |
| `password` | none | Needs `sshpass`. Prefer `key_path` |
| `key_path` | none | Private key. No fail2ban risk |
| `port` | 22 | SSH port |
| `weight` | 1 | Share of items from `yuj scatter`. `0` drains a host without removing it |
| `window` | job-wide | Per-host run window. A blank cell means always on; omit the column to inherit `active_window` |
| `do_not_use` | false | Skipped by default; naming it in `--hosts` is an error |
| `local` | false | Run through a local shell, no SSH. Auto-detected for `localhost`/`127.0.0.1` |
| `strict_host_key` | false | Enforce known_hosts checking for this host |
| `known_hosts_file` | none | Which known_hosts to check against |

`username`/`user`, `ip`/`host` and `name`/`machine` are all accepted spellings
for the three required columns.

Keep this file out of version control; the scaffold's `.gitignore` already
excludes it.

### YAML instead of CSV

Point `--fleet` (or `fleet:` in `yuj.yaml`) at a `.yaml` file to share settings
across machines. Top-level `user`, `port`, `key_path`, `weight`, `window`,
`strict_host_key` and `known_hosts_file` are defaults; each machine may override
them and must supply at least `name` and `ip`.

```yaml
# fleet.yaml
user: alice
key_path: /home/alice/.ssh/id_ed25519
window: "19:00-09:30"
machines:
  - name: gpu-rig
    ip: 10.0.0.1
    weight: 4
    window: ""            # always on, overriding the default
  - name: lab-desk
    ip: 10.0.0.2
  - name: cloud-box
    ip: 203.0.113.10
    port: 2222
    strict_host_key: true
    known_hosts_file: /home/alice/.ssh/known_hosts
```
