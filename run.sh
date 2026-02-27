#!/usr/bin/env bash
set -euo pipefail
cd /home/otpi/otp_project
VENV=/home/otpi/otp_project/.venv
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -U pip wheel
"$VENV/bin/pip" install -r requirements.txt
sudo --preserve-env=PATH "$VENV/bin/python" main.py
