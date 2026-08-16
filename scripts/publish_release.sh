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
if [ ! -x "$PYTHON" ]; then
    echo "Missing development environment. Run ./scripts/bootstrap-dev.sh first." >&2
    exit 2
fi
if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI is required. Install gh and run gh auth login." >&2
    exit 2
fi
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "Refusing to release a dirty or untracked working tree." >&2
    exit 2
fi
if [ "$(git branch --show-current)" != "main" ]; then
    echo "Refusing to release outside main." >&2
    exit 2
fi

git fetch --quiet origin main --tags
head_sha="$(git rev-parse HEAD)"
if [ "$head_sha" != "$(git rev-parse origin/main)" ]; then
    echo "Local main is not exactly origin/main." >&2
    exit 2
fi

version="$("$PYTHON" scripts/validate_release_version.py)"
tag="v$version"
if git rev-parse "$tag" >/dev/null 2>&1 || \
   git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1; then
    echo "Tag already exists: $tag" >&2
    exit 2
fi
if gh release view "$tag" --repo JonathanRReed/sidepulse-JR-Fork >/dev/null 2>&1; then
    echo "Release already exists: $tag" >&2
    exit 2
fi

if [ "$PYTHON_ONLY" -eq 0 ]; then
    ./scripts/verify_macos_release.sh
else
    ./scripts/verify.sh --no-bootstrap --portable
fi

artifacts=(
    dist/*.whl
    dist/*.tar.gz
    dist/release-environment.txt
    dist/sidepulse-sbom.cdx.json
)
if [ "$PYTHON_ONLY" -eq 0 ]; then
    artifacts+=(
        dist/SidePulse-"$version"-*.pkg
        dist/release-verification.json
    )
fi
for artifact in "${artifacts[@]}"; do
    if [ ! -f "$artifact" ]; then
        echo "Release artifact is missing: $artifact" >&2
        exit 1
    fi
done
shasum -a 256 "${artifacts[@]}" > dist/SHA256SUMS
artifacts+=(dist/SHA256SUMS)

release_created=0
rollback() {
    status=$?
    if [ "$status" -ne 0 ] && [ "$release_created" -eq 1 ]; then
        gh release delete "$tag" \
            --repo JonathanRReed/sidepulse-JR-Fork \
            --yes \
            --cleanup-tag >/dev/null 2>&1 || true
    fi
    exit "$status"
}
trap rollback EXIT

gh release create "$tag" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --target "$head_sha" \
    --title "SidePulse $version" \
    --generate-notes \
    --draft
release_created=1
gh release upload "$tag" "${artifacts[@]}" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --clobber
gh release edit "$tag" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --draft=false
release_created=0
trap - EXIT

printf '%s\n' "Published $tag"
