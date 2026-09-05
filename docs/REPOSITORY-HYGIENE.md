# Repository Hygiene

JR-Bar source control contains source, tests, durable documentation, and build definitions. It does not contain local build trees, virtual environments, logs, install receipts, generated packages, or agent scratch contracts.

## Local output

All local scratch work belongs under `work/`, which is ignored as a unit. Move durable research or decisions to `docs/` before committing them. Build products belong under `dist/` and are also ignored.

The canonical local verification command is:

```sh
make verify
```

That creates `.venv`, installs the `test` extra, runs Ruff, executes the complete macOS test suite, builds the wheel and source distribution, and validates both with Twine. The complete suite requires macOS because the application uses PyObjC/AppKit. A fast ordinary-change gate is available after bootstrap through:

```sh
make fast
```

It runs Ruff, real package imports, lightweight architecture and repository
contracts, the tracked-file secret scan, literal provider and schema fixtures,
430 selected contract, fixture, and semantic tests, compilation, dependency
and version policy, and
diff hygiene. It does not bootstrap or install tools, build or clean artifacts,
run the complete suite, touch hardware, install the app, sign or notarize a
candidate, launch Instruments, or publish anything. The platform-neutral
rescue gate remains `make verify-portable`.

## Release artifacts

The authoritative signed PKG, performance evidence, SBOM, release-verification
manifest, environment snapshot, checksums, and developer-facing Python
distributions are future GitHub Release assets when publication is explicitly
authorized. Do not commit `.pkg`, `.dmg`, `.zip`, wheel,
source-distribution, or generated evidence files to the repository.

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
