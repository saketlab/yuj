# Quickstart

This page walks through a complete Python job. For R, see [R users](r-users.md).

## 1. Scaffold a project

```bash
mkdir my-batch && cd my-batch
yuj init --template python
```

Creates:

```
my-batch/
  fleet.csv       ← add your hosts here
  yuj.yaml        ← job config (pre-filled)
  worker.py       ← your analysis code
  items.txt       ← one item per line
```

## 2. Add your hosts

Edit `fleet.csv`:

```csv
username,ip,name,password
alice,10.0.0.1,lab-desk-1,s3cret
alice,10.0.0.2,lab-desk-2,
```

:::{admonition} Key-based auth
:class: note
Leave `password` empty and add `key_path=/home/alice/.ssh/id_ed25519` to avoid fail2ban risk and skip `sshpass`.
:::

## 3. Write your worker

`worker.py` already has the right structure. Replace the placeholder:

```python
def main() -> None:
    item = sys.argv[1]                              # provided by yuj
    out_dir = Path(os.environ.get("YUJ_OUT", "results"))
    out_dir.mkdir(parents=True, exist_ok=True)

    result = analyze(item)
    (out_dir / f"{item}.out").write_text(result)
```


## 4. Bootstrap

Install Python (via `uv`) on every host:

```bash
yuj bootstrap
```

```
 yuj bootstrap
┏━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ host        ┃ result   ┃ detail                              ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ lab-desk-1  │ ✓ ok     │ bootstrapped [Ubuntu 22.04.5 LTS]  │
│ lab-desk-2  │ ✓ ok     │ bootstrapped [Ubuntu 24.04.1 LTS]  │
└─────────────┴──────────┴─────────────────────────────────────┘
```

Re-running bootstrap on an already-configured host is a no-op.

## 5. Deploy and submit

`yuj run` does both in one step (`yuj deploy && yuj submit`):

```bash
yuj run
```

- **deploy** rsyncs your code (and any heavy `payload`) to every host.
- **submit** installs the self-healing watchdog and a cron entry, then starts the job.

The watchdog relaunches your worker if it dies or stalls; the cron entry restarts the watchdog after a reboot, with no controller involvement needed. (Run `yuj deploy` and `yuj submit` separately if you'd rather stage them.)

:::{admonition} Give each host its own work
:class: tip
By default every host reads the full `items.txt`. That's safe (yuj skips items whose output already exists) but redundant. To split the work so each host gets only its share, weighted by capacity, run `yuj scatter` before submitting:

```bash
yuj scatter --input items.txt    # writes each host its own slice, by static weight
```

To split by live capacity instead of the static `weight` column, add `--by`
(`cores`, `mem`, `gpu`, `disk`, or `download`). Rank the fleet first with
`yuj fleet bench` to see the numbers:

```bash
yuj fleet bench --sort download          # rank hosts by download speed
yuj scatter --input items.txt --by download   # faster hosts get more items
```
:::

## 6. Watch

```bash
yuj status --watch 30       # refresh every 30 seconds (Ctrl-C to exit)
```

```
                     yuj fleet · mypyjob
┏━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━┓
┃ host        ┃ ip       ┃ state       ┃ cpu           ┃ outputs ┃ age ┃
┡━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━┩
│ lab-desk-1  │ 10.0.0.1 │ ● producing │ 8c · Xeon …  │      42 │  2m │
│ lab-desk-2  │ 10.0.0.2 │ ○ idle      │ 16c · EPYC … │       0 │   - │
└─────────────┴──────────┴─────────────┴───────────────┴─────────┴─────┘
2/2 up · 1 producing · 0 stalled · 42 outputs · 0 with owner present
```

## 7. Pull results

```bash
yuj pull                    # pull once into ./results/
watch -n 60 yuj pull        # or re-pull every 60 seconds
```

Results merge into `results/` by default (`--dest DIR` to change it, `--per-host` to keep them in `results/<host>/`). Pull as often as you like, mid-job for partial results or once at the end.

## 8. Decommission

```bash
yuj decommission lab-desk-1                         # now
yuj decommission lab-desk-2 --at "9am tomorrow"     # scheduled
```

Removes only yuj's cron entry and processes; any production job on the same host is untouched.
