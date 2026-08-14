---
code_file: frontend/src/components/chat/team/mentionPattern.ts
last_verified: 2026-08-14
stub: false
---

# mentionPattern — 什么算一个 @提及，只说一次

## 为什么存在

前端有两个渲染 @提及的地方，而且都必须存在：

- [[TeamMessageBubble.tsx]] 的 `markMentions` —— 用户自己的消息**不走 markdown**，需要一个
  直接返回 React 节点的版本；
- [[rehypeMentions.ts]] —— agent 说的话走 markdown，必须在 AST 上做，否则会污染代码块。

两者需要**同一个答案**，此前各自抄了一份正则，在同一个文件夹里，而且各自的注释都写着"这
必须和另一份保持一致"。

## 更远的那条约束

这个字符集还必须和**服务端** `message_bus_trigger._extract_team_mentions`、以及 composer
的自动补全逐字一致。把它收在一个文件里并不能跨语言强制这件事——但它消掉了这一侧正在漂移的
那份拷贝。

为什么这条约束重要：高亮了一个实际上**不会被唤醒**的人，或者漏掉一个**会被唤醒**的人，
比完全不高亮更糟——它教会读者不要相信这个高亮，而这个高亮存在的全部意义就是"被叫到的人
一眼能看见"。

## 一个容易踩的细节

`MENTION_PATTERN` 带 `g` 标志，而 `lastIndex` 是**有状态的**。共享同一个实例会让第二个调用
方从上一个调用方停下的位置开始匹配。所以对外给的是 `mentionMatcher()`——每次要一个新的。
