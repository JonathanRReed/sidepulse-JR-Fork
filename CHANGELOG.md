# Changelog

## 0.2.2

- Repair provider-pinned device projection and remove the undefined `pin` crash path.
- Filter provider-local workers before choosing a worker-only representative, so another provider cannot make a pinned device appear idle.
- Preserve the canonical main-agent and worker split to prevent duplicate worker rows.
- Place the historical AppKit controller behind a small compatibility facade and require new behavior to live in linted, testable modules.
- Restore direct `python -m sidepulse.status_bar` execution.
- Keep legacy `agent_monitor.hook_entry` and `sidepulse_cli.hook_entry` commands installable and fail-open.
- Make local macOS verification authoritative while hosted CI minutes are unavailable.
- Add package, version, clean-install, repository-hygiene, and release checks.
- Remove generated installers, logs, receipts, and rebuild output from the tracked tree.
- Point project metadata at the JR fork while preserving an explicit upstream link.
