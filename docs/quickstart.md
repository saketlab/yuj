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

!!! note "Key-based auth"
    Leave `password` empty and add `key_path=/home/alice/.ssh/id_ed25519` to avoid fail2ban risk and skip `sshpass`.

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

The contract:

| Rule | Why |
|------|-----|
| Item is the last CLI argument | yuj's run-loop appends it |
| Output goes to `$YUJ_OUT/<item><suffix>` | yuj's resume check looks here |
| Catch exceptions per-item | one bad item shouldn't abort the batch |

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

## 5. Deploy

```bash
yuj deploy
```

## 6. Submit

```bash
yuj submit
```

Installs the watchdog and cron on each host and starts the job. The watchdog relaunches your worker if it dies or stalls. A cron entry restarts the watchdog after a reboot, with no controller involvement needed.

## 7. Watch

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

## 8. Pull results

```bash
yuj pull --loop 60          # pull every 60 seconds, Ctrl-C to stop
yuj pull                    # or just once
```

Results land in `central/` by default.

## 9. Decommission

```bash
yuj decommission lab-desk-1                         # now
yuj decommission lab-desk-2 --at "9am tomorrow"     # scheduled
```

Removes only yuj's cron entry and processes; any production job on the same host is untouched.
