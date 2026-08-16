#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(/usr/bin/dirname "$0")/.." && /bin/pwd)"
VERSION="$(/usr/bin/sed -n 's/^version = "\([^"]*\)"/\1/p' "$ROOT_DIR/pyproject.toml" | /usr/bin/head -1)"
ARCH="$(/usr/bin/uname -m)"
BUILD_DIR="${BUILD_ROOT:-$ROOT_DIR/build/macos-pkg}"
DIST_DIR="${OUTPUT_ROOT:-$ROOT_DIR/dist}"
REQUESTED_BUILD_PYTHON="${BUILD_PYTHON:-}"
CONSTRAINTS="$ROOT_DIR/requirements/release-constraints.txt"
PINNED_PIP="26.1.2"
PINNED_PYINSTALLER="6.21.0"
VENV_DIR="$BUILD_DIR/venv"
APP_PATH="$BUILD_DIR/pyinstaller/SidePulse.app"
COMPONENT_PKG="$BUILD_DIR/SidePulse-component.pkg"
OUTPUT_PKG="$DIST_DIR/SidePulse-${VERSION}-${ARCH}.pkg"
ENVIRONMENT_SNAPSHOT="$DIST_DIR/release-environment.txt"
APP_ID="io.sidepulse.app"
APPLE_EVENTS_USAGE_DESCRIPTION="SidePulse uses Automation only to open a reviewed resume command in Terminal or iTerm2 when you choose Open."

APP_SIGN_IDENTITY="${APP_SIGN_IDENTITY:-}"
INSTALLER_SIGN_IDENTITY="${INSTALLER_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
ALLOW_UNSIGNED="${ALLOW_UNSIGNED:-0}"

select_build_python() {
    local candidate resolved
    local candidates=()

    if [ -n "$REQUESTED_BUILD_PYTHON" ]; then
        candidates=("$REQUESTED_BUILD_PYTHON")
    else
        candidates=(
            /opt/homebrew/bin/python3.13
            python3.13
            python3.12
            python3.11
            python3.10
            /opt/homebrew/bin/python3
            /usr/local/bin/python3
            python3
        )
    fi

    for candidate in "${candidates[@]}"; do
        if [[ "$candidate" = /* ]]; then
            resolved="$candidate"
        else
            resolved="$(command -v "$candidate" 2>/dev/null || true)"
        fi
        if [ -z "$resolved" ] || [ ! -x "$resolved" ]; then
            continue
        fi
        if "$resolved" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

if [ -z "$VERSION" ]; then
    echo "Could not read the SidePulse version from pyproject.toml." >&2
    exit 2
fi
if [ ! -f "$CONSTRAINTS" ]; then
    echo "Missing reviewed release constraints: $CONSTRAINTS" >&2
    exit 2
fi

BUILD_PYTHON="$(select_build_python || true)"
if [ -z "$BUILD_PYTHON" ]; then
    echo "SidePulse requires Python 3.10+ to build the macOS package." >&2
    echo "Install Homebrew Python 3.13 or set BUILD_PYTHON to a supported interpreter." >&2
    exit 2
fi

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
        echo "BUILD_PYTHON must resolve to an absolute path: $BUILD_PYTHON" >&2
        exit 2
        ;;
esac
if [ ! -x "$BUILD_PYTHON" ]; then
    echo "BUILD_PYTHON is missing or not executable: $BUILD_PYTHON" >&2
    exit 2
fi
"$BUILD_PYTHON" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' || {
    echo "SidePulse requires Python 3.10+; got $($BUILD_PYTHON -V 2>&1)." >&2
    exit 2
}

echo "Building SidePulse $VERSION for $ARCH with $($BUILD_PYTHON -V 2>&1)"
/bin/rm -rf "$BUILD_DIR"
/bin/mkdir -p "$BUILD_DIR" "$DIST_DIR"
export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_CONSTRAINT="$CONSTRAINTS"
export PIP_BUILD_CONSTRAINT="$CONSTRAINTS"
export PYTHONHASHSEED=0
export PYINSTALLER_CONFIG_DIR="$BUILD_DIR/pyinstaller-cache"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$ROOT_DIR" show -s --format=%ct HEAD)}"
"$BUILD_PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install "pip==$PINNED_PIP"
"$VENV_DIR/bin/python" -m pip install \
    --constraint "$CONSTRAINTS" \
    --only-binary=:all: \
    "pyinstaller==$PINNED_PYINSTALLER" \
    "$ROOT_DIR"
"$VENV_DIR/bin/python" -m pip check
LC_ALL=C "$VENV_DIR/bin/python" -m pip list --format=freeze \
    | /usr/bin/sort > "$ENVIRONMENT_SNAPSHOT"

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
    SIGN_IDENTITY="$APP_SIGN_IDENTITY"
else
    echo "WARNING: no APP_SIGN_IDENTITY -- signing AD HOC." >&2
    echo "         An ad-hoc bundle has a different code identity, so macOS" >&2
    echo "         treats it as a DIFFERENT APP: Full Disk Access, Screen" >&2
    echo "         Recording and Notification grants will be lost, and" >&2
    echo "         Gatekeeper will reject it. Local testing only." >&2
    SIGN_IDENTITY="-"
fi

"$VENV_DIR/bin/python" "$ROOT_DIR/packaging/sign_macos_app.py" \
    "$APP_PATH" \
    --identity "$SIGN_IDENTITY" \
    --entitlements "$ROOT_DIR/packaging/entitlements.plist"

if [ -n "$APP_SIGN_IDENTITY" ]; then
    SIGNED_TEAM="$(/usr/bin/codesign -dv --verbose=4 "$APP_PATH" 2>&1 \
        | /usr/bin/awk -F= '/^TeamIdentifier=/ {print $2}')"
    if [ -z "$SIGNED_TEAM" ] || [ "$SIGNED_TEAM" = "not set" ]; then
        echo "FATAL: asked for '$APP_SIGN_IDENTITY' but the bundle carries no" >&2
        echo "       TeamIdentifier -- it is ad-hoc signed. Refusing to ship a" >&2
        echo "       bundle that would silently lose the user's TCC grants." >&2
        exit 1
    fi
    echo "signed by team $SIGNED_TEAM"
fi

"$VENV_DIR/bin/python" "$ROOT_DIR/packaging/verify_macos_app.py" "$APP_PATH"
"$VENV_DIR/bin/python" "$ROOT_DIR/packaging/verify_entitlements.py" "$APP_PATH"

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
