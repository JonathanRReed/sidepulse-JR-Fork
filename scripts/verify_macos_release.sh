#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT_DIR/.venv/bin/python}"
PERFORMANCE_SOURCE="${SIDEPULSE_PERFORMANCE_EVIDENCE:-}"
REQUIRED_HARDWARE="${SIDEPULSE_REQUIRED_HARDWARE:-software}"
SETTINGS_PATH="${SIDEPULSE_SETTINGS_PATH:-$HOME/.config/sidepulse/agent-monitor/settings.json}"
RELEASE_USER="${SIDEPULSE_RELEASE_USER:-$(/usr/bin/id -un)}"
EVIDENCE_DIR="$ROOT_DIR/dist/release-evidence"
PERFORMANCE_EVIDENCE="$ROOT_DIR/dist/performance-evidence.json"
RELEASE_CHANNEL="${SIDEPULSE_RELEASE_CHANNEL:-stable}"
SPARKLE_HISTORY_DIR="${SIDEPULSE_SPARKLE_HISTORY_DIR:-}"

if [ "$(uname -s)" != "Darwin" ]; then
    echo "The authoritative JR-Bar release gate requires macOS." >&2
    exit 2
fi
if [ ! -x "$PYTHON" ]; then
    echo "Missing development environment. Run ./scripts/bootstrap-dev.sh." >&2
    exit 2
fi
: "${APP_SIGN_IDENTITY:?Set APP_SIGN_IDENTITY}"
: "${INSTALLER_SIGN_IDENTITY:?Set INSTALLER_SIGN_IDENTITY}"
: "${NOTARY_PROFILE:?Set NOTARY_PROFILE}"
: "${SPARKLE_KEY_ACCOUNT:?Set SPARKLE_KEY_ACCOUNT}"
case "$RELEASE_CHANNEL" in
    stable|beta) ;;
    *) echo "SIDEPULSE_RELEASE_CHANNEL must be stable or beta." >&2; exit 2 ;;
esac
if [ -z "$PERFORMANCE_SOURCE" ] || [ ! -f "$PERFORMANCE_SOURCE" ]; then
    echo "Set SIDEPULSE_PERFORMANCE_EVIDENCE to measured JSON evidence." >&2
    exit 2
fi
if [ "${SIDEPULSE_RUN_INSTALLED_UPGRADE:-0}" != "1" ]; then
    echo "Set SIDEPULSE_RUN_INSTALLED_UPGRADE=1 to authorize the upgrade gate." >&2
    exit 2
fi
if [ "${SIDEPULSE_RUN_UNINSTALL:-0}" != "1" ]; then
    echo "Set SIDEPULSE_RUN_UNINSTALL=1 to authorize uninstall verification." >&2
    exit 2
fi
case "$REQUIRED_HARDWARE" in
    software) ;;
    any|pro|dot|both)
        if [ "${SIDEPULSE_HARDWARE_CONFIRM:-0}" != "1" ]; then
            echo "Set SIDEPULSE_HARDWARE_CONFIRM=1 to authorize reversible hardware writes." >&2
            exit 2
        fi
        ;;
    *) echo "SIDEPULSE_REQUIRED_HARDWARE must be software, any, pro, dot, or both." >&2; exit 2 ;;
esac
if [ "$RELEASE_USER" != "$(/usr/bin/id -un)" ]; then
    echo "Run the release gate while logged in as SIDEPULSE_RELEASE_USER." >&2
    exit 2
fi

cd "$ROOT_DIR"
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "Refusing release verification from a dirty or untracked tree." >&2
    exit 2
fi
git fetch --quiet origin main --tags
head_commit="$(git rev-parse HEAD)"
origin_main_commit="$(git rev-parse origin/main)"
current_branch="$(git branch --show-current)"
if [ -n "$current_branch" ] && [ "$current_branch" != "main" ]; then
    echo "Authoritative release verification must run from main or its detached commit." >&2
    exit 2
