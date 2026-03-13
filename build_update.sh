#!/bin/bash
# build_update.sh — Developer tool to create an OTPi update bundle.
#
# Run this on your dev machine to build and publish a release:
#   ./build_update.sh 1.1.0
#
# This creates:
#   version.txt           – contains the version string
#   otpi_update.tar.gz    – contains all .py files to deploy
#
# Then uploads both as a GitHub Release (requires 'gh' CLI).
# Install gh: sudo apt install gh   (then: gh auth login)

set -e

VERSION="${1:?Usage: $0 <version>  (e.g. 1.1.0)}"

echo "Building OTPi update bundle v${VERSION}..."

# Write version file
echo "$VERSION" > version.txt
echo "  version.txt -> $VERSION"

# Collect project files to include in the bundle
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

# Commit and push to git
echo ""
echo "Committing to git..."
git add .
git commit -m "Release v${VERSION}" || echo "  (nothing new to commit)"
git push

# Create GitHub Release
echo ""
if command -v gh &>/dev/null; then
    echo "Creating GitHub Release v${VERSION}..."

    # Delete existing release with same tag if it exists
    gh release delete "v${VERSION}" --yes 2>/dev/null || true
    git tag -d "v${VERSION}" 2>/dev/null || true
    git push origin ":refs/tags/v${VERSION}" 2>/dev/null || true

    # Create new release with assets
    gh release create "v${VERSION}" \
        version.txt \
        otpi_update.tar.gz \
        --title "v${VERSION}" \
        --notes "OTPi firmware update v${VERSION}"

    echo ""
    echo "Release v${VERSION} published!"
    echo "Devices will pick this up automatically."
else
    echo "WARNING: 'gh' CLI not installed — Release not created."
    echo "Install it:  sudo apt install gh"
    echo "Then run:    gh auth login"
    echo ""
    echo "Or manually create a release at:"
    echo "  https://github.com/mylesdedwards/OTPi/releases/new"
    echo "  Tag: v${VERSION}"
    echo "  Attach: version.txt and otpi_update.tar.gz"
fi
