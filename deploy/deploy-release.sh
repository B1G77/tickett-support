#!/usr/bin/env bash
# Installs one release and switches to it; switches back if the router has no clean cycle within 4 minutes.
# Usage (as root): tickett-deploy /tmp/tickett-<sha>.tgz <sha>
set -euo pipefail

BASE=/opt/tickett-router
STATE=/var/lib/tickett-router/lebillet-data/router-tickett
TARBALL="${1:-}"
SHA="${2:-}"
WAIT_SECONDS=240
KEEP=5

[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "bad commit id: $SHA" >&2; exit 2; }
[[ "$TARBALL" == "/tmp/tickett-$SHA.tgz" && -f "$TARBALL" ]] || { echo "bad tarball: $TARBALL" >&2; exit 2; }

REL="$BASE/releases/$SHA"
rm -rf "$REL"
mkdir -p "$REL"
tar --no-same-owner --no-same-permissions -xzf "$TARBALL" -C "$REL"
rm -f "$TARBALL"
chown -R root:tickett "$REL"
chmod -R u=rwX,g=rX,o= "$REL"

REQ_HASH=$(sha256sum "$REL/requirements.txt" | cut -d' ' -f1)
if [[ ! -x "$BASE/venv/bin/python" || "$(cat "$BASE/venv/.requirements-sha256" 2>/dev/null)" != "$REQ_HASH" ]]; then
  echo "== installing dependencies"
  python3 -m venv "$BASE/venv"
  "$BASE/venv/bin/pip" install --quiet --upgrade pip
  "$BASE/venv/bin/pip" install --quiet --require-virtualenv -r "$REL/requirements.txt"
  echo "$REQ_HASH" > "$BASE/venv/.requirements-sha256"
  chown -R root:tickett "$BASE/venv"
  chmod -R u=rwX,g=rX,o= "$BASE/venv"
fi

echo "== tests"
cd /tmp
runuser -u tickett -- env -i PATH=/usr/bin:/bin HOME=/tmp "$BASE/venv/bin/python" "$REL/tests/run.py" | tail -1

PREV=$(readlink "$BASE/current" 2>/dev/null || true)
switch_to() { ln -sfn "$1" "$BASE/current.new" && mv -T "$BASE/current.new" "$BASE/current"; }

started=$(date -u +%s)
switch_to "$REL"
systemctl restart tickett-router tickett-webapp
echo "== waiting for a clean router cycle (up to ${WAIT_SECONDS}s)"
healthy=0
for _ in $(seq 1 $((WAIT_SECONDS / 5))); do
  sleep 5
  if "$BASE/venv/bin/python" - "$STATE/heartbeat.json" "$started" <<'PY'
import json, sys
from datetime import datetime
try:
    hb = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    sys.exit(1)
t = datetime.fromisoformat(hb.get("last_ok") or "1970-01-01T00:00:00+00:00").timestamp()
sys.exit(0 if t >= int(sys.argv[2]) and hb.get("failures_in_a_row", 1) == 0 else 1)
PY
  then healthy=1; break; fi
done

if [[ $healthy -ne 1 ]]; then
  echo "!! no clean cycle after the switch - rolling back to ${PREV:-nothing}" >&2
  journalctl -u tickett-router -n 30 --no-pager >&2 || true
  if [[ -n "$PREV" ]]; then switch_to "$PREV"; systemctl restart tickett-router tickett-webapp; fi
  exit 1
fi

echo "== live: $SHA"
cd "$BASE/releases"
ls -1t | grep -vxF -e "$SHA" -e "$(basename "${PREV:-none}")" | tail -n +$((KEEP - 1)) | xargs -r rm -rf --
