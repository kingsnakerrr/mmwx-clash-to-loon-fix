# Changelog

## 1.1.0 - 2026-09-07

- 安装前检查 MMWX 容器、SQLite 结构、有效订阅和官方转换结果。
- 安装后检查 Loon 段落、策略组、代理链、FINAL 规则、流量响应头和通知接管状态。
- 验证失败时明确输出“修复无效”、未解决 Bug 编号，并保存诊断日志供 AI 或维护者继续处理。

## 1.0.0 - 2026-09-07

- 修复套餐订阅 `clash-to-loon` 返回 producer not found。
- 保留 Clash 模板分组、规则与 `dialer-proxy` 代理链语义。
- 修正 `Subscription-Userinfo` 的套餐流量显示。
- 将一次外部订阅访问产生的重复 Telegram 通知合并为一次。
- 增加备份、卸载、更新和官方修复后的自动退出机制。
