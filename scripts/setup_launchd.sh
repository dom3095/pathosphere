#!/bin/bash
# Setup launchd agent for autonomous pathosphere cycle loop.
#
# Usage:
#   ./scripts/setup_launchd.sh [--interval SECONDS] [--uninstall]
#
# Default: runs every 12 hours (43200 seconds)
# Logs to: data/logs/launchd.log, data/logs/launchd_error.log

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Defaults
INTERVAL=43200  # 12 hours in seconds
UNINSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        *)
            echo "Usage: $0 [--interval SECONDS] [--uninstall]"
            exit 1
            ;;
    esac
done

# Determine user and home directory
USERNAME=$(whoami)
USERHOME=$(eval echo ~"$USERNAME")
LAUNCHD_DIR="$USERHOME/Library/LaunchAgents"
PLIST_FILE="$LAUNCHD_DIR/com.pathosphere.loop.plist"
LABEL="com.pathosphere.loop"

# Create log directory
LOG_DIR="$REPO_ROOT/data/logs"
mkdir -p "$LOG_DIR"

if [ "$UNINSTALL" = true ]; then
    echo "Uninstalling launchd agent..."
    launchctl unload "$PLIST_FILE" 2>/dev/null || true
    rm -f "$PLIST_FILE"
    echo "Uninstalled: $PLIST_FILE"
    exit 0
fi

# Create LaunchAgents directory if it doesn't exist
mkdir -p "$LAUNCHD_DIR"

# Generate plist file
cat > "$PLIST_FILE" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>-c</string>
    <string>cd $REPO_ROOT && source .venv/bin/activate && uv run pathos loop --sleep-hours $((INTERVAL / 3600))</string>
  </array>

  <key>StartInterval</key>
  <integer>$INTERVAL</integer>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd_error.log</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>
</dict>
</plist>
EOF

echo "Created launchd plist: $PLIST_FILE"
echo "  Interval: $INTERVAL seconds ($((INTERVAL / 3600)) hours)"
echo "  Logs: $LOG_DIR"

# Load the plist into launchd
echo "Loading into launchd..."
if launchctl load "$PLIST_FILE"; then
    echo "✓ Agent loaded successfully"
    echo ""
    echo "Monitor the loop:"
    echo "  tail -f $LOG_DIR/launchd.log"
    echo "  tail -f $REPO_ROOT/data/cycle_state.json"
    echo ""
    echo "Uninstall with:"
    echo "  ./scripts/setup_launchd.sh --uninstall"
else
    echo "✗ Failed to load agent. Check your .venv path."
    exit 1
fi
