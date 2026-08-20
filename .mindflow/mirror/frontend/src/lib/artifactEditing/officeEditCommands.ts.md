---
code_file: frontend/src/lib/artifactEditing/officeEditCommands.ts
last_verified: 2026-08-19
stub: false
---

# officeEditCommands.ts — T1 office 直改词汇(wire 格式唯一驻地)

officecli 1.0.144 实测(2026-08-19):batch 项={command,path,props?};
set 的变更走 props(text/bold/italic/color);watch 页选区上报体=
{"paths":[...]}。所有跟 watch 编辑 API 说话的面都过本模块——wire
格式只活在一处,officecli 升级只改这里+跑探针。

## 2026-08-19(二)— T2 词汇 + 路径分类器

move(index 0 基)/add row|column(parent+index)/formula prop/src prop
均经 CLI+batch 实测;分类器:slideIndexFromPath(仅整页 /slide[N])、
cellFromPath(/Sheet/A1 型,方括号段排除)、isPicturePath(pic[N] 与
picture[@id=])。**形状拖拽/缩放已按降级纪律砍**:探针中 query 返回的
原始 OOXML 路径 set 不收、/slide[N]/sp[N] 解析不稳定——寻址未验证
即不造 UI,待 officecli 升级或 watch 选区实测后再议(follow-up)。
