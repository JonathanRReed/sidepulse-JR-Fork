# SidePulse Rescue Report

Prepared from `a07895c34ad22809a2260da752c69d6bfb9036fa` on August 15, 2026.

## Confirmed defects repaired

- Provider-pinned device projection referenced an undefined local variable.
- Provider-only background workers could disappear because the global worker representative was selected before the provider pin was applied.
- Reconstructing a projection with a worker in `visible_rows` could duplicate that worker when the canonical invariant demoted it again.
- A constant split the import section in the AppKit controller, producing a large Ruff cascade and preventing pytest from running in the hosted workflow.
- Direct `python -m sidepulse.status_bar` execution was lost when the controller was placed behind a compatibility facade.
- Historical `agent_monitor` and `sidepulse_cli` hook modules were present or referenced but excluded from built packages.
- The packaging script defaulted to Apple's Python 3.9 even though the package requires Python 3.10 or newer.
- Project metadata and release automation still targeted upstream/PyPI despite this being a deliberately divergent personal fork.
- Generated packages, logs, receipts, rebuild output, and scratch contracts remained tracked.

## Structural changes

- Pure provider/device projection lives in `sidepulse.device_projection` with focused regression tests.
- The 18,000-line AppKit controller is retained as `status_bar_legacy.py`; `status_bar.py` is a small compatibility boundary. New behavior must be extracted rather than added to the monolith.
- Local bootstrap, verification, clean-install, isolated-install, and release scripts define the supported developer path.
- GitHub workflows are manual-only while hosted credits are unavailable.
- Releases are generated from the owner's signed Mac and uploaded to GitHub Releases with checksums. The fork does not publish the upstream-owned package name to PyPI.
- Generated `work/` output is ignored and absent from the current source tree.

## Verification status

Completed in the available Linux environment:

- provider projection regression suite: 6 passed;
- facade contract test: passed;
- new Python files: bytecode compilation passed;
- new shell scripts: syntax checks passed.

The complete AppKit/PyObjC suite, signed package build, notarization, TCC behavior, and physical hardware checks require the owner's Mac. Run `./scripts/verify.sh --fix` there before merging or releasing.
