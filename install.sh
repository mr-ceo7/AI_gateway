#!/usr/bin/env bash
# ==============================================================================
# AI Gateway Installer
# Sets up Python virtual environment, dependencies, security tokens,
# systemd user service, and command launcher.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)"
TARGET_BIN="${HOME}/.local/bin/ai-gateway"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
SERVICE_NAME="ai-gateway.service"

echo "=== Installing AI Gateway ==="

# 1. Validate Python 3
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: Python 3 is required but not installed." >&2
    exit 1
fi

# 2. Setup Python virtual environment
if [ ! -d "${SCRIPT_DIR}/venv" ]; then
    echo "Creating Python virtual environment in ${SCRIPT_DIR}/venv..."
    python3 -m venv "${SCRIPT_DIR}/venv" || {
        echo "Error: Failed to create venv. Ensure 'python3-venv' is installed." >&2
        exit 1
    }
fi

echo "Installing and upgrading Python dependencies..."
"${SCRIPT_DIR}/venv/bin/pip" install --upgrade pip --quiet
"${SCRIPT_DIR}/venv/bin/pip" install -r "${SCRIPT_DIR}/requirements.txt" --quiet

# 3. Initialize .env if missing
if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    echo "Generating secure .env configuration..."
    RANDOM_TOKEN=$("${SCRIPT_DIR}/venv/bin/python" -c "import secrets; print(secrets.token_hex(24))")
    RANDOM_SECRET=$("${SCRIPT_DIR}/venv/bin/python" -c "import secrets; print(secrets.token_hex(32))")

    cp "${SCRIPT_DIR}/.env.example" "${SCRIPT_DIR}/.env"
    sed -i "s/GATEWAY_TOKENS=generate-a-secure-random-token-here/GATEWAY_TOKENS=${RANDOM_TOKEN}/" "${SCRIPT_DIR}/.env"
    sed -i "s/SECRET_KEY=generate-a-random-hex-secret-here/SECRET_KEY=${RANDOM_SECRET}/" "${SCRIPT_DIR}/.env"
    chmod 600 "${SCRIPT_DIR}/.env"
    echo "✓ Created ${SCRIPT_DIR}/.env with auto-generated gateway tokens."
fi

# 4. Install command line launcher in ~/.local/bin
mkdir -p "${HOME}/.local/bin"
cat << 'EOF' > "${TARGET_BIN}"
#!/usr/bin/env bash
SCRIPT_DIR="$(dirname "$(dirname "$(readlink -f "$0" 2>/dev/null || echo "$0")")")/AI_gateway"
if [ ! -d "$SCRIPT_DIR" ]; then
    SCRIPT_DIR="${HOME}/AI_gateway"
fi
exec "${SCRIPT_DIR}/start.sh" "$@"
EOF
chmod +x "${TARGET_BIN}"
echo "✓ Installed launcher command to ${TARGET_BIN}"

# 5. Configure systemd user service (if systemctl is available)
if command -v systemctl >/dev/null 2>&1; then
    mkdir -p "${SYSTEMD_USER_DIR}"
    cat << EOF > "${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
[Unit]
Description=AI Gateway Daemon
After=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=-${SCRIPT_DIR}/.env
Environment="PATH=${SCRIPT_DIR}/venv/bin:${HOME}/.local/bin:${HOME}/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=${SCRIPT_DIR}/venv/bin/gunicorn --bind 0.0.0.0:5000 --timeout 0 app:app
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload 2>/dev/null || true
    echo "✓ Configured systemd user service: ${SYSTEMD_USER_DIR}/${SERVICE_NAME}"
fi

echo ""
echo "=== AI Gateway Successfully Installed ==="
echo ""
echo "Manage service:"
echo "  ai-gateway                        # Run directly in foreground"
echo "  systemctl --user start ai-gateway # Start background service"
echo "  systemctl --user status ai-gateway# Check service status"
echo "  systemctl --user enable ai-gateway# Start automatically on system boot"
echo ""
echo "API Access:"
echo "  URL: http://localhost:5000"
echo "  Config: ${SCRIPT_DIR}/.env"
echo ""
