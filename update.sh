#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_FILE="/etc/mmwx-subfix.conf"
[[ $EUID -eq 0 ]] || { echo "请使用 root 或 sudo 运行。" >&2; exit 1; }
[[ -f $CONFIG_FILE ]] || { echo "尚未安装，找不到 $CONFIG_FILE" >&2; exit 1; }
set -a
# shellcheck disable=SC1090
. "$CONFIG_FILE"
set +a

RAW_BASE="${MMWX_FIX_RAW_BASE:?配置中缺少 MMWX_FIX_RAW_BASE}"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$RAW_BASE/install.sh" -o "$TMP"
ARGS=(
  --domain "$MMWX_DOMAIN"
  --backend "$MMWX_BACKEND"
  --db "$MMWX_DB"
  --container "$MMWX_CONTAINER"
)
if [[ -n ${MMWX_NGINX_CONFIG:-} ]]; then
  ARGS+=(--nginx-config "$MMWX_NGINX_CONFIG")
fi
bash "$TMP" "${ARGS[@]}"
