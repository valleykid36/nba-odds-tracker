#!/bin/bash
# schedule_mac.sh — run the FREE update automatically every day (no credits, no questions).
#
#   ./schedule_mac.sh          turn it on   (safe to run again; it just refreshes the schedule)
#   ./schedule_mac.sh off      turn it off
#
# Each run: box scores, live/final scores, grading, official NBA injury report + ESPN,
# team pace/ratings and shooting splits. Times are picked around when teams file
# injury reports (5 PM is the league deadline for night games) plus a late run that
# grades the night's picks. If the Mac is asleep at a run time, it runs when it wakes.
# Output goes to logs/update.log in this folder.

set -e
cd "$(dirname "$0")"
DIR="$(pwd)"
LABEL="com.nbapicks.update"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
if [ "$1" = "off" ]; then
  rm -f "$PLIST"
  echo "Daily update turned off."
  exit 0
fi

if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup_mac.sh first."
  exit 1
fi
mkdir -p logs "$HOME/Library/LaunchAgents"

times=""
for hm in "10 10" "13 10" "17 40" "19 10" "23 50"; do
  set -- $hm
  times="$times<dict><key>Hour</key><integer>$1</integer><key>Minute</key><integer>$2</integer></dict>"
done

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>ProgramArguments</key>
  <array><string>$DIR/.venv/bin/python</string><string>daily.py</string><string>update</string></array>
  <key>StartCalendarInterval</key><array>$times</array>
  <key>StandardOutPath</key><string>$DIR/logs/update.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/update.log</string>
</dict>
</plist>
PLIST

launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Daily update is on: 10:10 AM, 1:10 PM, 5:40 PM, 7:10 PM and 11:50 PM."
echo "Log: $DIR/logs/update.log      Turn off: ./schedule_mac.sh off"
