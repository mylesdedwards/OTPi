#!/bin/bash
# dev.sh - Development testing script for OTPi

# Set development environment variables
export PYTHONUNBUFFERED=1
export OTPI_DEBUG_ENCODER_EVENTS=1  # Show encoder debug info
export OTPI_ENC_POLL_MS=1           # Fast encoder polling
export OTPI_ENC_BTN_DEBOUNCE_MS=10  # Button debounce

# Optional: Set specific hardware backends for testing
# export OTPI_GPIO_BACKEND=lgpio     # Force lgpio backend
# export OTPI_LED_BACKEND=neopixel   # Force neopixel backend

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}=== OTPi Development Mode ===${NC}"
echo "Project directory: $(pwd)"
echo "Virtual environment: $(.venv/bin/python --version)"
echo ""

# Check if running as root (needed for GPIO)
if [[ $EUID -ne 0 ]]; then
    echo -e "${YELLOW}Warning: Not running as root. GPIO access may fail.${NC}"
    echo "Consider running: sudo ./dev.sh"
    echo ""
fi

# Check virtual environment
if [[ ! -f ".venv/bin/python" ]]; then
    echo -e "${RED}Error: Virtual environment not found at .venv/${NC}"
    echo "Run: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# Check if packages are installed in venv
echo "Checking virtual environment packages..."
if ! .venv/bin/python -c "import luma.oled.device, neopixel, pyotp" 2>/dev/null; then
    echo -e "${YELLOW}Warning: Some packages missing in virtual environment${NC}"
    echo "Run: .venv/bin/pip install -r requirements.txt"
    echo ""
fi

# Check required files
missing_files=()
for file in "main.py" "oled_ui.py" "led_display.py" "encoder.py"; do
    if [[ ! -f "$file" ]]; then
        missing_files+=("$file")
    fi
done

if [[ ${#missing_files[@]} -gt 0 ]]; then
    echo -e "${RED}Error: Missing required files: ${missing_files[*]}${NC}"
    exit 1
fi

# Show current configuration
echo -e "${YELLOW}Current configuration:${NC}"
echo "  Encoder CLK: ${OTPI_ENC_CLK:-23}"
echo "  Encoder DT:  ${OTPI_ENC_DT:-24}" 
echo "  Encoder SW:  ${OTPI_ENC_SW:-25}"
echo "  LED Pin:     ${OTPI_LED_PIN:-18}"
echo "  Debug Mode:  ${OTPI_DEBUG_ENCODER_EVENTS:-0}"
echo ""

# Function to cleanup on exit
cleanup() {
    echo -e "\n${YELLOW}Cleaning up...${NC}"
    # Kill any background processes if needed
    pkill -f "python.*main.py" 2>/dev/null || true
    echo -e "${GREEN}Cleanup complete${NC}"
}

trap cleanup EXIT INT TERM

# Run the application
echo -e "${GREEN}Starting application...${NC}"
echo "Press Ctrl+C to stop"
echo ""

# Run with preserved environment
sudo --preserve-env=PYTHONUNBUFFERED,OTPI_ENC_CLK,OTPI_ENC_DT,OTPI_ENC_SW,OTPI_ENC_BTN_ACTIVE_LOW,OTPI_ENC_PPR,OTPI_ENC_POLL_MS,OTPI_GPIO_BACKEND,OTPI_LED_BACKEND,OTPI_DEBUG_ENCODER_EVENTS,PATH \
  ./.venv/bin/python -u main.py

echo -e "\n${YELLOW}Application stopped${NC}"
