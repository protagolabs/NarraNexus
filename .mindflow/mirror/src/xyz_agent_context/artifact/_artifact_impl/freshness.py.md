---
code_file: src/xyz_agent_context/artifact/_artifact_impl/freshness.py
last_verified: 2026-08-19
stub: false
---

# freshness.py — 外部编辑侦测(5①)

## 为什么存在

总纲「入口辖表不辖文件」的拉模型另一半:文件世界随时可被圈外写
(用户的 Word、备份还原、别的工具),本模块在**消费点**察觉并把既成
事实变成提交点。两段检测:mtime 快筛(零 IO,对 updated_at)→ sha256
复核(对 content_hash)——**mtime 动了但字节没变=touch/备份噪音,
绝不进事件层**(提交点契约:事件频率由真实变化而非文件系统噪声决定)。

确认后与任何编辑同构提交:hash/updated_at 刷新 + history
`external_edited` + stage updated 事件(external=True)。

## 消费点(触发布点)

T-A=状态块渲染(common_tools_module,每轮开工前,主力);T-C=office
watch version 轮询(带 ~$ lock 旗标)。**没有 watcher**——3′/3″/3‴
方案族被 Owner 裁定只记录;懒检查就是本版本的全部实时性。

## office_lock_present

`~$<name>` 存在=桌面 Office 正持有文档;watch 版本端点带出 `lock`,
前端 T1 浮条据此置灰(officecli 写入会与桌面应用互踩)。

## 坑

- 检测**永不移指针**(file_path 原样传回 update_pointer)。
- entry 消失返回 "missing" 且零提交——那是 heal 的领土,别在这里
  标 deleted。
