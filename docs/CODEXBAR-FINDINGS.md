# CodexBar findings — usage tracking done right

Source: https://github.com/steipete/CodexBar @ `26ebaf9` (2026-08-11).
Full citations verified by the research pass; the ones we acted on
tonight are marked SHIPPED.

1. **Codex rollouts carry their own rate limits** — every token_count
   event embeds `rate_limits.primary` (used_percent, window_minutes,
   resets_at). One file read, no API, no auth. SHIPPED:
   `usage_stats.codex_rate_limits`.
2. **Codex totals are cumulative per session** — the LAST
   `total_token_usage` in a rollout is the exact session total; no
   dedupe needed. SHIPPED: `_parse_codex_file`.
3. **Official quota APIs exist for deeper data**: Codex
   `chatgpt.com/backend-api/wham/usage` with the `~/.codex/auth.json`
   bearer; Claude `api.anthropic.com/api/oauth/usage` (5h/weekly/
   per-model utilization) with the Claude Code OAuth token +
   `anthropic-beta: oauth-2025-04-20`; Gemini via
   `cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota` using
   `~/.gemini/oauth_creds.json`. Next tier when we want live quota
   gauges without estimation.
4. **Universal RateWindow struct** (usedPercent, windowMinutes,
   resetsAt, placeholder flag) as the lingua franca between providers
   and surfaces — adopt when we add more quota sources.
5. **Local-day bucketing from each timestamp's own TZ offset** —
   SHIPPED in `daily_buckets`.
6. **Adaptive refresh policy** (pure function): menu opened recently →
   2min, agents active → 5min, idle → 15-30min, Low Power → 30min.
   Worth adopting for our usage rescan cadence.
7. **Pace deltas** ("+11% ahead of even burn", "runs out Thursday") —
   more actionable than raw percent; candidate for the dropdown.
8. **Provider tier we're missing**: GitHub Copilot, Amp, Factory
   Droid, Antigravity, Windsurf; Grok has `~/.grok/sessions/**/
   signals.json` that fits our file-watcher model directly.
9. **Versioned cache filenames** (`codex-v11.json`) so schema changes
   cold-rebuild cleanly — SHIPPED as CACHE_VERSION bump semantics.
