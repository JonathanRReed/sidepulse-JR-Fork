#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(/usr/bin/dirname "$0")/.." && /bin/pwd)"
ARCH="$(/usr/bin/uname -m)"
BUILD_DIR="${BUILD_ROOT:-$ROOT_DIR/build/macos-pkg}"
DIST_DIR="${OUTPUT_ROOT:-$ROOT_DIR/dist}"
RAW_EVIDENCE_DIR="$BUILD_DIR/release-evidence-raw"
SPARKLE_DISTRIBUTION="$BUILD_DIR/sparkle-distribution"
APP_NOTARY_ZIP="$BUILD_DIR/SidePulse-app-notary.zip"
REQUESTED_BUILD_PYTHON="${BUILD_PYTHON:-}"
CONSTRAINTS="$ROOT_DIR/requirements/release-constraints.txt"
LOCKFILE="$ROOT_DIR/requirements/release-lock.txt"
PINNED_PIP="26.1.2"
PINNED_PYINSTALLER="6.21.0"
VENV_DIR="$BUILD_DIR/venv"
APP_PATH="$BUILD_DIR/pyinstaller/SidePulse.app"
COMPONENT_PKG="$BUILD_DIR/SidePulse-component.pkg"
ENVIRONMENT_SNAPSHOT="$DIST_DIR/release-environment.txt"
APP_ID="io.sidepulse.app"
PRODUCT_DISPLAY_NAME="JR-Bar"
MINIMUM_SUPPORTED_MACOS="11.0"
APPLE_EVENTS_USAGE_DESCRIPTION="JR-Bar uses Automation only to open a reviewed resume command in Terminal or iTerm2 when you choose Open."
FOCUS_STATUS_USAGE_DESCRIPTION="JR-Bar uses Focus Status only when you choose Allow Focus Status, so Do Not Disturb can follow whether a macOS Focus is active."
SPARKLE_FEED_URL="https://github.com/JonathanRReed/sidepulse-JR-Fork/releases/download/updates/appcast.xml"
SPARKLE_PUBLIC_KEY_FILE="$ROOT_DIR/packaging/sparkle_public_ed_key.txt"

APP_SIGN_IDENTITY="${APP_SIGN_IDENTITY:-}"
INSTALLER_SIGN_IDENTITY="${INSTALLER_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${NOTARY_PROFILE:-}"
ALLOW_UNSIGNED="${ALLOW_UNSIGNED:-0}"
SPARKLE_ARCHIVE="${SPARKLE_ARCHIVE:-}"

select_build_python() {
    local candidate resolved
    local candidates=()

    if [ -n "$REQUESTED_BUILD_PYTHON" ]; then
        candidates=("$REQUESTED_BUILD_PYTHON")
    else
        candidates=(
            /opt/homebrew/bin/python3.12
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
        if "$resolved" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
            printf '%s\n' "$resolved"
            return 0
        fi
    done
    return 1
}

if [ ! -f "$CONSTRAINTS" ]; then
    echo "Missing reviewed release constraints: $CONSTRAINTS" >&2
    exit 2
fi
if [ ! -f "$LOCKFILE" ]; then
    echo "Missing hash-bound release lock: $LOCKFILE" >&2
    exit 2
fi

BUILD_PYTHON="$(select_build_python || true)"
if [ -z "$BUILD_PYTHON" ]; then
    echo "JR-Bar release packaging requires Python 3.12." >&2
    echo "Install Homebrew Python 3.12 or set BUILD_PYTHON to that interpreter." >&2
    exit 2
fi
if ! VERSION="$("$BUILD_PYTHON" "$ROOT_DIR/scripts/validate_release_version.py")"; then
    echo "Could not validate the JR-Bar release version." >&2
    exit 2
fi
OUTPUT_PKG="$("$BUILD_PYTHON" "$ROOT_DIR/scripts/release_artifact_contract.py" \
    --version "$VERSION" \
    --architecture "$ARCH" \
    --dist-dir "$DIST_DIR" \
    --format path)"
OUTPUT_ZIP="$("$BUILD_PYTHON" "$ROOT_DIR/scripts/release_artifact_contract.py" \
    --version "$VERSION" \
    --architecture "$ARCH" \
    --dist-dir "$DIST_DIR" \
    --format updater-path)"

if [ ! -f "$SPARKLE_PUBLIC_KEY_FILE" ]; then
    echo "Missing reviewed Sparkle public key: $SPARKLE_PUBLIC_KEY_FILE" >&2
    exit 2
fi
if ! SPARKLE_PUBLIC_ED_KEY="$("$BUILD_PYTHON" -c '
import base64
import binascii
import pathlib
import sys

key = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").strip()
try:
    decoded = base64.b64decode(key, validate=True)
except (binascii.Error, ValueError):
    raise SystemExit(1)
if len(decoded) != 32:
    raise SystemExit(1)
print(key)
' "$SPARKLE_PUBLIC_KEY_FILE")"; then
    echo "Sparkle public key must be one base64-encoded Ed25519 public key." >&2
    exit 2
fi

if [ "$ALLOW_UNSIGNED" != "1" ]; then
    if [ -z "$APP_SIGN_IDENTITY" ] || [ -z "$INSTALLER_SIGN_IDENTITY" ] || [ -z "$NOTARY_PROFILE" ]; then
        echo "Production packaging requires APP_SIGN_IDENTITY," >&2
        echo "INSTALLER_SIGN_IDENTITY, and NOTARY_PROFILE." >&2
        exit 2
    fi
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
"$BUILD_PYTHON" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))' || {
    echo "JR-Bar release packaging requires Python 3.12; got $($BUILD_PYTHON -V 2>&1)." >&2
    exit 2
}

