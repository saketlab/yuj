# Testing your setup

The package ships **local unit tests** that run in seconds with no SSH needed.
Beyond those, the surest check is a dry-run against your own hosts (below).

## Running the unit tests

```bash
# Install dev dependencies (includes pytest, shellcheck-py, etc.)
uv sync --locked

# Run all unit tests
uv run pytest

# With coverage
uv run pytest --cov=yuj --cov-report=term-missing
```

Expected output:

```
542 passed
```

## Verifying your own installation

```bash
# 1. Check the CLI works
yuj version
yuj --help

# 2. Scaffold a project and inspect it
yuj init --template r my-test-job
ls my-test-job

# 3. Dry-run bootstrap (no changes made)
cd my-test-job && yuj bootstrap --check
```

`yuj bootstrap --check` prints what it would install on each host without
running anything. Use it to verify the fleet CSV before committing.

## Probing your fleet

```bash
yuj diagnose
```

Classifies each host:

| Status | Meaning |
|--------|---------|
| `✓ ok` | SSH works, auth succeeded |
| `auth refused` | Wrong password/key, or fail2ban banned your IP |
| `banner fail (fail2ban?)` | TCP connects but SSH banner dropped (classic fail2ban) |
| `sshd down` | Port 22 closed / connection refused |
| `net down` | No route, timeout |

```bash
yuj status            # full dashboard (CPU, GPU, load, outputs, age, owner)
```

## Testing against your own hosts

:::{admonition} Use machines you own
:class: warning
Run this only on machines you own or have explicit permission to use, and not
alongside a production job you can't afford to disturb.
:::

Once the unit tests pass, validate yuj end-to-end with a tiny, harmless job:

```bash
yuj diagnose                       # confirm every host is reachable
yuj init --template bare my-check  # scaffold a trivial worker
cd my-check
# edit fleet.csv with your hosts, then:
yuj bootstrap --check              # dry-run: what would be installed
yuj run                            # deploy + start the watchdog
yuj status --watch 10              # watch it produce
yuj pull                           # gather results
yuj decommission <host>            # hand the machine back clean
```

yuj scopes every artifact to the job name (`<job>.yuj-*` scripts, a
`<job>.yuj.stop` sentinel, a job-tagged cron line), so a run never touches
another job already on the same host. Always finish with `yuj decommission` to
remove the cron entry and its processes.

## Template shellcheck

The generated on-host scripts are shellcheck-clean:

```bash
uv run pytest tests/test_templates.py tests/test_bootstrap.py -v
```

## CI

The GitHub Actions workflow runs on every push:

```yaml
# .github/workflows/ci.yml
- Lint (ruff)
- Format check (ruff format)
- Type check (mypy --strict)
- Tests (pytest, matrix: Python 3.12 + 3.13 + 3.14)
- Docs build (Sphinx, warnings as errors)
- Package build + wheel smoke test
```

[![CI](https://github.com/saketlab/yuj/actions/workflows/ci.yml/badge.svg)](https://github.com/saketlab/yuj/actions/workflows/ci.yml)
