#!/bin/bash
# Installs a macOS LaunchAgent that starts the trade agent scheduler
# automatically every time you log in.
#
# Usage: bash setup_autostart.sh
# Remove: bash setup_autostart.sh --remove

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
PLIST_NAME="com.tradeagent.scheduler"
PLIST_PATH="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
LOG_DIR="$PROJECT_DIR/logs"

mkdir -p "$LOG_DIR"

if [ "$1" == "--remove" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null
    rm -f "$PLIST_PATH"
    echo "Auto-start removed."
    exit 0
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "ERROR: venv not found at $VENV_PYTHON"
    echo "Run: python -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_NAME</string>

    <key>ProgramArguments</key>
    <array>
        <string>$VENV_PYTHON</string>
        <string>$PROJECT_DIR/main.py</string>
        <string>schedule</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/scheduler.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/scheduler_error.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null
launchctl load "$PLIST_PATH"

echo ""
echo "✅ Auto-start installed!"
echo ""
echo "The scheduler will now start automatically every time you log in."
echo ""
echo "Useful commands:"
echo "  Check status : launchctl list | grep tradeagent"
echo "  View logs    : tail -f $LOG_DIR/scheduler.log"
echo "  Stop now     : launchctl unload $PLIST_PATH"
echo "  Remove       : bash setup_autostart.sh --remove"
