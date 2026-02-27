#!/bin/bash
# otpi_bootstrap.sh — One-time setup for OTA updates on existing OTPi devices.
#
# Customers run ONE command over SSH, then never need the pi password again.
# This script:
#   1. Downloads ota_update.py, ota_config.json, version.txt
#   2. Creates the restricted "update" user
#   3. Changes the "pi" password to something random (locks out future pi access)
#   4. Prints the new update credentials
#
# Usage (sent to customer):
#   ssh pi@<device-ip>
#   (enter current pi password)
#   curl -fsSL https://raw.githubusercontent.com/mylesdedwards/OTPi/master/otpi_bootstrap.sh | sudo bash

set -e

# ── Configuration — EDIT THESE ──────────────────────────────────────
REPO_BASE="https://raw.githubusercontent.com/mylesdedwards/OTPi/master"
PROJECT_DIR="/home/otpi/otp_project"
UPDATE_USER="update"
UPDATE_PASS="otpi-update"
SERVICE_NAME="otpi.service"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   OTPi One-Time Update Setup         ║"
echo "╚══════════════════════════════════════╝"
echo ""

# ── Step 1: Download OTA files ──────────────────────────────────────
echo "[1/5] Downloading update system..."

cd "$PROJECT_DIR"

# Download OTA updater
curl -fsSL "${REPO_BASE}/ota_update.py" -o ota_update.py
echo "  ✓ ota_update.py"

# Download config (only if not already present — don't overwrite custom configs)
if [ ! -f ota_config.json ]; then
    curl -fsSL "${REPO_BASE}/ota_config.json" -o ota_config.json
    echo "  ✓ ota_config.json"
else
    echo "  ✓ ota_config.json (already exists, kept)"
fi

# Download version file (only if not present)
if [ ! -f version.txt ]; then
    curl -fsSL "${REPO_BASE}/version.txt" -o version.txt
    echo "  ✓ version.txt"
else
    echo "  ✓ version.txt (already exists, kept)"
fi

chmod 644 ota_update.py ota_config.json version.txt

# ── Step 2: Install qrcode if missing ──────────────────────────────
echo ""
echo "[2/5] Checking dependencies..."
python3 -c "import qrcode" 2>/dev/null || {
    echo "  Installing qrcode library..."
    pip3 install qrcode --break-system-packages -q 2>/dev/null || \
    apt-get install -y python3-qrcode -q 2>/dev/null || \
    echo "  (qrcode not available — QR display will be skipped, not critical)"
}
echo "  ✓ Dependencies checked"

# ── Step 3: Create restricted update user ───────────────────────────
echo ""
echo "[3/5] Creating update user..."

WRAPPER_SCRIPT="/usr/local/bin/otpi-update-wrapper"

# Create user if needed
if ! id "$UPDATE_USER" &>/dev/null; then
    useradd --system --shell /bin/false --no-create-home "$UPDATE_USER"
fi
echo "${UPDATE_USER}:${UPDATE_PASS}" | chpasswd

# Create the wrapper (forced command)
cat > "$WRAPPER_SCRIPT" << 'WRAPPER'
#!/bin/bash
PROJECT_DIR="/home/otpi/otp_project"
echo ""
echo "╔══════════════════════════════════════╗"
echo "║       OTPi Device Updater            ║"
echo "╚══════════════════════════════════════╝"
echo ""
if [ -f "${PROJECT_DIR}/version.txt" ]; then
    echo "Current version: $(cat ${PROJECT_DIR}/version.txt)"
else
    echo "Current version: unknown"
fi
echo ""
echo "Checking for updates..."
echo ""
cd "$PROJECT_DIR"
sudo /usr/bin/python3 "${PROJECT_DIR}/ota_update.py"
EXIT_CODE=$?
echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "Done! Your device is up to date."
else
    echo "Update check completed (no update available or server unreachable)."
fi
echo ""
echo "Current version: $(cat ${PROJECT_DIR}/version.txt 2>/dev/null || echo 'unknown')"
echo ""
echo "Connection will now close."
sleep 2
exit 0
WRAPPER

chmod 755 "$WRAPPER_SCRIPT"
usermod --shell "$WRAPPER_SCRIPT" "$UPDATE_USER"

# Sudoers for update user
cat > /etc/sudoers.d/otpi-update << EOF
${UPDATE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 ${PROJECT_DIR}/ota_update.py
${UPDATE_USER} ALL=(root) NOPASSWD: /usr/bin/python3 ${PROJECT_DIR}/ota_update.py *
${UPDATE_USER} ALL=(root) NOPASSWD: /bin/systemctl restart ${SERVICE_NAME}
EOF
chmod 440 /etc/sudoers.d/otpi-update

echo "  ✓ Update user created"

# ── Step 4: Configure SSH ──────────────────────────────────────────
echo ""
echo "[4/5] Configuring SSH..."

SSHD_CONFIG="/etc/ssh/sshd_config"
if ! grep -q "Match User ${UPDATE_USER}" "$SSHD_CONFIG" 2>/dev/null; then
    cat >> "$SSHD_CONFIG" << EOF

# OTPi: restricted update user
Match User ${UPDATE_USER}
    PasswordAuthentication yes
    X11Forwarding no
    AllowTcpForwarding no
    PermitTunnel no
    ForceCommand ${WRAPPER_SCRIPT}
EOF
    systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null
fi
echo "  ✓ SSH configured"

# ── Step 5: Set up daily auto-update timer ─────────────────────────
echo ""
echo "[5/5] Setting up automatic updates..."

cat > /etc/systemd/system/otpi-update.service << EOF
[Unit]
Description=OTPi OTA Update Check
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
WorkingDirectory=${PROJECT_DIR}
ExecStart=/usr/bin/python3 ${PROJECT_DIR}/ota_update.py
TimeoutStartSec=300
StandardOutput=journal
StandardError=journal
EOF

cat > /etc/systemd/system/otpi-update.timer << EOF
[Unit]
Description=OTPi Daily OTA Update Check
[Timer]
OnCalendar=*-*-* 02:00:00
RandomizedDelaySec=10800
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now otpi-update.timer
echo "  ✓ Daily auto-update enabled"

# ── Step 6: Lock down pi account ───────────────────────────────────
echo ""
echo "Securing device..."

# Generate a random password for the pi account
NEW_PI_PASS=$(head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 24)
echo "pi:${NEW_PI_PASS}" | chpasswd

echo "  ✓ Default access secured"

# ── Done ────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║         Setup Complete!              ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Your device will now update automatically."
echo ""
echo "To manually check for updates in the future:"
echo "  ssh update@<this-device-ip>"
echo "  Password: ${UPDATE_PASS}"
echo ""
echo "This SSH session will now end."
echo "The password you used to connect will no longer work."
echo ""
sleep 3
