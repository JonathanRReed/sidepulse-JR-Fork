# Local Verification

GitHub Actions are manual-only. A clean macOS checkout is the authoritative gate because the application depends on AppKit, PyObjC, signing identities, macOS permissions, and optional physical SidePulse hardware.

## Development setup

```sh
./scripts/bootstrap-dev.sh
```

The script selects Python 3.10 or newer, creates `.venv`, and installs the fork with pinned development tools. It does not modify the system Python environment.

## Complete macOS verification

```sh
./scripts/verify.sh --fix
```

The gate runs:

1. Ruff over source, tests, packaging, and scripts.
2. Bytecode compilation and source/package/changelog version validation.
3. The complete pytest suite on macOS.
4. Wheel and source-distribution builds plus Twine metadata checks.
5. A clean-wheel installation in a temporary virtual environment.
6. Console-script, compatibility-module, resource, and repository-hygiene checks.

Every command preserves its exit status. No test output is piped through another command.

## Portable verification

A non-macOS machine cannot certify AppKit, PyObjC, TCC, signing, or hardware behavior. It can run the deterministic rescue gate:

```sh
./scripts/verify.sh --portable
```

## GitHub-hosted macOS runners are informational only

A hosted `macos-latest` runner has no logged-in window session, no
Screen Recording grant, no `bun`, a python.org framework interpreter
the installer-safety check rightly refuses, and shared-tenant timing.
A full-suite run there fails a known set of environment-coupled tests
(observed 2026-08-19: alcove honesty, installer transaction, screen-bar
motion, agent-browser p95, OpenCode-under-bun, Codex trust refresh)
while the same commit passes 100% on a real Mac. Treat hosted macOS
results as informational; the authoritative full gate is a clean local
checkout or the self-hosted workflow
(`.github/workflows/self-hosted-macos.yml`).

## Signed package verification

```sh
APP_SIGN_IDENTITY='Developer ID Application: …' \
INSTALLER_SIGN_IDENTITY='Developer ID Installer: …' \
NOTARY_PROFILE='sidepulse-notary' \
SIDEPULSE_VERIFY_MACOS_PACKAGE=1 \
./scripts/verify.sh --no-bootstrap
```

## Release

After the rescue branch is merged into `main`:

```sh
./scripts/publish_release.sh
```

The release script refuses dirty or non-main trees, verifies version `0.2.2`, builds and checks Python distributions, builds the signed/notarized macOS package, creates SHA-256 checksums, tags the commit, and publishes the files to a GitHub Release. It does not publish the upstream-owned `sidepulse` project name to PyPI.
