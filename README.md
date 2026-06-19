# yuj

[![CI](https://github.com/saketlab/yuj/actions/workflows/ci.yml/badge.svg)](https://github.com/saketlab/yuj/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-Sphinx-blue)](https://yuj.saketlab.org)
[![PyPI](https://img.shields.io/pypi/v/yuj.svg)](https://pypi.org/project/yuj/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)


**[Full documentation →](https://yuj.saketlab.org)**

`yuj` scatters work across idle lab desktops over SSH and pulls the results back. A self-healing watchdog on each host keeps the batch running through crashes and reboots, so you don't need root on the machines or a scheduler watching over them.


If you have a 'batch of jobs' that can be split into independent items, you can use yuj to achieve effective and fast parallelisation. Each item runs on one host on its own, with no communication between items and no shared memory. 

`yuj` will not work if your job needs nodes to talk to each other mid-run (MPI, a shared address space).


## Quickstart

```bash
# Python job
pipx install yuj
mkdir my-job && cd my-job
yuj init --template python    # scaffold worker.py + config
$EDITOR fleet.csv             # add hosts (username, IP, password or key)
yuj bootstrap                 # install Python (uv) on each host, no root
yuj deploy && yuj submit      # push code, start watchdog + cron
yuj status --watch 30         # live dashboard
yuj pull --loop 60
yuj decommission lab-desk-1 --at "9am tomorrow"
```

```bash
# R job
pipx install yuj
mkdir my-r-job && cd my-r-job
yuj init --template r         # scaffold worker.R + environment.yaml
$EDITOR fleet.csv
yuj bootstrap                 # install R via micromamba, no root or compiler
yuj deploy && yuj submit
yuj status --watch 30
yuj pull --loop 60
```

See the [full quickstart](https://yuj.saketlab.org/quickstart/) and [R users guide](https://yuj.saketlab.org/r-users/) in the docs.

## How it works

0. **provision** *(optional)*: if you only have an admin/sudo login, `yuj provision` creates a dedicated worker user with key-only auth and writes a `provisioned-fleet.csv` you can hand to the rest of the commands. This is the one step that needs privilege.
1. **deploy**: rsync your code + data to `$HOME` on each host.
2. **submit**: install a watchdog and a cron entry that restarts it every 15 min. The watchdog runs your work loop, detects stalls (no new output in N min), and relaunches.
3. **resume**: the work loop skips any item whose output file already exists. This is the only reliable checkpoint when a worker crashes mid-write.
4. **pull**: parallel rsync of outputs back to a central directory, tolerant of hosts being down.
5. **survive**: supervision lives in cron on each host, so a rebooted host resumes within 15 min with no controller involved.

## Caveats

- Passwords work but trigger fail2ban on repeated auth failures. Pass `key_path` in `fleet.csv` to avoid this.
- A few failed auths can get your IP banned for hours. `yuj diagnose` classifies what's wrong (net down / sshd down / banner drop / auth refused); it won't hammer a banned host.
- Passwords are stored as plain text in fleet.csv. The `.gitignore` excludes it by default, but double-check before pushing.
- `yuj status` shows a warning when someone is at the console. Use `yuj decommission --at "9am tomorrow"` to hand machines back at a civilised time.

## Development

```bash
uv sync --locked              # create the venv and install dev deps
uv run pytest                 # tests
uv run ruff check .           # lint
uv run ruff format .          # format
uv run mypy src               # strict type-check
```

## License

[MIT](LICENSE) 
