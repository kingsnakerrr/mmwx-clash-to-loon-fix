#!/usr/bin/env bash
set -Eeuo pipefail

VERSION="1.0.0"
RAW_BASE="${MMWX_FIX_RAW_BASE:-https://raw.githubusercontent.com/kingsnakerrr/mmwx-clash-to-loon-fix/main}"
INSTALL_DIR="/opt/mmwx-subinfo-proxy"
CONFIG_FILE="/etc/mmwx-subfix.conf"
BACKEND="http://127.0.0.1:12889"
DB="/opt/miaomiaowux/data/mmwx.db"
CONTAINER="miaomiaowux"
DOMAIN=""
NGINX_CONFIG=""
SKIP_NGINX=0

usage() {
  cat <<'EOF'
用法: sudo bash install.sh --domain mmw.example.com [选项]

选项:
  --domain DOMAIN        MMWX 对外域名（必填）
  --backend URL          MMWX 本地后端，默认 http://127.0.0.1:12889
  --db PATH              SQLite 数据库路径
  --container NAME       MMWX Docker 容器名，默认 miaomiaowux
  --nginx-config PATH    明确指定站点配置文件
  --no-nginx             不修改 Nginx，只安装本地服务
  -h, --help             显示帮助
EOF
}

while (($#)); do
  case "$1" in
    --domain) DOMAIN="${2:?缺少域名}"; shift 2 ;;
    --backend) BACKEND="${2:?缺少后端地址}"; shift 2 ;;
    --db) DB="${2:?缺少数据库路径}"; shift 2 ;;
    --container) CONTAINER="${2:?缺少容器名}"; shift 2 ;;
    --nginx-config) NGINX_CONFIG="${2:?缺少 Nginx 配置路径}"; shift 2 ;;
    --no-nginx) SKIP_NGINX=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage; exit 2 ;;
  esac
done

[[ $EUID -eq 0 ]] || { echo "请使用 root 或 sudo 运行。" >&2; exit 1; }
[[ $DOMAIN =~ ^[A-Za-z0-9.-]+$ ]] || { echo "--domain 必填且格式不正确。" >&2; exit 1; }
[[ -f $DB ]] || { echo "找不到数据库: $DB" >&2; exit 1; }
for command in python3 curl systemctl; do
  command -v "$command" >/dev/null || { echo "缺少命令: $command" >&2; exit 1; }
done

if ! python3 -c 'import yaml' 2>/dev/null; then
  if command -v apt-get >/dev/null; then
    apt-get update && apt-get install -y python3-yaml
  else
    echo "缺少 PyYAML，请先安装 Python yaml 模块。" >&2
    exit 1
  fi
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fetch_file() {
  local relative="$1" destination="$2"
  if [[ -f "$SCRIPT_DIR/$relative" ]]; then
    install -m 0644 "$SCRIPT_DIR/$relative" "$destination"
  else
    curl -fsSL "$RAW_BASE/$relative" -o "$destination"
  fi
}

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$INSTALL_DIR/backups/$STAMP"
mkdir -p "$BACKUP_DIR"
python3 - "$DB" "$BACKUP_DIR/mmwx.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect(f'file:{sys.argv[1]}?mode=ro', uri=True)
target = sqlite3.connect(sys.argv[2])
source.backup(target)
target.close()
source.close()
PY

if [[ -f "$INSTALL_DIR/proxy.py" ]]; then
  cp -a "$INSTALL_DIR/proxy.py" "$BACKUP_DIR/proxy.py.previous"
fi

if [[ -f "$INSTALL_DIR/notify-subscribe-fetch.previous" ]]; then
  PREVIOUS_NOTIFY="$(cat "$INSTALL_DIR/notify-subscribe-fetch.previous")"
else
  PREVIOUS_NOTIFY="$(python3 - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
row = con.execute('select notify_subscribe_fetch from system_config where id=1').fetchone()
print(1 if row and row[0] else 0)
con.close()
PY
)"
  printf '%s\n' "$PREVIOUS_NOTIFY" > "$INSTALL_DIR/notify-subscribe-fetch.previous"
fi

fetch_file "src/proxy.py" "$TMP_DIR/proxy.py"
fetch_file "src/proxy-header-only.py" "$TMP_DIR/proxy.py.header-only"
fetch_file "src/watch-clash-to-loon.py" "$TMP_DIR/watch-clash-to-loon.py"
python3 -m py_compile "$TMP_DIR/proxy.py" "$TMP_DIR/proxy.py.header-only" "$TMP_DIR/watch-clash-to-loon.py"
install -m 0755 "$TMP_DIR/proxy.py" "$INSTALL_DIR/proxy.py"
install -m 0755 "$TMP_DIR/proxy.py.header-only" "$INSTALL_DIR/proxy.py.header-only"
install -m 0755 "$TMP_DIR/watch-clash-to-loon.py" "$INSTALL_DIR/watch-clash-to-loon.py"

