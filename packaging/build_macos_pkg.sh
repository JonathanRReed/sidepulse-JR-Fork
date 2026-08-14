#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(/usr/bin/dirname "$0")/.." && /bin/pwd)"
VERSION="$(/usr/bin/sed -n 's/^version = "\([^"]*\)"/\1/p' "$ROOT_DIR/pyproject.toml" | /usr/bin/head -1)"
ARCH="$(/usr/bin/uname -m)"
BUILD_DIR="${BUILD_ROOT:-$ROOT_DIR/build/macos-pkg}"
DIST_DIR="${OUTPUT_ROOT:-$ROOT_DIR/dist}"
BUILD_PYTHON="${BUILD_PYTHON:-/usr/bin/python3}"
VENV_DIR="$BUILD_DIR/venv"
APP_PATH="$BUILD_DIR/pyinstaller/SidePulse.app"
COMPONENT_PKG="$BUILD_DIR/SidePulse-component.pkg"
OUTPUT_PKG="$DIST_DIR/SidePulse-${VERSION}-${ARCH}.pkg"
APP_ID="io.sidepulse.app"
APPLE_EVENTS_USAGE_DESCRIPTION="SidePulse uses Automation only to open a reviewed resume command in Terminal or iTerm2 when you choose Open."

APP_SIGN_IDENTITY="${APP_SIGN_IDENTITY:-}"
INSTALLER_SIGN_IDENTITY="${INSTALLER_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
ALLOW_UNSIGNED="${ALLOW_UNSIGNED:-0}"

if { [ -z "$APP_SIGN_IDENTITY" ] || [ -z "$INSTALLER_SIGN_IDENTITY" ]; } && [ "$ALLOW_UNSIGNED" != "1" ]; then
    echo "Set APP_SIGN_IDENTITY to a Developer ID Application identity and" >&2
    echo "INSTALLER_SIGN_IDENTITY to a Developer ID Installer identity." >&2
    exit 2
fi

case "$BUILD_DIR" in
    ""|"/")
        echo "Refusing unsafe BUILD_ROOT: $BUILD_DIR" >&2
        exit 2
        ;;
esac
case "$DIST_DIR" in
    ""|"/")
        echo "Refusing unsafe OUTPUT_ROOT: $DIST_DIR" >&2
        exit 2
        ;;
esac
case "$BUILD_PYTHON" in
    /*) ;;
    *)
        echo "BUILD_PYTHON must be an absolute path: $BUILD_PYTHON" >&2
        exit 2
        ;;
esac
if [ ! -x "$BUILD_PYTHON" ]; then
    echo "BUILD_PYTHON is missing or not executable: $BUILD_PYTHON" >&2
    exit 2
fi

/bin/rm -rf "$BUILD_DIR"
/bin/mkdir -p "$BUILD_DIR" "$DIST_DIR"
export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PYINSTALLER_CONFIG_DIR="$BUILD_DIR/pyinstaller-cache"
"$BUILD_PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install 'pyinstaller>=6.10' "$ROOT_DIR"

"$VENV_DIR/bin/pyinstaller" \
    --noconfirm --clean --windowed \
    --name SidePulse \
    --osx-bundle-identifier "$APP_ID" \
    --distpath "$BUILD_DIR/pyinstaller" \
    --workpath "$BUILD_DIR/work" \
    --specpath "$BUILD_DIR" \
    --collect-submodules Cocoa \
    --collect-data sidepulse.resources \
    "$ROOT_DIR/packaging/sidepulse_entry.py"

/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string $APPLE_EVENTS_USAGE_DESCRIPTION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :NSAppleEventsUsageDescription $APPLE_EVENTS_USAGE_DESCRIPTION" "$APP_PATH/Contents/Info.plist"

# Downloads and copied workspace resources can carry Finder or provenance
# metadata that codesign rejects. Limit cleanup to the isolated candidate.
/usr/bin/xattr -cr "$APP_PATH"

if [ -n "$APP_SIGN_IDENTITY" ]; then
    /usr/bin/codesign --force --deep --options runtime --timestamp \
        --entitlements "$ROOT_DIR/packaging/entitlements.plist" \
        --sign "$APP_SIGN_IDENTITY" "$APP_PATH"
else
    /usr/bin/codesign --force --deep --sign - "$APP_PATH"
fi

# Verify both Developer ID and local ad-hoc candidates. A successful signing
# command is not evidence that every nested item or bundle attribute is valid.
/usr/bin/xattr -cr "$APP_PATH"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$APP_PATH"

"$VENV_DIR/bin/python" "$ROOT_DIR/packaging/verify_macos_app.py" "$APP_PATH"

if [ ! -x "$ROOT_DIR/packaging/scripts/postinstall" ]; then
    echo "packaging/scripts/postinstall must be executable" >&2
    exit 2
fi
export COPYFILE_DISABLE=1
/usr/bin/pkgbuild \
    --component "$APP_PATH" \
    --install-location /Applications \
    --identifier "$APP_ID" \
    --version "$VERSION" \
    --scripts "$ROOT_DIR/packaging/scripts" \
    "$COMPONENT_PKG"
if [ -n "$INSTALLER_SIGN_IDENTITY" ]; then
    /usr/bin/productbuild --package "$COMPONENT_PKG" \
        --sign "$INSTALLER_SIGN_IDENTITY" \
        --timestamp "$OUTPUT_PKG"
    /usr/sbin/pkgutil --check-signature "$OUTPUT_PKG"
else
    /usr/bin/productbuild --package "$COMPONENT_PKG" "$OUTPUT_PKG"
fi

if [ -n "$NOTARY_PROFILE" ] && [ -n "$INSTALLER_SIGN_IDENTITY" ]; then
    /usr/bin/xcrun notarytool submit "$OUTPUT_PKG" --keychain-profile "$NOTARY_PROFILE" --wait
    /usr/bin/xcrun stapler staple "$OUTPUT_PKG"
    /usr/bin/xcrun stapler validate "$OUTPUT_PKG"
else
    echo "Built package: $OUTPUT_PKG"
    if [ -n "$INSTALLER_SIGN_IDENTITY" ]; then
        echo "Set NOTARY_PROFILE to submit and staple it automatically."
    else
        echo "Local verification build only: this package is not Developer ID signed or notarized."
    fi
fi
