---
code_file: frontend/src/components/chat/team/rehypeMentions.ts
last_verified: 2026-08-14
stub: false
---

# rehypeMentions — 高亮 @提及，但别碰代码

## 为什么存在

agent @ 队友的那一下**就是交接本身**，被叫到的人得能一眼看见，而不必逐条读完整个房间。

第一版是在 markdown **源码**上做 `String.replace`。于是每一段包含 `@all`、`@everyone` 或
某个队友名字的代码块里，都被塞进了一段字面量
`<span data-testid="mention-all" class="…">@all</span>`——而团队房间的主要产出正是代码和
命令，`@all` 在 shell 里是真的 make target，在 Makefile 里是真的伪目标，在别的 IM 里是真的
提及语法。

markdown 会转义代码块里的 HTML，所以**不是安全问题**（文件头注释论证的是 XSS，那部分是对
的，只是论证的不是这件事）。是更平庸的那种坏：**用户复制出去的代码是坏的**，而排查方向会
先指向模型，再指向 prompt，最后才轮到渲染层。

## 为什么在 AST 上做

正则不知道什么是代码块，AST 知道。走 rehype 插件，只改 `text` 节点，祖先是 `code` / `pre`
的直接跳过。顺带把 DOM 版（`markMentions`）和字符串版两份同规则实现里的一份消掉。

## 一个必须保持一致的地方

字符集 `/@([\w一-鿿]+)/` 必须和 [[message_bus_trigger.py]] 的 `_extract_team_mentions`
以及 composer 的自动补全**逐字一致**。高亮了一个实际上不会被唤醒的人（或者漏掉一个会被
唤醒的人），比不高亮更糟——它教会读者不要相信这个高亮。