cat > "$CONFIG_FILE" <<EOF
MMWX_BACKEND=$BACKEND
MMWX_DB=$DB
MMWX_DOMAIN=$DOMAIN
MMWX_CONTAINER=$CONTAINER
MMWX_FIX_DIR=$INSTALL_DIR
MMWX_SUBINFO_LISTEN=127.0.0.1
MMWX_SUBINFO_PORT=12890
MMWX_PROXY_NOTIFY=$PREVIOUS_NOTIFY
MMWX_NOTIFY_DEDUPE_SECONDS=60
MMWX_FIX_RAW_BASE=$RAW_BASE
EOF
chmod 0600 "$CONFIG_FILE"

for unit in mmwx-subinfo-proxy.service mmwx-clash-to-loon-watch.service mmwx-clash-to-loon-watch.timer; do
  fetch_file "systemd/$unit" "/etc/systemd/system/$unit"
done

if [[ $SKIP_NGINX -eq 0 ]]; then
  command -v nginx >/dev/null || { echo "找不到 nginx；可用 --no-nginx 跳过。" >&2; exit 1; }
  if [[ -z $NGINX_CONFIG ]]; then
    NGINX_CONFIG="$(grep -RslE "server_name[[:space:]]+([^;[:space:]]+[[:space:]]+)*${DOMAIN//./\\.}([[:space:];])" /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null | head -n1 || true)"
  fi
  [[ -f $NGINX_CONFIG ]] || { echo "没有找到域名对应的 Nginx 配置，请用 --nginx-config 指定。" >&2; exit 1; }
  cp -a "$NGINX_CONFIG" "$BACKUP_DIR/nginx.conf.previous"
  NGINX_CONFIG="$NGINX_CONFIG" PREVIOUS_BLOCK_PATH="$INSTALL_DIR/nginx-x-location.previous" python3 <<'PY'
import os, re
path = os.environ['NGINX_CONFIG']
previous_path = os.environ['PREVIOUS_BLOCK_PATH']
start = '    # BEGIN mmwx-clash-to-loon-fix'
end = '    # END mmwx-clash-to-loon-fix'
block = '''    # BEGIN mmwx-clash-to-loon-fix
    location ^~ /x/ {
        proxy_pass http://127.0.0.1:12890;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
    }
    # END mmwx-clash-to-loon-fix'''
text = open(path, encoding='utf-8').read()
if start in text and end in text:
    text = re.sub(re.escape(start) + r'.*?' + re.escape(end), block, text, flags=re.S)
else:
    existing = re.search(r'(?m)^\s*location\s+(?:\^~\s+)?/x/\s*\{', text)
    if existing:
        depth = 0
        finish = None
        for index in range(existing.start(), len(text)):
            if text[index] == '{':
                depth += 1
            elif text[index] == '}':
                depth -= 1
                if depth == 0:
                    finish = index + 1
                    break
        if finish is None:
            raise SystemExit('现有 location /x/ 结构不完整，未修改 Nginx 配置')
        if not os.path.exists(previous_path):
            open(previous_path, 'w', encoding='utf-8').write(
                text[existing.start():finish] + '\n'
            )
        text = text[:existing.start()] + block + text[finish:]
    else:
        match = re.search(r'(?m)^(\s*)location\s+/\s*\{', text)
        if not match:
            raise SystemExit('无法定位 location /，未修改 Nginx 配置')
        text = text[:match.start()] + block + '\n\n' + text[match.start():]
open(path, 'w', encoding='utf-8').write(text)
PY
  if ! nginx -t; then
    cp -a "$BACKUP_DIR/nginx.conf.previous" "$NGINX_CONFIG"
    echo "Nginx 检查失败，已恢复原配置。" >&2
    exit 1
  fi
  systemctl reload nginx
  printf '%s\n' "$NGINX_CONFIG" > "$INSTALL_DIR/nginx-config.path"
  printf 'MMWX_NGINX_CONFIG=%s\n' "$NGINX_CONFIG" >> "$CONFIG_FILE"
fi

rm -f "$INSTALL_DIR/clash-to-loon-fallback.retired"
python3 - "$DB" <<'PY'
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute("update system_config set notify_subscribe_fetch=0, updated_at=datetime('now') where id=1")
con.commit()
con.close()
PY
systemctl daemon-reload
systemctl enable --now mmwx-subinfo-proxy.service
systemctl enable --now mmwx-clash-to-loon-watch.timer
systemctl is-active --quiet mmwx-subinfo-proxy.service

echo "安装完成（v$VERSION）。备份目录: $BACKUP_DIR"
echo "测试链接格式: https://$DOMAIN/x/你的短码?t=clash-to-loon"
