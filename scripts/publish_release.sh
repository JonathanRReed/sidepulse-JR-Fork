#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"

if [ -n "${1:-}" ]; then
    echo "Usage: $0" >&2
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

./scripts/verify_macos_release.sh

arch="$(/usr/bin/uname -m)"
macos_artifact="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir dist \
    --format path)"
update_archive="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir dist \
    --format updater-path)"
appcast="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir dist \
    --format appcast-path)"
channel_metadata="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir dist \
    --format channel-metadata-path)"
developer_artifact_output="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir dist \
    --format developer-paths)"
immutable_artifacts=()
while IFS= read -r artifact; do
    if [ -n "$artifact" ]; then
        immutable_artifacts+=("$artifact")
    fi
done <<< "$developer_artifact_output"
if [ "${#immutable_artifacts[@]}" -ne 2 ]; then
    echo "Release artifact contract did not return one wheel and one sdist." >&2
    exit 1
fi
immutable_artifacts+=(
    dist/release-environment.txt
    dist/performance-evidence.json
    dist/sidepulse-sbom.cdx.json
    "$macos_artifact"
    "$update_archive"
    dist/release-verification.json
)
feed_artifacts=("$channel_metadata" "$appcast")
checksum_artifacts=("${immutable_artifacts[@]}" "${feed_artifacts[@]}")
for artifact in "${checksum_artifacts[@]}"; do
    if [ ! -f "$artifact" ]; then
        echo "Release artifact is missing: $artifact" >&2
        exit 1
    fi
done
"$PYTHON" scripts/generate_release_checksums.py \
    --root "$ROOT_DIR" \
    --output "$ROOT_DIR/dist/SHA256SUMS" \
    --evidence-manifest "$ROOT_DIR/dist/release-verification.json" \
    "${checksum_artifacts[@]}"
immutable_artifacts+=(dist/SHA256SUMS)

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
    --title "JR-Bar $version" \
    --generate-notes \
    --draft
release_created=1
gh release upload "$tag" "${immutable_artifacts[@]}" \
    --repo JonathanRReed/sidepulse-JR-Fork
gh release edit "$tag" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --draft=false
release_created=0

archive_name="$(/usr/bin/basename "$update_archive")"
published_assets="$(gh release view "$tag" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --json assets \
    --jq '.assets[].name')"
archive_available=0
while IFS= read -r asset_name; do
    if [ "$asset_name" = "$archive_name" ]; then
        archive_available=1
        break
    fi
done <<< "$published_assets"
if [ "$archive_available" -ne 1 ]; then
    echo "Published version release is missing updater archive: $archive_name" >&2
    exit 1
fi

updates_created=0
if ! gh release view updates --repo JonathanRReed/sidepulse-JR-Fork >/dev/null 2>&1; then
    gh release create updates \
        --repo JonathanRReed/sidepulse-JR-Fork \
        --target "$head_sha" \
        --title "JR-Bar Updates" \
        --notes "Durable signed Sparkle update feed." \
        --draft
    updates_created=1
fi
# Upload metadata first. The signed appcast is the client-visible pointer and
# changes only after the immutable versioned archive is confirmed available.
gh release upload updates "$channel_metadata" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --clobber
gh release upload updates "$appcast" \
    --repo JonathanRReed/sidepulse-JR-Fork \
    --clobber
if [ "$updates_created" -eq 1 ]; then
    gh release edit updates \
        --repo JonathanRReed/sidepulse-JR-Fork \
        --draft=false
fi
trap - EXIT

printf '%s\n' "Published $tag"
