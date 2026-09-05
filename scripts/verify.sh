#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${SIDEPULSE_DEV_VENV:-${VENV_DIR:-$ROOT_DIR/.venv}}"
PYTHON="${PYTHON:-$VENV_DIR/bin/python}"
BOOTSTRAP=1
FIX=0
PORTABLE=0
SKIP_BUILD=0
SKIP_CLEAN_INSTALL=0

usage() {
    cat <<'EOF'
Usage: scripts/verify.sh [options]

  --fix                 Apply Ruff's safe fixes before checking.
  --portable            Run only platform-neutral tests.
  --no-bootstrap        Use an existing development environment.
  --skip-build          Skip wheel/sdist build and Twine validation.
  --skip-clean-install  Skip installation of the built wheel into a fresh venv.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --fix) FIX=1 ;;
        --portable) PORTABLE=1 ;;
        --no-bootstrap) BOOTSTRAP=0 ;;
        --skip-build) SKIP_BUILD=1 ;;
        --skip-clean-install) SKIP_CLEAN_INSTALL=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$BOOTSTRAP" -eq 1 ]; then
    "$ROOT_DIR/scripts/bootstrap-dev.sh"
fi
if [ ! -x "$PYTHON" ]; then
    echo "Missing development environment. Run ./scripts/bootstrap-dev.sh first." >&2
    exit 2
fi

cd "$ROOT_DIR"
if [ "$FIX" -eq 1 ]; then
    "$PYTHON" -m ruff check --fix src tests packaging scripts
fi

"$PYTHON" -m pip check
"$PYTHON" scripts/verify_dependency_policy.py --root "$ROOT_DIR"
"$PYTHON" scripts/scan_secrets.py --root "$ROOT_DIR"
"$PYTHON" -m ruff check src tests packaging scripts
"$PYTHON" -m compileall -q src tests packaging scripts
"$PYTHON" scripts/validate_release_version.py

if [ "$PORTABLE" -eq 1 ]; then
    "$PYTHON" -m pytest \
        tests/test_device_projection.py \
        tests/test_packaging_contract.py \
        tests/test_status_bar_facade_contract.py \
        tests/test_status_bar_production_boundary.py \
        tests/test_external_integration_wiring.py \
        tests/test_architecture_ratchets.py \
        tests/test_unwired_modules_ratchet.py \
        tests/test_core_state.py \
        tests/test_core_state_determinism.py \
        tests/test_refresh_admission.py \
        tests/test_background_services.py \
        tests/test_version_contract.py \
        tests/test_module_entrypoint.py \
        tests/test_legacy_hook_entrypoints.py \
        tests/test_build_script_contract.py \
        tests/test_repository_hygiene.py \
        tests/test_workflow_contract.py \
        tests/test_install_user.py \
        tests/test_settings_schema_coverage.py \
        tests/test_settings_compatibility.py \
        tests/test_settings_concurrency.py \
        tests/test_integration_settings.py \
        tests/test_integration_compatibility_manifest.py \
        tests/test_integration_cli.py \
        tests/test_integration_cli_entrypoint.py \
        tests/test_collector_external_statuses.py \
        tests/test_t3_compat.py \
        tests/test_battery_runtime.py \
        tests/test_transcript_runtime.py \
        tests/test_transcript_coalescing.py \
        tests/test_performance_metrics.py \
        tests/test_presentation_safety_compiler.py \
        tests/test_firmware_write_boundary.py \
        tests/test_firmware_validation_cache.py \
        tests/test_release_gate_contract.py \
        tests/test_installer_safety_contract.py \
        tests/test_launch_agent_safety.py \
        tests/test_webhook_delivery.py \
        tests/test_webhook_queue.py \
        tests/test_weather_network_bounds.py \
        tests/test_supply_chain_tools.py \
        tests/test_dependency_and_entitlements.py \
        tests/test_inside_out_signing.py \
        -q
elif [ "$(uname -s)" = "Darwin" ]; then
    "$PYTHON" -m pytest tests -q
else
    echo "Full JR-Bar verification requires macOS and PyObjC." >&2
    echo "Use --portable for the platform-neutral production gate." >&2
    exit 3
fi

if [ "$SKIP_BUILD" -eq 0 ]; then
    rm -rf build dist
    "$PYTHON" -m build --no-isolation
    "$PYTHON" -m twine check dist/*.whl dist/*.tar.gz
    if [ "$SKIP_CLEAN_INSTALL" -eq 0 ]; then
        "$PYTHON" scripts/verify_clean_install.py
    fi
    LC_ALL=C "$PYTHON" -m pip list --format=freeze \
        | /usr/bin/sort > dist/release-environment.txt
    version="$("$PYTHON" scripts/validate_release_version.py)"
    sbom_args=(
        --output dist/sidepulse-sbom.cdx.json
        --application-version "$version"
        --artifact dist/release-environment.txt
    )
    for artifact in dist/*.whl dist/*.tar.gz; do
        sbom_args+=(--artifact "$artifact")
    done
    SOURCE_DATE_EPOCH="$(git show -s --format=%ct HEAD)" \
        "$PYTHON" scripts/generate_sbom.py "${sbom_args[@]}"
fi

git diff --check
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    tracked_work="$(git ls-files work)"
    if [ -n "$tracked_work" ]; then
        echo "Generated work files remain tracked:" >&2
        printf '%s\n' "$tracked_work" >&2
        exit 1
    fi

    while IFS= read -r -d '' generated; do
        if [ -e "$generated" ]; then
            echo "Generated installer remains tracked: $generated" >&2
            exit 1
        fi
    done < <(git ls-files -z '*.pkg' '*.dmg')
fi

if [ "$(uname -s)" = "Darwin" ] && \
   [ "${SIDEPULSE_VERIFY_MACOS_PACKAGE:-0}" = "1" ]; then
    BUILD_PYTHON="$PYTHON" "$ROOT_DIR/packaging/build_macos_pkg.sh"
fi

printf '%s\n' "JR-Bar verification passed."
