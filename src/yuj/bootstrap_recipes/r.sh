# yuj bootstrap extra: R, install CRAN packages into a user library.
# Reads one package name per line from $YUJ_REMOTE_DIR/r-packages.txt (or
# ./r-packages.txt). Skips packages already installed, so it is idempotent.
# Needs an R on PATH, get one without root via the conda/micromamba env manager
# (e.g. `yuj bootstrap --env-manager micromamba --env-file environment.yaml`
# with `r-base` in the env). Lines starting with '#' are ignored.
# shellcheck shell=bash
pkgfile="${YUJ_REMOTE_DIR:-.}/r-packages.txt"
[ -f "$pkgfile" ] || pkgfile="./r-packages.txt"

if ! command -v Rscript >/dev/null 2>&1; then
    echo "R not found on PATH, skipping R packages (install r-base via your env manager)"
elif [ ! -f "$pkgfile" ]; then
    echo "no r-packages.txt found, skipping R package install"
else
    # User library survives reboots and needs no root.
    rlib="$HOME/.yuj-rlib"
    mkdir -p "$rlib"
    echo "installing R packages from $pkgfile into $rlib ..."
    grep -vE '^\s*(#|$)' "$pkgfile" | while IFS= read -r pkg; do
        pkg=$(echo "$pkg" | tr -d '[:space:]')
        [ -z "$pkg" ] && continue
        Rscript -e "lib='$rlib'; .libPaths(c(lib, .libPaths())); \
if (!requireNamespace('$pkg', quietly=TRUE)) \
install.packages('$pkg', lib=lib, repos='https://cloud.r-project.org') \
else cat('already installed: $pkg\n')"
    done
fi
