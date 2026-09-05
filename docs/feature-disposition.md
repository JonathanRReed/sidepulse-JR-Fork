# Feature disposition

This is the review list for unfinished, dormant, duplicate, or poorly wired
features found during the September 4, 2026 inventory. Scores measure value to
JR-Bar from 0 (remove) to 5 (worth shipping). "Keep" means the current design
is sound. "Integrate" means the code is useful but needs a visible product
entry point. "Defer" means it belongs after the macOS release. "Retire"
means it has no supported caller or user value.

| Feature | Score | Disposition | Evidence and reason |
| --- | ---: | --- | --- |
| T3 Code query-only usage compatibility | 5 | Keep | `src/sidepulse/usage_graph_worker.py` scans T3's local SQLite without mutation or credentials. |
| Alcove following and confidence ladder | 5 | Keep | `docs/FEATURE-MATRIX.md` marks it implemented but release-gated; the audit records renderer regressions that still need installed-app verification. |
| Provider-exhaustion session repair | 5 | Ship | `quota_power_hold.py` releases only the keep-awake claim after repeated authoritative zero-quota evidence and a quiet window. It never claims the agent stopped. |
| Statuspage incident rows | 4 | Ship | `status_feeds.py` keeps bounded provider-status observations separate from local collection failures and projects active incidents into the menu. |
| `sidepulse serve` control/observability API | 4 | Keep | The loopback schema-v2 API remains a privacy-redacted surface for Stream Deck, Waybar, and scripts. It is useful even without first-party orchestration. |
| Creator Micro 2 / Agent Deck controls | 5 | Implemented in source, release-gated | Saved app-switching and shortcut mappings, native controls, single-owner input/output, shared colours/dimming, and revocable delivery are wired. Device-layer onboarding, a rebuilt installed app, and physical input/output verification remain. Optional read-only snapshots are separate. |
| Screen Bar auto-hide diagnostics | 4 | Integrate | The audit says auto-hidden menu bars can lose the Screen Bar with no diagnostic. |
| Studio and Lid Animations split | 4 | Ship | Settings navigation now places lighting, devices, effects, and reset delivery in explicit categories while preserving Effect Studio as the program editor. |
| Claude consent and duplicate fetch paths | 5 | Ship | Background reconnect uses `allow_prompt=False`; interactive consent is explicit and the canonical usage plane owns quota collection. |
| Devin reconnect token safety | 5 | Ship | Reconnect no longer deletes a valid token merely because an organization header is absent. |
| API-equivalent cost coverage | 3 | Defer | The audit records missing model prices and skipped Codex cost. Keep estimates separate from authoritative quotas until pricing policy is explicit. |
| Settings controls that never resync | 5 | Ship | Category refresh, destination refresh, and focused control tests cover state changes made outside the active pane. |
| Account aliases and screenshot privacy | 5 | Ship | Normal UI uses user aliases or safe short labels. Privacy mode removes account names and email addresses without changing collection. |
| Local usage heatmap | 4 | Ship | The Usage pane renders immutable local token and session history by provider. It remains separate from authoritative quota limits. |
| Reset delivery ledger | 5 | Ship | Each reset event records channel outcomes, retries eligible suppressed channels for five minutes, and discards expired delivery work. |
| Dead settings dials | 0 | Retire | `closed_lid_system_override_enabled`, `local_activity_history_enabled`, and `forecast_release_authority` have no supported runtime value. |
| `signals.quota_resets` duplicate symbol | 0 | Retire | The audit found zero callers. Current reset delivery uses the provider reset event path instead. |
| `interruption_policy.plan_deliveries` | 0 | Retire | The audit found no caller and the delivery ledger was removed. |
| `LID_ANIMATION_CHOICES` | 0 | Retire | The audit lists this as deleted/dead; no source symbol remains. |
| Mailbox v1 migration | 1 | Keep | `mailbox_preference_store.py` still decodes strict v1 documents and `mailbox_preferences.py` applies the legacy adapter. Removing it would break existing settings. |
| `agent_browser.handle_key_command` | 2 | Keep | `agent_browser_window.py:290-301,597` calls it from the live key event path. The older audit entry is stale. |
| Duplicate field-diagnostics output | 0 | Defer | The audit identifies a doubled `0`, but the current script has no obvious duplicate line. Revisit with a captured field report rather than guessing. |
| Linux native runtime | 3 | Defer | Only remote-peer Linux support exists. Native dependencies are macOS-gated in `pyproject.toml`; extract the headless core first. |
| Windows native runtime | 1 | Defer | No Windows UI, device, or packaging path exists. Revisit after macOS and Linux headless contracts. |

The 0-score items were rechecked before this document was written. The
duplicate symbols are already absent, the mailbox migration is still needed
for compatibility, and the current browser key handler is live. No additional
source deletion is safe in this pass. The diagnostics note needs a captured
field report before anyone edits the redaction script.
