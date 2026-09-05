# Native provider usage

JR-Bar owns provider accounting directly. CodexBar is an engineering reference only; it is never launched, queried, imported, or required at runtime. The only external application integrations are T3 Code and Alcove.

## Supported providers

| Provider | Native sources | Main facts |
| --- | --- | --- |
| ChatGPT / Codex | Codex OAuth usage API, `codex app-server`, local Codex records | five-hour and weekly limits, dynamic additional lanes such as Spark, credits, resets, tokens, models, and estimates |
| Claude | Claude OAuth usage API, consented Claude browser session, local Claude records | five-hour, weekly, arbitrary model- or feature-scoped limits such as Fable, credits, extra usage, tokens, cache savings, and estimates |
| Cursor | Cursor.app read-only SQLite auth, consented browser session | included plan, Auto/Composer, API/model usage, extra usage, resets, account identity |
| Devin | encrypted JR-Bar manual bearer or consented Chromium localStorage | daily and weekly quota, reset times, organization identity |
| Grok | `~/.grok/auth.json`, Grok billing API, local signals | subscription credit usage, cycle reset, account/plan, local token activity |
| Antigravity | running Antigravity or `agy` loopback quota server | Gemini session/weekly and Claude+GPT session/weekly pools, dynamic detail lanes |
| OpenAI API | encrypted JR-Bar Admin API key | organization/project spend, tokens, requests, models, daily history |

Unknown provider-owned quota lanes remain visible in detail views but cannot trigger hardware or interruption alerts until their effect is declared. Missing data, measured zero, stale data, last-known-good data, permission failures, and unsupported sources are separate states.

## Basic setup

```bash
sidepulse providers status
sidepulse providers enable codex
sidepulse providers enable claude
sidepulse providers enable cursor
sidepulse providers enable devin
sidepulse providers enable grok
sidepulse providers enable antigravity
sidepulse providers enable openai-api
```

Configure source and display policy:

```bash
sidepulse providers configure claude --source-mode auto
sidepulse providers configure claude --dynamic-lanes on
sidepulse providers configure claude --reset-celebrations on
sidepulse providers configure claude --threshold-remaining 20
sidepulse providers configure devin --option organization=org_example
sidepulse providers configure openai-api --option project_id=proj_example
```

Collect one bounded user-initiated snapshot:

```bash
sidepulse providers refresh
sidepulse providers refresh --json
```

## Manual credentials

Secrets are read from standard input and stored in JR-Bar's encrypted owner-private credential store. They never appear in configuration files, process arguments, diagnostics, exports, or command output.

```bash
printf '%s' "$DEVIN_BEARER_TOKEN" | \
  sidepulse providers credential set devin --stdin \
  --option organization=org_example

printf '%s' "$OPENAI_ADMIN_KEY" | \
  sidepulse providers credential set openai-api --stdin \
  --option project_id=proj_example

sidepulse providers credential list
sidepulse providers credential remove devin
```

## Browser-backed sources

Browser sources are disabled by default. Consent binds one provider, browser, profile, approved domains, approved field names, and optional background repair policy. Granting consent does not import anything. Import is a separate explicit action.

```bash
sidepulse providers configure cursor --browser-sources on
sidepulse providers browser-consent grant cursor \
  --browser chrome --profile Default --background-repair
sidepulse providers browser-consent import cursor \
  --browser chrome --profile Default \
  --profile-root "$HOME/Library/Application Support/Google/Chrome/Default"

sidepulse providers browser-consent list
sidepulse providers browser-consent revoke cursor \
  --browser chrome --profile Default
```

The packaged reader supports Chromium-family cookie/localStorage databases and Firefox cookies. It copies stores to an isolated temporary directory before reading, never mutates browser data, restricts reads to the provider's allowlist, and stores validated imported values encrypted. Safari remains unavailable until a signed-bundle WebKit import path passes the same consent and account-isolation tests.

## Usage Center and quality-of-life behavior

The native Usage Center shows every account and quota lane, reset countdowns, model count, tokens, credits, incidents, source freshness, partial pricing coverage, local estimates, and cross-Mac totals. The menu shows the most constrained trustworthy lane without changing status-item width.

A reset celebration occurs only after a real lane transitions across its recorded reset boundary and a fresh observation confirms replenishment. It is a finite, accessibility-safe cue and is deduplicated across restarts. Threshold and incident notifications are upward-only and use the same authoritative facts shown in the Usage Center.

Pricing is an explicitly versioned local estimate. Unknown models remain visible as unpriced usage and reduce pricing coverage rather than inheriting another model's price.

## Cross-Mac sync

JR-Bar sync is local-first and peer-to-peer over SSH/SFTP, normally addressed through Tailscale. Envelopes are JSON signed with HMAC-SHA256 using the per-peer pairing secret — they are authenticated, not encrypted; confidentiality in transit comes from the SSH/SFTP channel. Packets stamped older than a bounded freshness window (7 days) are rejected on decode to blunt replays. Account-wide quota snapshots use the freshest valid observation and are never summed. Machine-local token events are deduplicated by device and provider, latest observation wins.

On the first Mac:

```bash
sidepulse providers sync set-device mac-mini
sidepulse providers sync set-categories quota,token_usage,agent_activity
sidepulse providers sync export-pairing --output ~/Desktop/sidepulse-pairing.json
```

Transfer that owner-private file directly to the second Mac. On the second Mac:

```bash
sidepulse providers sync set-device macbook
sidepulse providers sync import-pairing \
  --input ~/Desktop/sidepulse-pairing.json \
  --host mac-mini.tailnet-name.ts.net \
  --remote-path ~/.local/state/sidepulse/provider-sync/local.json \
  --known-hosts ~/.ssh/known_hosts \
  --identity-file ~/.ssh/id_ed25519
sidepulse providers sync enable
```

Repeat the pairing import in the opposite direction so both Macs can fetch each other's signed packet. Delete pairing files after import. Agent activity sync is a separate metadata-only category and remains off by default.
