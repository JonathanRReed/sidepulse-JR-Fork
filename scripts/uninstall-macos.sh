#!/bin/bash
set -euo pipefail

APP_PATH="${SIDEPULSE_APP_PATH:-/Applications/SidePulse.app}"
APP_BINARY="$APP_PATH/Contents/MacOS/SidePulse"
CLI_LINK="${SIDEPULSE_CLI_LINK:-/usr/local/bin/sidepulse}"
RECEIPT_DIR="${SIDEPULSE_RECEIPT_DIR:-/var/db/sidepulse}"
PURGE_STATE=0
REMOVE_APP=1
TARGET_USER="${SIDEPULSE_USER:-}"

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/uninstall-macos.sh [options]

  --user NAME    Remove user-owned SidePulse integrations for NAME.
  --keep-app     Keep /Applications/SidePulse.app.
  --purge-state  Also remove SidePulse settings, logs, and local history.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --user)
            shift
            [ "$#" -gt 0 ] || { usage >&2; exit 2; }
            TARGET_USER="$1"
            ;;
        --keep-app) REMOVE_APP=0 ;;
        --purge-state) PURGE_STATE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
    echo "Run the macOS uninstaller with sudo." >&2
    exit 2
fi

if [ -z "$TARGET_USER" ]; then
    TARGET_USER="$(/usr/bin/stat -f '%Su' /dev/console)"
fi
if [ -z "$TARGET_USER" ] || [ "$TARGET_USER" = "root" ] || [ "$TARGET_USER" = "loginwindow" ]; then
    echo "Specify the SidePulse user with --user NAME." >&2
    exit 2
fi

TARGET_UID="$(/usr/bin/id -u "$TARGET_USER")"
TARGET_HOME="$(/usr/bin/dscl . -read "/Users/$TARGET_USER" NFSHomeDirectory 2>/dev/null | /usr/bin/awk '{print $2}')"
if [ -z "$TARGET_HOME" ] || [ ! -d "$TARGET_HOME" ]; then
    echo "Could not resolve a safe home directory for $TARGET_USER." >&2
    exit 2
fi

if [ ! -x "$APP_BINARY" ]; then
    echo "SidePulse application executable is missing: $APP_BINARY" >&2
    exit 2
fi

run_as_user() {
    /bin/launchctl asuser "$TARGET_UID" \
        /usr/bin/sudo -H -u "$TARGET_USER" \
        /usr/bin/env \
            HOME="$TARGET_HOME" \
            USER="$TARGET_USER" \
            LOGNAME="$TARGET_USER" \
            "$@"
}

# Remove only SidePulse-owned integrations. Provider installers preserve every
# unrelated hook entry, and the status-bar command removes only its own plist.
run_as_user "$APP_BINARY" status-bar stop
run_as_user "$APP_BINARY" agent-monitor uninstall all
run_as_user "$APP_BINARY" sdejectguard uninstall --scope user

# System-owned helpers are removed only through their reviewed commands.
/usr/bin/env \
    SUDO_USER="$TARGET_USER" \
    USER="$TARGET_USER" \
    LOGNAME="$TARGET_USER" \
    HOME="$TARGET_HOME" \
    "$APP_BINARY" status-bar uninstall-sleep-helper
"$APP_BINARY" sdejectguard uninstall --scope system

# Remove the CLI link only if it is the exact link created by the package.
if [ -L "$CLI_LINK" ] && [ "$(/usr/bin/readlink "$CLI_LINK")" = "$APP_BINARY" ]; then
    /bin/rm -f "$CLI_LINK"
elif [ -e "$CLI_LINK" ] || [ -L "$CLI_LINK" ]; then
    echo "Left existing $CLI_LINK unchanged because SidePulse does not own it."
fi

/bin/rm -rf "$RECEIPT_DIR"

if [ "$PURGE_STATE" -eq 1 ]; then
    /bin/rm -rf \
        "$TARGET_HOME/.config/sidepulse" \
        "$TARGET_HOME/.local/state/sidepulse" \
        "$TARGET_HOME/.local/share/sidepulse"
fi

if [ "$REMOVE_APP" -eq 1 ]; then
    /bin/rm -rf "$APP_PATH"
fi

printf '%s\n' "SidePulse integrations removed for $TARGET_USER."
