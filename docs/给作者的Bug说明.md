# 给 MMWX 作者的 Bug 说明

## 现象

套餐/用户订阅使用 `?t=clash-to-loon` 时，服务端返回 HTTP 500：

```text
producer type 'clash-to-loon' not found
```

同一条订阅的 `?t=clash` 与 `?t=loon` 都能正常返回。除此之外，一次 Clash-to-Loon 请求在兼容转换时可能触发三次“订阅获取”通知。

## 原因

1. 后端没有注册 `clash-to-loon` producer，前端虽然提供这个复制选项，服务端却无法处理。
2. Clash 的 `dialer-proxy` 不能只当普通代理输出。Loon 需要 `[Proxy Chain]`，否则代理链会丢失。
3. Clash `MATCH` 在 Loon 中应转换为 `FINAL`。
4. `.mrs` 规则不能简单改后缀为 `.list`；目标 URL 不一定存在，需转换为 Loon 能读取的内容。
5. 兼容层为了生成结果会请求原始地址一次，再请求 `t=clash` 和 `t=loon`，后端把三次内部请求都当成用户访问，导致 Telegram 通知重复。

## 临时修复做法

- Nginx 仅把 `/x/` 转给本机兼容服务，后台管理页面与其他接口不受影响。
- 兼容服务先请求官方 `clash-to-loon`；只有确认返回 `producer not found` 时才启用转换。
- 转换时以 Clash 配置保留分组和规则，以 Loon 输出取得客户端兼容的节点行。
- 遇到 `dialer-proxy` 时保留真实落地节点，并生成对应 `[Proxy Chain]`。
- 对无法直接供 Loon 使用的规则集进行转换或内联。
- 关闭后端的订阅获取通知，由兼容层在最终外部请求成功后发送一次，并在 60 秒内去重。
- 保留补丁前的通知设置，卸载或自动退出补丁时恢复。

## 建议官方修复

后端原生注册 `clash-to-loon` producer，并增加包含以下内容的回归测试：普通节点、`dialer-proxy`、策略组顺序、`MATCH`、远程规则集、套餐短码和用户短码。通知应只在一次最终外部订阅响应成功后触发，不应统计内部格式转换请求。