echo "Building JR-Bar $VERSION for $ARCH with $($BUILD_PYTHON -V 2>&1)"
/bin/rm -rf "$BUILD_DIR"
/bin/mkdir -p "$BUILD_DIR" "$DIST_DIR"
/bin/mkdir -m 700 "$RAW_EVIDENCE_DIR"
export PIP_CACHE_DIR="$BUILD_DIR/pip-cache"
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_CONSTRAINT="$CONSTRAINTS"
export PIP_BUILD_CONSTRAINT="$CONSTRAINTS"
export PYTHONHASHSEED=0
export PYINSTALLER_CONFIG_DIR="$BUILD_DIR/pyinstaller-cache"
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git -C "$ROOT_DIR" show -s --format=%ct HEAD)}"
"$BUILD_PYTHON" -m venv "$VENV_DIR"
# --no-cache-dir is LOAD-BEARING: pip caches the built sidepulse wheel
# BY VERSION, so every rebuild between version bumps could silently ship
# a stale wheel from an older commit (it did: a deploy passed md5 parity
# against its own stale build while the source had moved two commits).
"$VENV_DIR/bin/python" -m pip install \
    --no-cache-dir \
    --require-hashes \
    --only-binary=:all: \
    --requirement "$LOCKFILE"
# pip 26 refuses a build constraint together with --no-build-isolation. The
# reviewed runtime constraint still applies through PIP_CONSTRAINT, and every
# build requirement is already installed from the hash-bound binary lock.
env -u PIP_BUILD_CONSTRAINT "$VENV_DIR/bin/python" -m pip install "$ROOT_DIR" --no-deps --no-build-isolation
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
    --copy-metadata sidepulse \
    --hidden-import sidepulse.creator_micro_adapter \
    --hidden-import sidepulse.creator_micro_hidapi \
    --hidden-import hid \
    "$ROOT_DIR/packaging/sidepulse_entry.py"

if [ -n "$SPARKLE_ARCHIVE" ]; then
    "$VENV_DIR/bin/python" "$ROOT_DIR/scripts/prepare_sparkle.py" --output "$SPARKLE_DISTRIBUTION" \
        --archive "$SPARKLE_ARCHIVE"
else
    "$VENV_DIR/bin/python" "$ROOT_DIR/scripts/prepare_sparkle.py" --output "$SPARKLE_DISTRIBUTION"
fi
if [ -e "$APP_PATH/Contents/Frameworks/Sparkle.framework" ] || \
    [ -L "$APP_PATH/Contents/Frameworks/Sparkle.framework" ]; then
    echo "Refusing to overwrite an unexpected embedded Sparkle.framework." >&2
    exit 2
fi
/bin/mkdir -p \
    "$APP_PATH/Contents/Frameworks" \
    "$APP_PATH/Contents/Resources/ThirdPartyLicenses"
# ditto preserves the framework's reviewed relative symlinks and executable bits.
/usr/bin/ditto \
    "$SPARKLE_DISTRIBUTION/Sparkle.framework" \
    "$APP_PATH/Contents/Frameworks/Sparkle.framework"
/usr/bin/ditto \
    "$SPARKLE_DISTRIBUTION/LICENSE" \
    "$APP_PATH/Contents/Resources/ThirdPartyLicenses/Sparkle.txt"

/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string $PRODUCT_DISPLAY_NAME" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName $PRODUCT_DISPLAY_NAME" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string $PRODUCT_DISPLAY_NAME" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :CFBundleName $PRODUCT_DISPLAY_NAME" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string $MINIMUM_SUPPORTED_MACOS" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :LSMinimumSystemVersion $MINIMUM_SUPPORTED_MACOS" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSAppleEventsUsageDescription string $APPLE_EVENTS_USAGE_DESCRIPTION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :NSAppleEventsUsageDescription $APPLE_EVENTS_USAGE_DESCRIPTION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :NSFocusStatusUsageDescription string $FOCUS_STATUS_USAGE_DESCRIPTION" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :NSFocusStatusUsageDescription $FOCUS_STATUS_USAGE_DESCRIPTION" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :SUFeedURL string $SPARKLE_FEED_URL" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :SUFeedURL $SPARKLE_FEED_URL" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :SUPublicEDKey string $SPARKLE_PUBLIC_ED_KEY" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :SUPublicEDKey $SPARKLE_PUBLIC_ED_KEY" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :SURequireSignedFeed bool true" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :SURequireSignedFeed true" "$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Add :SUVerifyUpdateBeforeExtraction bool true" "$APP_PATH/Contents/Info.plist" 2>/dev/null || \
    /usr/libexec/PlistBuddy -c "Set :SUVerifyUpdateBeforeExtraction true" "$APP_PATH/Contents/Info.plist"

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
sparkle_verify_args=("$APP_PATH")
if [ "$ALLOW_UNSIGNED" != "1" ]; then
    sparkle_verify_args+=(--production --expected-team "$SIGNED_TEAM")