fi
if [ "$head_commit" != "$origin_main_commit" ]; then
    echo "Release commit is not exactly the freshly fetched origin/main." >&2
    exit 2
fi

APP_SIGN_IDENTITY="$APP_SIGN_IDENTITY" \
INSTALLER_SIGN_IDENTITY="$INSTALLER_SIGN_IDENTITY" \
NOTARY_PROFILE="$NOTARY_PROFILE" \
BUILD_PYTHON="$PYTHON" \
    ./packaging/build_macos_pkg.sh

version="$("$PYTHON" scripts/validate_release_version.py)"
arch="$(/usr/bin/uname -m)"
pkg="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir "$ROOT_DIR/dist" \
    --format path)"
update_archive="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir "$ROOT_DIR/dist" \
    --format updater-path)"
appcast="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir "$ROOT_DIR/dist" \
    --format appcast-path)"
channel_metadata="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir "$ROOT_DIR/dist" \
    --format channel-metadata-path)"
developer_artifact_output="$("$PYTHON" scripts/release_artifact_contract.py \
    --version "$version" \
    --architecture "$arch" \
    --dist-dir "$ROOT_DIR/dist" \
    --format developer-paths)"
developer_artifacts=()
while IFS= read -r artifact; do
    if [ -n "$artifact" ]; then
        developer_artifacts+=("$artifact")
    fi
done <<< "$developer_artifact_output"
if [ "${#developer_artifacts[@]}" -ne 2 ]; then
    echo "Release artifact contract did not return one wheel and one sdist." >&2
    exit 1
fi
"$PYTHON" scripts/python_release_artifacts.py \
    --root "$ROOT_DIR" \
    --staging-dir "$ROOT_DIR/build/macos-pkg/python-release" \
    --output-dir "$ROOT_DIR/dist" \
    --version "$version"
app="$ROOT_DIR/build/macos-pkg/pyinstaller/SidePulse.app"
environment_snapshot="$ROOT_DIR/dist/release-environment.txt"
raw_evidence_dir="$ROOT_DIR/build/macos-pkg/release-evidence-raw"
notary_response="$raw_evidence_dir/notary-submission.json"
notary_log="$raw_evidence_dir/notary-log.json"
notary_submitted_sha="$raw_evidence_dir/notary-submitted-pkg.sha256"
app_notary_response="$raw_evidence_dir/app-notary-submission.json"
app_notary_log="$raw_evidence_dir/app-notary-log.json"
app_notary_submitted_sha="$raw_evidence_dir/app-notary-submitted-zip.sha256"
sparkle_distribution="$ROOT_DIR/build/macos-pkg/sparkle-distribution"
if [ ! -f "$pkg" ] || [ ! -f "$update_archive" ] || [ ! -d "$app" ] || \
   [ ! -d "$sparkle_distribution" ] || [ ! -f "$environment_snapshot" ]; then
    echo "Signed release artifacts or environment snapshot are missing." >&2
    exit 1
fi
for required_notary_file in \
    "$notary_response" \
    "$notary_log" \
    "$notary_submitted_sha" \
    "$app_notary_response" \
    "$app_notary_log" \
    "$app_notary_submitted_sha"; do
    if [ ! -f "$required_notary_file" ]; then
        echo "Notarization evidence is missing: $required_notary_file" >&2
        exit 1
    fi
done

case "$EVIDENCE_DIR" in
    "$ROOT_DIR"/dist/release-evidence) ;;
    *) echo "Refusing unsafe release evidence directory: $EVIDENCE_DIR" >&2; exit 2 ;;
esac
/bin/rm -rf "$EVIDENCE_DIR"
/bin/mkdir -m 700 "$EVIDENCE_DIR"
/bin/cp "$PERFORMANCE_SOURCE" "$PERFORMANCE_EVIDENCE"
/bin/chmod 644 "$PERFORMANCE_EVIDENCE"

expected_team="$(/usr/bin/codesign -dv --verbose=4 "$app" 2>&1 \
    | /usr/bin/awk -F= '/^TeamIdentifier=/ {print $2}')"
