#!/usr/bin/env bash
set -Eeuo pipefail

INSTALL_DIR="/opt/mmwx-subinfo-proxy"
CONFIG_FILE="/etc/mmwx-subfix.conf"
[[ $EUID -eq 0 ]] || { echo "请使用 root 或 sudo 运行。" >&2; exit 1; }

if [[ -f $CONFIG_FILE ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$CONFIG_FILE"
  set +a
fi
DB="${MMWX_DB:-/opt/miaomiaowux/data/mmwx.db}"
PREVIOUS=1
[[ -f "$INSTALL_DIR/notify-subscribe-fetch.previous" ]] && PREVIOUS="$(cat "$INSTALL_DIR/notify-subscribe-fetch.previous")"

systemctl disable --now mmwx-clash-to-loon-watch.timer mmwx-subinfo-proxy.service 2>/dev/null || true
rm -f /etc/systemd/system/mmwx-subinfo-proxy.service \
  /etc/systemd/system/mmwx-clash-to-loon-watch.service \
  /etc/systemd/system/mmwx-clash-to-loon-watch.timer
systemctl daemon-reload

if [[ -f $DB ]]; then
  python3 - "$DB" "$PREVIOUS" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("update system_config set notify_subscribe_fetch=?, updated_at=datetime('now') where id=1", (int(sys.argv[2]),))
con.commit()
con.close()
PY
fi

if [[ -f "$INSTALL_DIR/nginx-config.path" ]]; then
  NGINX_CONFIG="$(cat "$INSTALL_DIR/nginx-config.path")"
  if [[ -f $NGINX_CONFIG ]]; then
    NGINX_CONFIG="$NGINX_CONFIG" PREVIOUS_BLOCK_PATH="$INSTALL_DIR/nginx-x-location.previous" python3 <<'PY'
import os, re
path = os.environ['NGINX_CONFIG']
previous_path = os.environ['PREVIOUS_BLOCK_PATH']
text = open(path, encoding='utf-8').read()
replacement = ''
if os.path.isfile(previous_path):
    replacement = open(previous_path, encoding='utf-8').read().rstrip() + '\n'
text = re.sub(
    r'(?ms)^\s*# BEGIN mmwx-clash-to-loon-fix\n.*?^\s*# END mmwx-clash-to-loon-fix\n?',
    replacement,
    text,
)
open(path, 'w', encoding='utf-8').write(text)
PY
    nginx -t && systemctl reload nginx
  fi
fi

rm -f "$CONFIG_FILE"
echo "补丁已卸载，数据库备份和 $INSTALL_DIR/backups 均已保留。"
