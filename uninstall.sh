#!/usr/bin/env bash
# ==============================================================================
# AI Gateway Uninstaller
# Stops services and removes systemd units and command launchers.
# ==============================================================================

TARGET_BIN="${HOME}/.local/bin/ai-gateway"
SERVICE_PATH="${HOME}/.config/systemd/user/ai-gateway.service"

echo "=== Uninstalling AI Gateway ==="

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user stop ai-gateway.service 2>/dev/null || true
    systemctl --user disable ai-gateway.service 2>/dev/null || true
fi

if [ -f "${SERVICE_PATH}" ]; then
    rm -f "${SERVICE_PATH}"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    echo "✓ Removed systemd user service."
fi

if [ -f "${TARGET_BIN}" ]; then
    rm -f "${TARGET_BIN}"
    echo "✓ Removed ${TARGET_BIN}"
fi

echo "✓ AI Gateway service and command have been removed."
echo "Note: The repository directory and .env were kept intact."
