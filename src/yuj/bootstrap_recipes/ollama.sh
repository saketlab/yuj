# yuj bootstrap extra: OLLAMA, install Ollama for local LLM workloads.
# Idempotent: skips the install if the binary is already present.
# shellcheck shell=bash
if command -v ollama >/dev/null 2>&1; then
    echo "ollama already installed: $(ollama --version 2>/dev/null | head -1)"
else
    echo "installing ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
fi
# Keep Ollama's data under $HOME so it survives nothing-installed-as-root setups.
mkdir -p "$HOME/.ollama"
export OLLAMA_HOME="$HOME/.ollama"
