#!/bin/sh
# JR Bar field diagnostics -- pure shell, for the support case where
# Python itself is broken. Prints redacted environment facts only:
# never transcripts, prompts, tool payloads, or tokens.
set -u

section() { printf '\n== %s\n' "$1"; }

echo "JR Bar field diagnostics ($(date '+%Y-%m-%dT%H:%M:%S%z'))"

section "system"
sw_vers 2>/dev/null || echo "sw_vers unavailable"
uname -m

section "app + launchd"
for APP in "$HOME/Applications/SidePulse.app" "/Applications/SidePulse.app"; do
  if [ -d "$APP" ]; then
    echo "app: $APP (modified $(stat -f %Sm "$APP/Contents/MacOS/SidePulse" 2>/dev/null || echo '?'))"
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
  tail -200 "$LOG" | grep -E "error|timing|state=|device|closed_lid|keep_awake" | tail -12
else
  echo "no app log"
fi

section "hooks configured"
for CONFIG in "$HOME/.claude/settings.json" "$HOME/.codex/hooks.json"; do
  if [ -f "$CONFIG" ]; then
    COUNT=$(grep -c "hook_entry.py" "$CONFIG" 2>/dev/null) || COUNT=0
    echo "$CONFIG: $COUNT hook_entry references"
  else
    echo "$CONFIG: missing"
  fi
done

section "devices"
ls -d /Volumes/SidePulse* 2>/dev/null || echo "no SidePulse volumes mounted"
for VOLUME in /Volumes/SidePulse*; do
  [ -f "$VOLUME/STATUS.TXT" ] \
    && tr -d '\0' < "$VOLUME/STATUS.TXT" \
       | grep -E "serial|firmware_version|uptime_ms|state|temp_c" | head -5
done

section "power"
pmset -g 2>/dev/null | grep -E "SleepDisabled|sleep " | head -3
pmset -g assertions 2>/dev/null | grep -iE "sidepulse|caffeinate" | head -4 \
  || echo "no sidepulse power assertions"

echo
echo "done -- share this output; it contains no conversation content."
