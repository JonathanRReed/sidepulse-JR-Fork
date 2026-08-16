#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
PERFORMANCE_EVIDENCE="${SIDEPULSE_PERFORMANCE_EVIDENCE:-}"
REQUIRED_HARDWARE="${SIDEPULSE_REQUIRED_HARDWARE:-both}"
SETTINGS_PATH="${SIDEPULSE_SETTINGS_PATH:-$HOME/.config/sidepulse/agent-monitor/settings.json}"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "The authoritative SidePulse release gate requires macOS." >&2
    exit 2
fi
if [ ! -x "$PYTHON" ]; then
    echo "Missing development environment. Run ./scripts/bootstrap-dev.sh." >&2
    exit 2
fi
: "${APP_SIGN_IDENTITY:?Set APP_SIGN_IDENTITY}"
: "${INSTALLER_SIGN_IDENTITY:?Set INSTALLER_SIGN_IDENTITY}"
: "${NOTARY_PROFILE:?Set NOTARY_PROFILE}"
if [ -z "$PERFORMANCE_EVIDENCE" ] || [ ! -f "$PERFORMANCE_EVIDENCE" ]; then
    echo "Set SIDEPULSE_PERFORMANCE_EVIDENCE to measured JSON evidence." >&2
    exit 2
fi
if [ "${SIDEPULSE_RUN_INSTALLED_UPGRADE:-0}" != "1" ]; then
    echo "Set SIDEPULSE_RUN_INSTALLED_UPGRADE=1 to authorize the upgrade gate." >&2
    exit 2
fi
if [ "${SIDEPULSE_HARDWARE_CONFIRM:-0}" != "1" ]; then
    echo "Set SIDEPULSE_HARDWARE_CONFIRM=1 to authorize reversible hardware writes." >&2
    exit 2
fi

cd "$ROOT_DIR"
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "Refusing release verification from a dirty or untracked tree." >&2
    exit 2
fi
if [ "$(git branch --show-current)" != "main" ]; then
    echo "Authoritative release verification must run from main." >&2
    exit 2
fi
git fetch --quiet origin main --tags
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
    echo "Local main is not exactly origin/main." >&2
    exit 2
fi

./scripts/verify.sh --no-bootstrap
"$PYTHON" scripts/verify_performance_budget.py "$PERFORMANCE_EVIDENCE"

APP_SIGN_IDENTITY="$APP_SIGN_IDENTITY" \
INSTALLER_SIGN_IDENTITY="$INSTALLER_SIGN_IDENTITY" \
NOTARY_PROFILE="$NOTARY_PROFILE" \
BUILD_PYTHON="$PYTHON" \
    ./packaging/build_macos_pkg.sh

version="$("$PYTHON" scripts/validate_release_version.py)"
arch="$(/usr/bin/uname -m)"
pkg="$ROOT_DIR/dist/SidePulse-$version-$arch.pkg"
app="$ROOT_DIR/build/macos-pkg/pyinstaller/SidePulse.app"
if [ ! -f "$pkg" ] || [ ! -d "$app" ]; then
    echo "Signed release artifacts are missing." >&2
    exit 1
fi

/usr/sbin/pkgutil --check-signature "$pkg"
/usr/sbin/spctl -a -vv -t install "$pkg"
/usr/bin/xcrun stapler validate "$pkg"
/usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
/usr/sbin/spctl -a -vv "$app"
"$PYTHON" packaging/verify_macos_app.py "$app"

"$PYTHON" scripts/verify_hardware_release.py \
    --confirm-write \
    --require "$REQUIRED_HARDWARE"

if [ ! -f "$SETTINGS_PATH" ]; then
    echo "Installed-upgrade verification requires settings: $SETTINGS_PATH" >&2
    exit 2
fi
before_settings="$(/usr/bin/mktemp -t sidepulse-settings-before.XXXXXX.json)"
cleanup() {
    /bin/rm -f "$before_settings"
}
trap cleanup EXIT
/bin/cp "$SETTINGS_PATH" "$before_settings"

expected_team="$(/usr/bin/codesign -dv --verbose=4 "$app" 2>&1 \
    | /usr/bin/awk -F= '/^TeamIdentifier=/ {print $2}')"
if [ -z "$expected_team" ] || [ "$expected_team" = "not set" ]; then
    echo "Signed candidate has no TeamIdentifier." >&2
    exit 1
fi

/usr/bin/sudo /usr/sbin/installer -pkg "$pkg" -target /
"$PYTHON" scripts/verify_installed_upgrade.py \
    --before-settings "$before_settings" \
    --settings "$SETTINGS_PATH" \
    --expected-team "$expected_team"

artifacts=(dist/*.whl dist/*.tar.gz "$pkg")
sbom_args=(
    --output dist/sidepulse-sbom.cdx.json
    --application-version "$version"
)
manifest_args=(
    --root "$ROOT_DIR"
    --output dist/release-verification.json
    --version "$version"
    --app "$app"
    --performance-evidence "$PERFORMANCE_EVIDENCE"
    --hardware-requirement "$REQUIRED_HARDWARE"
    --sbom dist/sidepulse-sbom.cdx.json
)
for artifact in "${artifacts[@]}"; do
    sbom_args+=(--artifact "$artifact")
    manifest_args+=(--artifact "$artifact")
done
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
    "$PYTHON" scripts/generate_sbom.py "${sbom_args[@]}"
"$PYTHON" scripts/generate_release_manifest.py "${manifest_args[@]}"

printf '%s\n' "Authoritative SidePulse macOS release gate passed."