fi
"$VENV_DIR/bin/python" "$ROOT_DIR/packaging/verify_sparkle_bundle.py" \
    "${sparkle_verify_args[@]}"

export COPYFILE_DISABLE=1
package_args=(
    --app "$APP_PATH"
    --scripts "$ROOT_DIR/packaging/scripts"
    --component-pkg "$COMPONENT_PKG"
    --output-pkg "$OUTPUT_PKG"
    --identifier "$APP_ID"
    --version "$VERSION"
)
if [ -n "$INSTALLER_SIGN_IDENTITY" ]; then
    package_args+=(--installer-sign-identity "$INSTALLER_SIGN_IDENTITY")
fi

if [ "$ALLOW_UNSIGNED" != "1" ]; then
    /usr/bin/ditto -c -k --keepParent "$APP_PATH" "$APP_NOTARY_ZIP"
    app_notary_response="$RAW_EVIDENCE_DIR/app-notary-submission.json"
    app_notary_log="$RAW_EVIDENCE_DIR/app-notary-log.json"
    app_submitted_sha="$RAW_EVIDENCE_DIR/app-notary-submitted-zip.sha256"
    /usr/bin/shasum -a 256 "$APP_NOTARY_ZIP" \
        | /usr/bin/awk '{print $1}' > "$app_submitted_sha"
    /usr/bin/xcrun notarytool submit "$APP_NOTARY_ZIP" \
        --keychain-profile "$NOTARY_PROFILE" \
        --wait \
        --output-format json > "$app_notary_response"
    app_submission_id="$("$BUILD_PYTHON" "$ROOT_DIR/scripts/release_evidence.py" \
        notary-submission-id \
        --response "$app_notary_response")"
    /usr/bin/xcrun notarytool log "$app_submission_id" \
        --keychain-profile "$NOTARY_PROFILE" \
        "$app_notary_log"
    /bin/chmod 600 "$app_notary_response" "$app_notary_log" "$app_submitted_sha"
    /usr/bin/xcrun stapler staple "$APP_PATH"
    /usr/bin/xcrun stapler validate "$APP_PATH"

    "$VENV_DIR/bin/python" "$ROOT_DIR/packaging/verify_macos_app.py" "$APP_PATH"
    "$VENV_DIR/bin/python" "$ROOT_DIR/packaging/verify_sparkle_bundle.py" \
        "$APP_PATH" \
        --production \
        --expected-team "$SIGNED_TEAM"
    "$VENV_DIR/bin/python" "$ROOT_DIR/scripts/package_sparkle_archive.py" \
        --app "$APP_PATH" \
        --output "$OUTPUT_ZIP"
    "$VENV_DIR/bin/python" "$ROOT_DIR/scripts/package_macos_artifact.py" \
        "${package_args[@]}"

    notary_response="$RAW_EVIDENCE_DIR/notary-submission.json"
    notary_log="$RAW_EVIDENCE_DIR/notary-log.json"
    submitted_sha="$RAW_EVIDENCE_DIR/notary-submitted-pkg.sha256"
    /usr/bin/shasum -a 256 "$OUTPUT_PKG" \
        | /usr/bin/awk '{print $1}' > "$submitted_sha"
    /usr/bin/xcrun notarytool submit "$OUTPUT_PKG" \
        --keychain-profile "$NOTARY_PROFILE" \
        --wait \
        --output-format json > "$notary_response"
    submission_id="$("$BUILD_PYTHON" "$ROOT_DIR/scripts/release_evidence.py" \
        notary-submission-id \
        --response "$notary_response")"
    /usr/bin/xcrun notarytool log "$submission_id" \
        --keychain-profile "$NOTARY_PROFILE" \
        "$notary_log"
    /bin/chmod 600 "$notary_response" "$notary_log" "$submitted_sha"
    /usr/bin/xcrun stapler staple "$OUTPUT_PKG"
    /usr/bin/xcrun stapler validate "$OUTPUT_PKG"
else
    "$VENV_DIR/bin/python" "$ROOT_DIR/scripts/package_macos_artifact.py" \
        "${package_args[@]}"
    echo "Built package: $OUTPUT_PKG"
    echo "ALLOW_UNSIGNED is local-only."
    echo "No updater archive or updater evidence was produced."
    echo "This package is not a production update candidate."
fi
