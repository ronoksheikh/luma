#!/usr/bin/env bash
# Luma Studio container entrypoint (runs as root only long enough to prepare /data and
# the sandbox firewall, then drops to the unprivileged `studio` user for the backend).
set -euo pipefail

DATA="${LUMA_DATA_DIR:-/data}"
mkdir -p "$DATA/projects" "$DATA/secrets"
# studio (backend) owns /data; the sandbox user `luma` shares the lumawork group so the
# agent can read/write project workspaces but never the secrets dir or the database.
chown studio:lumawork "$DATA" "$DATA/projects" 2>/dev/null || true
chmod 2775 "$DATA" "$DATA/projects" 2>/dev/null || true
chown -R studio:studio "$DATA/secrets" 2>/dev/null || true
chmod 700 "$DATA/secrets" 2>/dev/null || true
for f in "$DATA"/studio.db "$DATA"/studio.db-wal "$DATA"/studio.db-shm; do
  [ -e "$f" ] && chown studio:studio "$f" && chmod 600 "$f"
done
# project workspaces created by earlier versions / other uids
find "$DATA/projects" -maxdepth 1 -mindepth 1 -type d ! -group lumawork -exec chgrp -R lumawork {} + 2>/dev/null || true
find "$DATA/projects" -type d ! -perm -2070 -exec chmod g+rwxs {} + 2>/dev/null || true

# Firewall (needs CAP_NET_ADMIN; skipped gracefully without it):
#  * the sandbox user may never talk to the Luma API itself (no self-administration)
#  * outbound network for the sandbox follows the Settings toggle (luma-netctl)
if /usr/local/sbin/luma-netctl init >/dev/null 2>&1; then
  echo "[luma] sandbox firewall active (API port blocked for the agent)"
else
  echo "[luma] WARNING: iptables unavailable (missing NET_ADMIN?) — the terminal network toggle is disabled"
  export LUMA_NETCTL=""
fi

if [ "${LUMA_SANDBOX_SUDO:-1}" = "0" ]; then
  rm -f /etc/sudoers.d/luma-sandbox
  echo "[luma] passwordless sudo for the sandbox user is DISABLED (LUMA_SANDBOX_SUDO=0)"
fi

cd /opt/luma/backend
exec setpriv --reuid=studio --regid=studio --init-groups --inh-caps=-all \
  env HOME=/home/studio USER=studio /opt/venv/bin/python -m app.main
