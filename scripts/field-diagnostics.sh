#!/bin/sh
# JR-Bar field diagnostics -- pure shell, for the support case where
# Python itself is broken. Prints redacted environment facts only:
# never transcripts, prompts, tool payloads, or tokens.
set -u

section() { printf '\n== %s\n' "$1"; }
sanitize_output() { LC_ALL=C tr -c '\011\012\040-\176' '?'; }

echo "JR-Bar field diagnostics ($(date '+%Y-%m-%dT%H:%M:%S%z'))"

section "system"
sw_vers 2>/dev/null || echo "sw_vers unavailable"
uname -m

section "app + launchd"
for APP in "$HOME/Applications/SidePulse.app" "/Applications/SidePulse.app"; do
  if [ -d "$APP" ]; then
    case "$APP" in
      "$HOME"/*) APP_LABEL="user Applications/SidePulse.app" ;;
      *) APP_LABEL="system Applications/SidePulse.app" ;;
    esac
    echo "app: $APP_LABEL (modified $(stat -f %Sm "$APP/Contents/MacOS/SidePulse" 2>/dev/null || echo '?'))"
  fi
done
launchctl print "gui/$(id -u)/io.sidepulse.agentstatus" 2>/dev/null \
  | grep -E "state|pid|last exit" | head -4 \
  || echo "launchd service not loaded"

section "state files (sizes + ages only)"
STATE="$HOME/.local/state/sidepulse/agent-monitor"
for NAME in latest.json events.sock status-bar.out.log status-bar.err.log \
    claude.jsonl codex.jsonl grok.jsonl devin.jsonl hermes.jsonl; do
  TARGET="$STATE/$NAME"
  if [ -e "$TARGET" ]; then
    echo "$NAME: $(stat -f '%z bytes, modified %Sm' "$TARGET" 2>/dev/null)"
  else
    echo "$NAME: missing"
  fi
done

section "recent app log (non-content lines)"
LOG="$STATE/status-bar.out.log"
if [ -f "$LOG" ]; then
  tail -c 131072 "$LOG" \
    | tail -200 \
    | sanitize_output \
    | grep -E "error|timing|state=|device|closed_lid|keep_awake" \
    | sed -E "s|$HOME|[home]|g; s/([Ss]erial([_ ]?(number|id))?[=: ]+)[^ ,;]+/\\1[redacted]/g" \
    | cut -c 1-512 \
    | tail -12
else
  echo "no app log"
fi

section "hooks configured"
for CONFIG in "$HOME/.claude/settings.json" "$HOME/.codex/hooks.json"; do
  case "$CONFIG" in
    *"/.claude/"*) CONFIG_LABEL="Claude settings" ;;
    *) CONFIG_LABEL="Codex hooks" ;;
  esac
  if [ -f "$CONFIG" ]; then
    COUNT=$(grep -c "hook_entry.py" "$CONFIG" 2>/dev/null) || COUNT=0
    echo "$CONFIG_LABEL: $COUNT hook_entry references"
  else
    echo "$CONFIG_LABEL: missing"
  fi
done

section "devices"
VOLUME_ROOT=${SIDEPULSE_TEST_VOLUME_ROOT:-/Volumes}
VOLUME_COUNT=0
for VOLUME in "$VOLUME_ROOT"/SidePulse*; do
  if [ -d "$VOLUME" ]; then
    VOLUME_COUNT=$((VOLUME_COUNT + 1))
  fi
  [ -f "$VOLUME/STATUS.TXT" ] \
    && head -c 65536 "$VOLUME/STATUS.TXT" \
       | sanitize_output \
       | grep -E "firmware_version|uptime_ms|state|temp_c" \
       | cut -c 1-512 \
       | head -4
done
echo "SidePulse volumes mounted: $VOLUME_COUNT"

section "power"
pmset -g 2>/dev/null | grep -E "SleepDisabled|sleep " | head -3
pmset -g assertions 2>/dev/null | grep -iE "sidepulse|caffeinate" | head -4 \
  || echo "no sidepulse power assertions"

echo
echo "done -- share this output; it contains no conversation content or raw device serials."
echo "It retains file names, sizes, ages, and filtered operational log lines."
