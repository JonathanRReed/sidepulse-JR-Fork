#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
PYTHON_ONLY=0

if [ "${1:-}" = "--python-only" ]; then
    PYTHON_ONLY=1
elif [ -n "${1:-}" ]; then
    echo "Usage: $0 [--python-only]" >&2
    exit 2
fi

cd "$ROOT_DIR"
if [ "$(uname -s)" != "Darwin" ] && [ "$PYTHON_ONLY" -eq 0 ]; then
    echo "A signed SidePulse app/package release must be built on macOS." >&2
    exit 2
fi
if [ ! -x "$PYTHON" ]; then
    echo "Missing development environment. Run ./scripts/bootstrap-dev.sh first." >&2
    exit 2
fi
if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI is required. Install gh and run gh auth login." >&2
    exit 2
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Refusing to release a dirty working tree." >&2
    exit 2
fi
if [ "$(git branch --show-current)" != "main" ] && [ "${SIDEPULSE_ALLOW_NON_MAIN_RELEASE:-0}" != "1" ]; then
    echo "Refusing to release outside main. Set SIDEPULSE_ALLOW_NON_MAIN_RELEASE=1 to override." >&2
    exit 2
fi

version="$("$PYTHON" scripts/validate_release_version.py)"
tag="v$version"
if git rev-parse "$tag" >/dev/null 2>&1; then
    echo "Tag already exists: $tag" >&2
    exit 2
fi

./scripts/verify.sh --no-bootstrap
artifacts=(dist/*.whl dist/*.tar.gz)

if [ "$PYTHON_ONLY" -eq 0 ]; then
    : "${APP_SIGN_IDENTITY:?Set APP_SIGN_IDENTITY to a Developer ID Application identity}"
    : "${INSTALLER_SIGN_IDENTITY:?Set INSTALLER_SIGN_IDENTITY to a Developer ID Installer identity}"
    : "${NOTARY_PROFILE:?Set NOTARY_PROFILE to a notarytool keychain profile}"
    APP_SIGN_IDENTITY="$APP_SIGN_IDENTITY" \
    INSTALLER_SIGN_IDENTITY="$INSTALLER_SIGN_IDENTITY" \
    NOTARY_PROFILE="$NOTARY_PROFILE" \
    BUILD_PYTHON="$PYTHON" \
        ./packaging/build_macos_pkg.sh
    artifacts+=(dist/SidePulse-"$version"-*.pkg)
fi

shasum -a 256 "${artifacts[@]}" > dist/SHA256SUMS
artifacts+=(dist/SHA256SUMS)

git tag -a "$tag" -m "SidePulse $version"
git push origin "$tag"
gh release create "$tag" "${artifacts[@]}" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --title "SidePulse $version" \
    --generate-notes

printf '%s\n' "Published $tag"
