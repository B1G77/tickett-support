#!/usr/bin/env bash
# One-time server setup. Run as root: sudo bash server-setup.sh <dashboard address>
# Safe to run again: existing secrets and settings are kept.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ADDRESS="${1:-}"

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
[[ -n "$ADDRESS" ]] || { echo "usage: sudo bash server-setup.sh <dashboard address, e.g. suporte.example.com>" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || { echo "needs Python 3.12 or newer" >&2; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "install python3-venv first" >&2; exit 1; }

id tickett >/dev/null 2>&1 || useradd --system --home-dir /var/lib/tickett-router --shell /usr/sbin/nologin tickett
id deploy >/dev/null 2>&1 || useradd --create-home --shell /bin/bash deploy

install -d -o root -g tickett -m 0750 /opt/tickett-router /opt/tickett-router/releases
install -d -o tickett -g tickett -m 0700 /var/lib/tickett-router /var/lib/tickett-router/lebillet-data
install -d -o root -g tickett -m 0750 /etc/tickett-router

if [[ ! -f /etc/tickett-router/secrets.env ]]; then
  printf 'IMAP_PASSWORD=\nANTHROPIC_API_KEY=\n' > /etc/tickett-router/secrets.env
fi
chown root:tickett /etc/tickett-router/secrets.env
chmod 0640 /etc/tickett-router/secrets.env

if [[ ! -f /etc/tickett-router/router.local.json ]]; then
  cat > /etc/tickett-router/router.local.json <<JSON
{
  "webapp": {"hosts": ["$ADDRESS"], "secure_cookie": true, "trusted_proxy": true}
}
JSON
fi
chown root:tickett /etc/tickett-router/router.local.json
chmod 0640 /etc/tickett-router/router.local.json

# The user deploy may run this one script as root, nothing else.
install -o root -g root -m 0755 "$HERE/deploy-release.sh" /usr/local/sbin/tickett-deploy
echo 'deploy ALL=(root) NOPASSWD: /usr/local/sbin/tickett-deploy' > /etc/sudoers.d/tickett-deploy.tmp
visudo -cf /etc/sudoers.d/tickett-deploy.tmp >/dev/null
chmod 0440 /etc/sudoers.d/tickett-deploy.tmp
mv /etc/sudoers.d/tickett-deploy.tmp /etc/sudoers.d/tickett-deploy

install -o root -g root -m 0644 "$HERE/tickett-router.service" /etc/systemd/system/tickett-router.service
install -o root -g root -m 0644 "$HERE/tickett-webapp.service" /etc/systemd/system/tickett-webapp.service
systemctl daemon-reload
systemctl enable tickett-router tickett-webapp >/dev/null

echo "Done. Next steps are in the deployment document."
