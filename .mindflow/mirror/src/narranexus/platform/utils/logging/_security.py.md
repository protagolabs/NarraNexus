---
code_file: src/narranexus/platform/utils/logging/_security.py
last_verified: 2026-10-30
stub: false
---

# utils/logging/_security.py — 越界拒绝的统一日志行

## 为什么存在

跨租户的路径越界在多个独立闸口被拒（agent_id、文件路径、bundle 导入、heal 搜索根），
以前各自用不同措辞记日志或不记，日志侧无法区分"过期链接"和"有人在试边界"。现在所有这类
闸口都调 `log_boundary_violation`，行首固定为 `[security.boundary]`，部署侧的日志告警只需
匹配这一个标记。

## 行格式（与部署侧告警的契约，改动需同步）

`[security.boundary] surface=<闸口名> actor=<调用者|-> owner=<记录属主|-> detail=<repr>`

- `surface` 由代码固定，不来自用户输入。
- `detail` 通常是攻击者可控的值：用 `repr()` 保证单行（换行被转义，伪造不出第二行），
  截断到 300 字符。真实字段总在前面，告警侧读第一个 `surface=` 即可。

## 什么时候调用

只在诚实客户端不可能触发的地方调用——误报会变成告警。反例：`confine_entry` 不调用，
因为存量数据里有大量遗留布局指针会被它正常拒绝。
