# yuj bootstrap extra: RCLONE, install rclone for moving results to cloud/remote.
# Idempotent: skips if already on PATH.
# shellcheck shell=bash
if command -v rclone >/dev/null 2>&1; then
    echo "rclone already installed: $(rclone version | head -1)"
else
    echo "installing rclone..."
    curl -fsSL https://rclone.org/install.sh | sudo bash || curl -fsSL https://rclone.org/install.sh | bash
fi
