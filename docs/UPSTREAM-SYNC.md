# Upstream Sync Review

Reviewed on August 15, 2026 against the ten upstream commits added after merge base `f4dd7e0`. They are not merged wholesale because the JR fork is 160 commits ahead and its controller, installer, packaging, and session-navigation design have intentionally diverged.

| Upstream work | JR-fork disposition |
| --- | --- |
| Custom terminal selection and terminal-window reuse | Already superseded by `status_bar_launch.py`, `navigation_policy.py`, and `session_actions.py`, including Terminal, iTerm2, and verified direct Ghostty execution. |
| Missing ScriptingBridge dependency | Not applicable. This fork has no ScriptingBridge import. Adding the framework would add install weight without repairing reachable behavior. |
| Isolated user installer | Ported as `scripts/install-user.sh` for CLI users while preserving the fork's sealed app-bundle, signed-package, and launch-agent paths. |
| Packaging and clean-install tests | Adopted through package-contract tests and `scripts/verify_clean_install.py`. |
| Hook stability and compatibility | Adopted through fail-open current and legacy hook-module entrypoints. |
| Version and post-build guards | Adopted through `scripts/validate_release_version.py`, Twine validation, clean-wheel installation, and checksum generation. |
| Status-bar UI patches | Not copied by file name. Upstream's controller is substantially different; any behavior must be ported with a fork-native regression test. |

Future upstream syncs should be reviewed commit by commit. Prefer porting a behavior and its test over merging a large controller diff.
