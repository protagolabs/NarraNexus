---
code_file: src/xyz_agent_context/artifact/_artifact_impl/commit.py
last_verified: 2026-08-20
stub: false
---

# commit.py — 内容变更提交尾巴的唯一 owner

## 为什么存在

pr-self-review 自查(2026-08-20)揪出的手抄漂移:user_edit 的两条路径与
freshness 检测各自复制了同一段「update_pointer→history→事件」尾巴,
三份已在 size 语义上出现措辞分叉。收敛为 `commit_content_refresh`——
调用方只提供 new_hash 与 history_action(user_edited/external_edited),
size 规则(顶层单文件=文件本身,专属目录=整目录)与事件形状只活在
这里。**指针永不因内容提交移动**(file_path 原样写回)。

## 上下游

调用方:[[user_edit.py]](save_user_content / commit_office_user_edit)、
[[freshness.py]](refresh_external_state,external=True)。注册/重注册
不走这里——那是 [[registration.py]] 的领土(它还管 kind 校验与路径
确权,尾巴形状不同)。
