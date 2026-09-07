# MMWX Clash-to-Loon 兼容修复

这是一个给 MMWX 使用的临时兼容层，解决套餐或用户订阅选择 `clash-to-loon` 时出现 HTTP 500、代理链丢失、套餐流量显示异常和 Telegram 重复通知的问题。

补丁不修改 MMWX 容器、节点数据或模板。它只接管 Nginx 的 `/x/` 订阅路径，官方支持正常的请求仍原样透传。

## 修复内容

- 当官方返回 `producer type 'clash-to-loon' not found` 时生成可供 Loon 使用的完整配置。
- 按 Clash 模板保留策略组、组内顺序和规则。
- 把 Clash `dialer-proxy` 正确转换成 Loon `[Proxy Chain]`。
- 把 `MATCH` 转换为 Loon 的 `FINAL`，并处理不能直接使用的规则集。
- 从 MMWX SQLite 数据库校正 `Subscription-Userinfo` 流量响应头。
- 一次外部订阅访问只发送一条管理员 Telegram 通知，60 秒内相同请求去重。
- 每次安装或更新前自动备份数据库和相关配置。
- Docker 镜像更新后自动复查官方实现；确认分组、规则和代理链都完整后，自动退出转换补丁并恢复原通知设置。
- 安装前确认妙妙屋确实存在并检测原始 Bug，安装后再次验证；修复无效时列出未解决 Bug，不会虚报成功。

## 适用范围

- 已验证：MMWX `0.5.3`、SQLite、Docker、Nginx、systemd。
- 默认容器名：`miaomiaowux`。
- 默认后端：`http://127.0.0.1:12889`。
- 默认数据库：`/opt/miaomiaowux/data/mmwx.db`。

请先确认服务器上只有一套 MMWX。脚本不会上传数据库，也不会读取或保存节点密码到仓库。

## 一键安装

```bash
curl -fsSL https://raw.githubusercontent.com/kingsnakerrr/mmwx-clash-to-loon-fix/main/install.sh | \
  sudo bash -s -- --domain mmw.example.com
```

使用非默认路径时：

```bash
sudo bash install.sh \
  --domain mmw.example.com \
  --backend http://127.0.0.1:12889 \
  --db /opt/miaomiaowux/data/mmwx.db \
  --container miaomiaowux \
  --nginx-config /etc/nginx/sites-enabled/mmwx.conf
```

脚本会先执行 SQLite 在线备份和 Nginx 配置备份。Nginx 检查失败时会立即恢复原文件。

安装严格按以下顺序执行：

1. 检查 MMWX 容器、数据库结构、有效订阅和后端连接，并列出检测到的官方 Bug。
2. 备份并安装兼容服务。
3. 使用实际订阅复测 Loon 段落、策略组、代理链、FINAL 规则、流量响应头和通知接管状态。

只有第三步全部通过才显示“安装完成”。如有一项失败，脚本会输出 `MMWX_FIX_RESULT=修复无效` 和 `MMWX_UNRESOLVED_BUGS=...`，同时把完整结果保存到：

```text
/opt/mmwx-subinfo-proxy/last-diagnostic.txt
```

## 更新

```bash
curl -fsSL https://raw.githubusercontent.com/kingsnakerrr/mmwx-clash-to-loon-fix/main/update.sh | sudo bash
```

更新会沿用 `/etc/mmwx-subfix.conf` 中的域名、后端、数据库和容器设置，并生成新的备份。

## 卸载

```bash
curl -fsSL https://raw.githubusercontent.com/kingsnakerrr/mmwx-clash-to-loon-fix/main/uninstall.sh | sudo bash
```

卸载会删除 systemd 服务和脚本管理的 Nginx 区块，恢复安装前的 MMWX 通知开关。历史备份会保留在 `/opt/mmwx-subinfo-proxy/backups/`。

## 验证

```bash
systemctl status mmwx-subinfo-proxy.service --no-pager
systemctl status mmwx-clash-to-loon-watch.timer --no-pager
journalctl -u mmwx-subinfo-proxy.service -n 100 --no-pager
curl -I 'https://mmw.example.com/x/你的短码?t=clash-to-loon'
sudo bash -c 'set -a; . /etc/mmwx-subfix.conf; set +a; \
  python3 /opt/mmwx-subinfo-proxy/diagnose.py --mode post'
```

生成的配置至少应包含 `[General]`、`[Proxy]`、`[Proxy Group]` 和 `[Rule]`。如果源 Clash 节点含有 `dialer-proxy`，还应包含 `[Proxy Chain]`。

## 自动退出机制

定时器每天检查一次 Docker 镜像 ID，镜像没有变化时不发送请求。镜像升级后，它会同时读取官方 Clash 源配置和官方 Clash-to-Loon 输出；只有基本段落完整，并且源配置含代理链时输出也含 `[Proxy Chain]`，才会停用本项目的转换逻辑。

自动退出后仍保留流量响应头校正服务；官方订阅通知开关会恢复到安装前的值。状态记录在：

```text
/opt/mmwx-subinfo-proxy/clash-to-loon-fallback.retired
```

## 常见问题

**安装后出现 502**

检查 MMWX 后端是否监听 `127.0.0.1:12889`，再查看代理服务日志。

**还是收到三条通知**

检查数据库中的 `system_config.notify_subscribe_fetch` 是否为 `0`，以及兼容服务是否只有一个实例。

**不希望脚本修改 Nginx**

安装时加 `--no-nginx`，然后自行将 `/x/` 反向代理到 `127.0.0.1:12890`。

**官方修复后会不会冲突**

监控器不会仅凭 HTTP 200 就移除补丁；它会检查实际配置结构和代理链。验证通过后才自动退出，因此不会长期覆盖官方实现。

更适合提交给上游的说明见 [给作者的 Bug 说明](docs/给作者的Bug说明.md)。

## 交给 AI 继续修复

把下面三项提供给 AI 即可定位失败点，不要附带数据库、密码、Token 或完整订阅链接：

```bash
cat /opt/mmwx-subinfo-proxy/last-diagnostic.txt
journalctl -u mmwx-subinfo-proxy.service -n 200 --no-pager
docker inspect miaomiaowux --format '{{.Config.Image}} {{.Image}}'
```

要求 AI 根据 `MMWX_UNRESOLVED_BUGS` 修改本仓库、补充对应自动测试、更新 `CHANGELOG.md`，测试通过后再发布。Bug 编号是稳定的，方便不同账号或不同 AI 接手。

## 许可

[MIT](LICENSE)
