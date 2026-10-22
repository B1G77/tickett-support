#!/usr/bin/env bash
# Install, update or remove the router and the dashboard as launchd agents on this Mac.
#
#   scripts/router-install.sh password    store the mailbox password in the login Keychain
#   scripts/router-install.sh install     copy the code out of ~/Documents and start it
#   scripts/router-install.sh uninstall   stop and remove the agent (data and logs are kept)
#
# Re-run "install" after changing any code: it copies the current version.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/.local/share/lebillet-router"
DATA="$HOME/lebillet-data"
PY="$HOME/.venvs/lebillet-email/bin/python"
LABEL="com.lebillet.router"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
WEB_LABEL="com.lebillet.webapp"
WEB_PLIST="$HOME/Library/LaunchAgents/$WEB_LABEL.plist"
cfg() { python3 -c "import json,sys;c=json.load(open('$REPO/config/router.json'));print(eval(sys.argv[1]))" "$1"; }
ACCOUNT="$(cfg "c['imap']['user']")"
SERVICE="$(cfg "c['imap']['keychain_service']")"
INTERVAL="$(cfg "c['poll_seconds']")"

case "${1:-}" in
  password)
    echo "Password for $ACCOUNT (typed invisibly, stored only in your login Keychain):"
    security add-generic-password -U -s "$SERVICE" -a "$ACCOUNT" -w
    echo "stored."
    ;;
  install)
    [ -x "$PY" ] || { echo "missing $PY (the Python environment with the Anthropic SDK)"; exit 1; }
    security find-generic-password -s "$SERVICE" -a "$ACCOUNT" >/dev/null 2>&1 \
      || { echo "no Keychain password yet: run '$0 password' first"; exit 1; }
    command -v npm >/dev/null 2>&1 \
      || { echo "npm not found: install Node 22 (e.g. 'brew install node@22') to build the dashboard"; exit 1; }
    (cd "$REPO/web" && npm ci && npm run build)
    mkdir -p "$APP" "$DATA/router" "$APP/web/dist"
    # macOS privacy protection usually stops background jobs reading ~/Documents,
    # so the agent runs from a copy. The data it needs already lives in ~/lebillet-data.
    rsync -a --delete "$REPO/src/" "$APP/src/"
    rsync -a --delete "$REPO/config/" "$APP/config/"
    rsync -a --delete "$REPO/web/dist/" "$APP/web/dist/"
    ln -sfn "$DATA" "$APP/data"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$APP/src/router.py</string><string>--loop</string></array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>ProcessType</key><string>Adaptive</string>
  <key>StandardOutPath</key><string>$DATA/router/launchd.out</string>
  <key>StandardErrorPath</key><string>$DATA/router/launchd.err</string>
</dict></plist>
EOF
    cat > "$WEB_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$WEB_LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$APP/src/webapp.py</string></array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DATA/router/webapp.out</string>
  <key>StandardErrorPath</key><string>$DATA/router/webapp.err</string>
</dict></plist>
EOF
    for label in "$LABEL" "$WEB_LABEL"; do
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
      # bootout returns before the old job is gone; bootstrapping too soon fails with error 5
      for _ in 1 2 3 4 5 6 7 8 9 10; do
        launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || break
        sleep 1
      done
    done
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    launchctl bootstrap "gui/$(id -u)" "$WEB_PLIST"
    echo "router running every ${INTERVAL}s. Decisions: the router.log in state_dir (config/router.json)"
    echo "dashboard: http://127.0.0.1:$(cfg "c['webapp']['port']")/"
    echo "Next (first time): set the two dashboard passwords:"
    echo "  $PY \"$APP/src/webapp.py\" --set-password admin"
    echo "  $PY \"$APP/src/webapp.py\" --set-password agent"
    ;;
  uninstall)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootout "gui/$(id -u)/$WEB_LABEL" 2>/dev/null || true
    rm -f "$PLIST" "$WEB_PLIST"
    echo "router and dashboard stopped and removed (data and logs kept in $DATA)"
    ;;
  *)
    echo "usage: $0 password|install|uninstall"; exit 2
    ;;
esac
