# Testing your setup

There are two levels of tests: **local unit tests** (run in seconds, no SSH
needed) and **real-fleet e2e tests** (opt-in, touch real hosts).

## Running the unit tests

```bash
# Install dev dependencies (includes pytest, shellcheck-py, etc.)
uv sync

# Run all unit tests
uv run pytest

# With coverage
uv run pytest --cov=yuj --cov-report=term-missing
```

Expected output:

```
262 passed, 5 skipped in 2.0s
```

The 5 skipped are the real-fleet e2e tests; they don't run unless you opt in
(see below).

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

## Real-fleet e2e tests

!!! warning "These tests SSH into real hosts"
    The e2e suite is fully opt-in. Run it only on machines you own or have
    explicit permission to use. **Do not run on a Friday evening** if a
    production job is running alongside.

### Safety isolation

Every e2e test uses **`job="yuj-test"`**, which makes all artifacts unique:

| Artifact | production | yuj e2e |
|----------|------------|---------|
| Deploy dir | `~/hpc_v3/` | `~/yuj-test/` |
| Cron line | `…ensure_watchdog.sh` | `…yuj-test.yuj-ensure.sh` |
| Sentinel | `/tmp/watchdog.stop` | `/tmp/yuj-test.yuj.stop` |
| Results | `hpc_v3/results/` | `~/yuj-test/results/` |

A session finalizer decommissions every touched host at the end, and the suite
fails if any production `run_b20.sh` process count drops.

### Running

```bash
uv run pytest tests/e2e/ \
  --fleet=/path/to/fleet.csv \
  --launchpad-cred=~/.my-creds \
  --include-hosts=myhost1,myhost2 \
  --tb=short -v
```

Options:

| Option | Description |
|--------|-------------|
| `--fleet PATH` | Fleet CSV with hosts + credentials |
| `--include-hosts HOST,...` | **Required** opt-in list (never runs on all by default) |
| `--launchpad-cred PATH` | File with `kcdh_password=...` for launchpad |
| `--keep` | Don't delete `~/yuj-test/` after the run |

### Scenarios

| # | Test | What it proves |
|---|------|---------------|
| 1 | `test_bootstrap_real.py` | Bootstrap (uv + Ollama), idempotency |
| 2 | `test_deploy_and_pull.py` | 200-item job completes; all digests correct |
| 6 | `test_probe_classifies.py` | Diagnose net/sshd/banner/auth |
| 7 | `test_do_not_use_respected.py` | `do_not_use` flag enforced |
| 8 | Cleanup assertion in session finalizer | No stray cron/procs/dirs |

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
- Tests (pytest, matrix: Python 3.12 + 3.13)
```

[![CI](https://github.com/saketkc/yuj/actions/workflows/ci.yml/badge.svg)](https://github.com/saketkc/yuj/actions/workflows/ci.yml)
