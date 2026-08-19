---
code_file: frontend/src/lib/officeEditCommands.ts
last_verified: 2026-08-19
stub: false
---

# officeEditCommands.ts — T1 office 直改词汇(wire 格式唯一驻地)

officecli 1.0.144 实测(2026-08-19):batch 项={command,path,props?};
set 的变更走 props(text/bold/italic/color);watch 页选区上报体=
{"paths":[...]}。所有跟 watch 编辑 API 说话的面都过本模块——wire
格式只活在一处,officecli 升级只改这里+跑探针。
