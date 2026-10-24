#!/usr/bin/env bash
# Installs the current commit on the server. Run as root from the repository folder: sudo bash deploy/install.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -x /usr/local/sbin/tickett-deploy ]] || { echo "run deploy/server-setup.sh first" >&2; exit 1; }
SHA=$(git rev-parse HEAD)

command -v npm >/dev/null || { echo "install Node 22 to build the dashboard" >&2; exit 1; }
rm -rf web/dist
(cd web && npm ci && npm run build)

git archive --format=tar -o /tmp/release.tar "$SHA"
tar --append -f /tmp/release.tar web/dist
gzip -c /tmp/release.tar > "/tmp/tickett-$SHA.tgz"
rm /tmp/release.tar
/usr/local/sbin/tickett-deploy "/tmp/tickett-$SHA.tgz" "$SHA"
