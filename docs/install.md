# Installation

Install **yuj on your laptop**. You don't need anything on the remote hosts; `yuj bootstrap` takes care of
that.

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

## pip 

```bash
python -m venv .venv && source .venv/bin/activate
pip install yuj
```

## Requirements

- Python ≥ 3.12 on your **laptop**
- `ssh` and `rsync` on your laptop 
- `sshpass`, only needed for password-based hosts:

::::{tab-set}

:::{tab-item} Ubuntu / Debian
```bash
sudo apt install sshpass
```
:::

:::{tab-item} macOS (Homebrew)
```bash
brew install hudochenkov/sshpass/sshpass
```
:::


::::

:::{admonition} SSH keys avoid fail2ban
:class: tip
If the host owner can add your public key to `~/.ssh/authorized_keys`, use `key_path` in `fleet.csv`. Keys never trigger fail2ban and don't need `sshpass`.
:::

## Verify

```console
$ yuj version
<installed version>

$ yuj --help
 Usage: yuj [OPTIONS] COMMAND [ARGS]...
 ...
```
