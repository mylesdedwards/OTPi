#!/bin/bash
# build_update.sh — Developer tool to create an OTPi update bundle.
#
# Run this on your dev machine before pushing a release:
#   ./build_update.sh 1.1.0
#
# This creates:
#   version.txt           – contains the version string
#   otpi_update.tar.gz    – contains all .py files to deploy
#
# Then push both files to your GitHub repo or upload as a release.

set -e

VERSION="${1:?Usage: $0 <version>  (e.g. 1.1.0)}"

echo "Building OTPi update bundle v${VERSION}..."

# Write version file
echo "$VERSION" > version.txt
echo "  version.txt -> $VERSION"

# Collect project files to include in the bundle
# Add or remove files from this list as your project grows
FILES=(
    main.py
    led_display.py
    oled_ui.py
    start_ap_mode.py
    utils.py
    wifi_web.py
    lang.py
    encoder.py
    process_qr_image.py
    piows2812.py
    ota_update.py
)

# Filter to only files that exist
EXISTING=()
for f in "${FILES[@]}"; do
    if [ -f "$f" ]; then
        EXISTING+=("$f")
    else
        echo "  SKIP (not found): $f"
    fi
done

# Create the tarball
tar czf otpi_update.tar.gz "${EXISTING[@]}"
echo "  otpi_update.tar.gz -> ${#EXISTING[@]} files"

# Show bundle contents
echo ""
echo "Bundle contents:"
tar tzf otpi_update.tar.gz | sed 's/^/  /'

echo ""
echo "Ready! Upload these to your release:"
echo "  version.txt"
echo "  otpi_update.tar.gz"
echo ""
echo "If using GitHub, push to main branch or create a release."
