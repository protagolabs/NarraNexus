---
code_file: frontend/src/lib/htmlAnchorReplace.ts
last_verified: 2026-08-19
stub: false
---

# htmlAnchorReplace.ts — 锚定字面替换(html 逐元素)

inner 唯一→替换;inner 零命中→not-found(脚本生成,降 AI);inner 多
义→扩到 outerHTML,唯一→在其内替换(搜索从开标签 '>' 之后起,防
属性同文误中),仍多义→ambiguous。**绝不最近似替换**——错位编辑比
拒绝更糟(测试钉死)。no-change 拒绝(无事可存)。
