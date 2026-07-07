# yuj bootstrap extra: micromamba + a conda-forge env from environment.yaml.
# Pairs with `env_manager: uv` so a job can use uv (Python) and R side by side;
# run the env's tools with `micromamba run -n <env> <cmd>`. No root, no compiler.
# Idempotent: fetches micromamba only if absent, creates the env only if missing.
# shellcheck shell=bash
envfile="${YUJ_REMOTE_DIR:-.}/environment.yaml"
[ -f "$envfile" ] || envfile="./environment.yaml"
export MAMBA_ROOT_PREFIX="${MAMBA_ROOT_PREFIX:-$HOME/micromamba}"

if ! command -v micromamba >/dev/null 2>&1; then
    echo "fetching micromamba..."
    arch=$(uname -m); [ "$arch" = "x86_64" ] && arch=64
    mkdir -p "$HOME/.local"
    curl -Ls "https://micro.mamba.pm/api/micromamba/linux-${arch}/latest" \
        | tar -xj -C "$HOME/.local" bin/micromamba
fi

if [ ! -f "$envfile" ]; then
    echo "no environment.yaml found, skipping env creation"
else
    name=$(grep -m1 '^name:' "$envfile" | awk '{print $2}')
    name="${name:-yuj-rpy}"
    if micromamba env list | grep -qE "[ /]${name}\$"; then
        echo "micromamba env '$name' already exists"
    else
        echo "creating micromamba env '$name' from $envfile ..."
        micromamba create -y -f "$envfile"
    fi
fi
