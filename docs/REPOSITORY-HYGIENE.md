# Repository Hygiene

SidePulse source control contains source, tests, durable documentation, and build definitions. It does not contain local build trees, virtual environments, logs, install receipts, generated packages, or agent scratch contracts.

## Local output

All local scratch work belongs under `work/`, which is ignored as a unit. Move durable research or decisions to `docs/` before committing them. Build products belong under `dist/` and are also ignored.

The canonical local verification command is:

```sh
make verify
```

That creates `.venv`, installs the `test` extra, runs Ruff, executes the complete macOS test suite, builds the wheel and source distribution, and validates both with Twine. The complete suite requires macOS because the application uses PyObjC/AppKit. A platform-neutral rescue check is available through:

```sh
./scripts/verify.sh --targeted --no-build
```

## Release artifacts

Installers and Python distributions are published as GitHub Release assets. Do not commit `.pkg`, `.dmg`, wheel, or source-distribution files to the repository.

## Historical size cleanup

Removing a tracked artifact from the current tree does not remove its historical blobs. After the rescue branch is merged and backed up with a tag, repository history can be compacted separately:

```sh
git filter-repo \
  --path work/build-live \
  --path work/dist-live \
  --path-glob 'work/rebuild-*' \
  --invert-paths
```

History rewriting changes commit IDs and requires a coordinated force-push. It is deliberately separate from functional cleanup.
