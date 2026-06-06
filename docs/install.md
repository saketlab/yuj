# Installation

Install **yuj on your laptop** (the controller). You don't need anything on the remote hosts; that is what `yuj bootstrap` is for.

## Recommended: pipx

[pipx](https://pipx.pypa.io) installs yuj into its own environment and puts `yuj` on your `$PATH`.

```bash
pipx install yuj
yuj version
```

## uv tool

```bash
uv tool install yuj
yuj version
```

## pip (inside a venv)

```bash
python -m venv .venv && source .venv/bin/activate
pip install yuj
```

## Requirements

- Python ≥ 3.12 on your **laptop**
- `ssh` and `rsync` on your laptop (standard on macOS and Linux)
- `sshpass`, only needed for password-based hosts

    === "Ubuntu / Debian"
        ```bash
        sudo apt install sshpass
        ```
    === "macOS (Homebrew)"
        ```bash
        brew install hudochenkov/sshpass/sshpass
        ```
    === "Fedora / RHEL"
        ```bash
        sudo dnf install sshpass
        ```

!!! tip "SSH keys avoid fail2ban"
    If the host owner can add your public key to `~/.ssh/authorized_keys`, use `key_path` in `fleet.csv`. Keys never trigger fail2ban and don't need `sshpass`.

## Verify

```console
$ yuj version
0.1.0.dev0

$ yuj --help
 Usage: yuj [OPTIONS] COMMAND [ARGS]...
 ...
```
