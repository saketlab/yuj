# yuj bootstrap extra: SHELLCHECK, install shellcheck via the static binary.
# Idempotent: skips if already on PATH.
# shellcheck shell=bash
if command -v shellcheck >/dev/null 2>&1; then
    echo "shellcheck already installed: $(shellcheck --version | awk '/version:/{print $2}')"
else
    echo "installing shellcheck..."
    sc_ver="v0.10.0"
    sc_url="https://github.com/koalaman/shellcheck/releases/download/${sc_ver}/shellcheck-${sc_ver}.linux.x86_64.tar.xz"
    mkdir -p "$HOME/.local/bin"
    curl -fsSL "$sc_url" | tar -xJ -C "$HOME/.local/bin" --strip-components=1 "shellcheck-${sc_ver}/shellcheck"
    echo "shellcheck installed to $HOME/.local/bin"
fi