if [ -z "$expected_team" ] || [ "$expected_team" = "not set" ]; then
    echo "Signed candidate has no TeamIdentifier." >&2
    exit 1
fi

candidate="$EVIDENCE_DIR/candidate.json"
"$PYTHON" scripts/release_evidence.py candidate \
    --root "$ROOT_DIR" \
    --output "$candidate" \
    --version "$version" \
    --architecture "$arch" \
    --commit "$head_commit" \
    --pkg "$pkg" \
    --app "$app" \
    --update-archive "$update_archive" \
    --bundle-identifier io.sidepulse.app \
    --team-identifier "$expected_team"

candidate_id="$("$PYTHON" -c \
    'import json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); print(value["candidate_id"])' \
    "$candidate")"
sparkle_channel_args=(
    --sparkle-distribution "$sparkle_distribution"
    --archive "$update_archive"
    --output-dir "$ROOT_DIR/dist"
    --candidate-id "$candidate_id"
    --keychain-account "$SPARKLE_KEY_ACCOUNT"
    --channel "$RELEASE_CHANNEL"
)
if [ -n "$SPARKLE_HISTORY_DIR" ]; then
    case "$SPARKLE_HISTORY_DIR" in
        /*) ;;
        *) echo "SIDEPULSE_SPARKLE_HISTORY_DIR must be an absolute path." >&2; exit 2 ;;
    esac
    if [ ! -d "$SPARKLE_HISTORY_DIR" ] || [ ! -f "$SPARKLE_HISTORY_DIR/appcast.xml" ]; then
        echo "Sparkle history must contain appcast.xml: $SPARKLE_HISTORY_DIR" >&2
        exit 2
    fi
    sparkle_channel_args+=(--previous-appcast "$SPARKLE_HISTORY_DIR/appcast.xml")
    previous_archive_count=0
    while IFS= read -r -d '' previous_archive; do
        sparkle_channel_args+=(--previous-archive "$previous_archive")
        previous_archive_count=$((previous_archive_count + 1))
    done < <(/usr/bin/find "$SPARKLE_HISTORY_DIR" -maxdepth 1 -type f -name 'SidePulse-*.zip' -print0)
    if [ "$previous_archive_count" -eq 0 ]; then
        echo "Sparkle history contains no retained SidePulse update archive." >&2
        exit 2
    fi
fi
"$PYTHON" scripts/generate_sparkle_channel.py \
    "${sparkle_channel_args[@]}"
if [ ! -f "$appcast" ] || [ ! -f "$channel_metadata" ]; then
    echo "Signed Sparkle appcast or candidate-bound channel metadata is missing." >&2
    exit 1
fi

receipt_files=()
record_receipt() {
    local kind="$1"
    local input="$2"
    local output="$EVIDENCE_DIR/$kind.json"
    shift 2
    "$PYTHON" scripts/release_evidence.py run-receipt \
        --root "$ROOT_DIR" \
        --candidate "$candidate" \
        --kind "$kind" \
        --input "$input" \
        --output "$output" \
        -- "$@"
    receipt_files+=("$output")
}

record_receipt source-gate "$pkg" ./scripts/verify.sh --no-bootstrap --skip-build --skip-clean-install
record_receipt performance "$PERFORMANCE_EVIDENCE" \
    "$PYTHON" scripts/verify_performance_budget.py "$PERFORMANCE_EVIDENCE"
record_receipt pkg-signature "$pkg" \
    /usr/sbin/pkgutil --check-signature "$pkg"
record_receipt pkg-gatekeeper "$pkg" \
    /usr/sbin/spctl -a -vv -t install "$pkg"
stapling_receipt="$EVIDENCE_DIR/stapling.json"
"$PYTHON" scripts/release_evidence.py stapling-receipt \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --pkg "$pkg" \
    --submitted-sha256 "$notary_submitted_sha" \
    --output "$stapling_receipt"
receipt_files+=("$stapling_receipt")
record_receipt app-signature "$app" \
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$app"
record_receipt app-gatekeeper "$app" \
    /usr/sbin/spctl -a -vv "$app"
record_receipt bundle-closure "$app" \
    "$PYTHON" packaging/verify_macos_app.py "$app"
record_receipt entitlements "$app" \
    "$PYTHON" packaging/verify_entitlements.py "$app"
record_receipt sparkle-nested-signing "$app" \
    "$PYTHON" packaging/verify_sparkle_bundle.py \
        "$app" \
        --production \
        --expected-team "$expected_team"
app_notarization_receipt="$EVIDENCE_DIR/app-notarization.json"
"$PYTHON" scripts/release_evidence.py app-notarization-receipt \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --app "$app" \
    --response "$app_notary_response" \
    --log "$app_notary_log" \
    --submitted-sha256 "$app_notary_submitted_sha" \
    --output "$app_notarization_receipt"
receipt_files+=("$app_notarization_receipt")
app_stapling_receipt="$EVIDENCE_DIR/app-stapling.json"
"$PYTHON" scripts/release_evidence.py app-stapling-receipt \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --app "$app" \
    --response "$app_notary_response" \
    --output "$app_stapling_receipt"
receipt_files+=("$app_stapling_receipt")
record_receipt update-archive "$update_archive" \
    "$PYTHON" -c \
    'import sys; from pathlib import Path; from scripts.package_sparkle_archive import validate_archive; validate_archive(archive=Path(sys.argv[1]), app=Path(sys.argv[2]))' \
    "$update_archive" "$app"
record_receipt signed-appcast "$appcast" \
    "$PYTHON" -c \
    'import sys; from pathlib import Path; from scripts.generate_sparkle_channel import validate_channel_outputs; validate_channel_outputs(archive=Path(sys.argv[1]), appcast=Path(sys.argv[2]), metadata=Path(sys.argv[3]), candidate_id=sys.argv[4], sparkle_distribution=Path(sys.argv[5]), keychain_account=sys.argv[6])' \
    "$update_archive" "$appcast" "$channel_metadata" "$candidate_id" \
    "$sparkle_distribution" "$SPARKLE_KEY_ACCOUNT"
if [ "$REQUIRED_HARDWARE" != "software" ]; then
    record_receipt hardware-smoke "$pkg" \
        "$PYTHON" scripts/verify_hardware_release.py \
            --confirm-write \
            --require "$REQUIRED_HARDWARE"
fi

package_contents_receipt="$EVIDENCE_DIR/package-contents.json"
"$PYTHON" scripts/release_evidence.py package-contents-receipt \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --pkg "$pkg" \
    --output "$package_contents_receipt"
receipt_files+=("$package_contents_receipt")

notarization_receipt="$EVIDENCE_DIR/notarization.json"
"$PYTHON" scripts/release_evidence.py notarization-receipt \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --pkg "$pkg" \
    --response "$notary_response" \
    --log "$notary_log" \
    --submitted-sha256 "$notary_submitted_sha" \
    --output "$notarization_receipt"
receipt_files+=("$notarization_receipt")

if [ ! -f "$SETTINGS_PATH" ]; then
    echo "Installed-upgrade verification requires settings: $SETTINGS_PATH" >&2
    exit 2
fi
installed_app="/Applications/SidePulse.app"
upgrade_baseline="$EVIDENCE_DIR/pre-upgrade-baseline.json"
"$PYTHON" scripts/capture_installed_release_baseline.py \
    --app "$installed_app" \
    --settings "$SETTINGS_PATH" \
    --output "$upgrade_baseline"
before_settings="$(/usr/bin/mktemp -t sidepulse-settings-before.XXXXXX.json)"
before_uninstall_settings=""
cleanup() {
    /bin/rm -f "$before_settings"
    if [ -n "$before_uninstall_settings" ]; then
        /bin/rm -f "$before_uninstall_settings"
    fi
}
trap cleanup EXIT
/bin/cp "$SETTINGS_PATH" "$before_settings"

/usr/bin/sudo /usr/sbin/installer -pkg "$pkg" -target /
installed_binary="/Applications/SidePulse.app/Contents/MacOS/SidePulse"
if [ ! -x "$installed_binary" ]; then
    echo "Installed JR-Bar executable is missing at the compatibility path." >&2
    exit 1
fi
# The package remains payload-only. The upgrade smoke explicitly starts the
# previously configured, user-owned status-bar path.
"$installed_binary" status-bar start
"$PYTHON" scripts/verify_installed_upgrade.py \
    --before-settings "$before_settings" \
    --settings "$SETTINGS_PATH" \
    --expected-team "$expected_team" \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --pkg "$pkg" \
    --receipt-dir "$EVIDENCE_DIR" \
    --baseline "$upgrade_baseline"
receipt_files+=(
    "$EVIDENCE_DIR/installed-upgrade.json"
    "$EVIDENCE_DIR/settings-preservation.json"
)

before_uninstall_settings="$(/usr/bin/mktemp -t sidepulse-settings-before-uninstall.XXXXXX.json)"
/bin/cp "$SETTINGS_PATH" "$before_uninstall_settings"

uninstall_log="$EVIDENCE_DIR/uninstall.log"
if ! /usr/bin/sudo "$ROOT_DIR/scripts/uninstall-macos.sh" \
    --user "$RELEASE_USER" > "$uninstall_log" 2>&1; then
    /bin/cat "$uninstall_log" >&2
    exit 1
fi
/bin/cat "$uninstall_log"
uninstall_receipt="$EVIDENCE_DIR/uninstall.json"
"$PYTHON" scripts/verify_uninstalled_candidate.py \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --pkg "$pkg" \
    --before-settings "$before_uninstall_settings" \
    --settings "$SETTINGS_PATH" \
    --user "$RELEASE_USER" \
    --output "$uninstall_receipt"
receipt_files+=("$uninstall_receipt")

/usr/bin/sudo /usr/sbin/installer -pkg "$pkg" -target /
clean_install_receipt="$EVIDENCE_DIR/clean-install.json"
"$PYTHON" scripts/verify_clean_pkg_install.py \
    --root "$ROOT_DIR" \
    --candidate "$candidate" \
    --pkg "$pkg" \
    --output "$clean_install_receipt"
receipt_files+=("$clean_install_receipt")

artifacts=(
    "${developer_artifacts[@]}"
    "$environment_snapshot"
    "$PERFORMANCE_EVIDENCE"
    "$pkg"
    "$update_archive"
    "$appcast"
    "$channel_metadata"
)
sbom="$ROOT_DIR/dist/sidepulse-sbom.cdx.json"
sbom_args=(
    --output "$sbom"
    --root "$ROOT_DIR"
    --application-version "$version"
)
for artifact in "${artifacts[@]}"; do
    sbom_args+=(--artifact "$artifact")
done
SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
    "$PYTHON" scripts/generate_sbom.py "${sbom_args[@]}"
record_receipt sbom "$sbom" \
    "$PYTHON" -c \
    'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); assert d.get("bomFormat") == "CycloneDX"' \
    "$sbom"
artifacts+=("$sbom")

manifest_args=(
    --root "$ROOT_DIR"
    --output "$ROOT_DIR/dist/release-verification.json"
    --candidate "$candidate"
    --performance-evidence "$PERFORMANCE_EVIDENCE"
    --sbom "$sbom"
    --hardware-profile "$REQUIRED_HARDWARE"
)
for artifact in "${artifacts[@]}"; do
    manifest_args+=(--artifact "$artifact")
done
for receipt in "${receipt_files[@]}"; do
    manifest_args+=(--receipt "$receipt")
done
"$PYTHON" scripts/generate_release_manifest.py "${manifest_args[@]}"

printf '%s\n' "Authoritative JR-Bar macOS release gate passed."
