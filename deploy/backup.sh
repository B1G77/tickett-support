#!/usr/bin/env bash
# Weekly copy of the database and the dashboard logins into /var/backups/tickett-router; keeps the last 8.
set -euo pipefail
SRC=/var/lib/tickett-router/lebillet-data/router-tickett
DEST=/var/backups/tickett-router
STAMP=$(date -u +%Y-%m-%d)
install -d -o root -g root -m 0700 "$DEST"
python3 - "$SRC/router.db" "$DEST/router-$STAMP.db" <<'PY'
import sqlite3, sys
src = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
PY
cp "$SRC/accounts.json" "$DEST/accounts-$STAMP.json"
chmod 0600 "$DEST"/*
ls -1t "$DEST"/router-*.db | tail -n +9 | xargs -r rm --
ls -1t "$DEST"/accounts-*.json | tail -n +9 | xargs -r rm --
echo "backup done: $DEST/router-$STAMP.db"
