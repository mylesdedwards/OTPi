#!/bin/bash
# test.sh - Simple test runner that handles virtual environment

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Check if we're in the project directory
if [[ ! -f "main.py" ]]; then
    echo -e "${RED}Error: Run this from the project directory${NC}"
    exit 1
fi

# Check virtual environment
if [[ ! -f ".venv/bin/python" ]]; then
    echo -e "${RED}Error: Virtual environment not found${NC}"
    echo "Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Set environment variables
export PYTHONUNBUFFERED=1
export OTPI_DEBUG_ENCODER_EVENTS=1

# Check if running as root
if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}Re-running with sudo for GPIO access...${NC}"
    exec sudo --preserve-env=PYTHONUNBUFFERED,OTPI_DEBUG_ENCODER_EVENTS,PATH "$0" "$@"
fi

echo -e "${GREEN}=== OTPi Component Testing ===${NC}"

# Run the test with proper python path
./.venv/bin/python test_encoder.py "$@"
