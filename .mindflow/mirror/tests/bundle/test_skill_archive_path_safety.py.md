---
code_file: tests/bundle/test_skill_archive_path_safety.py
last_verified: 2026-08-17
stub: false
---

# test_skill_archive_path_safety.py — SEC-07 归档路径的写侧 + 读侧

## 为什么存在

SEC-07 是一个被 QA 实证的路径穿越：`skill_archives/{user_id}/{skill_name}.*`
的拼接散在 7 处，`skill_name` 全部来自进程外部。修复方式是收敛成
[[skill_backup.py]] 的 `archive_target()` 单一构造点，所以测试也钉在这个
构造点上，而不是逐个 caller 重复一遍参数化。

## 覆盖的三件事

1. **写侧构造点**：10 个穿越 payload（含 QA 用的
   `../qa-sec07-oneup-marker` 原件）、3 种 suffix 的正常路径、穿越型
   `user_id`、symlink 出逃。symlink 那条专门盯
   `ensure_within_directory` 锚在用户目录上的盲区。
2. **不许再手拼**：一条 grep 式断言扫 route / skill_backup / importer 三个
   文件，禁止出现 `<dir> / f"...skill_name..."` 形状的行。这类 bug 的复发
   路径就是有人又手拼一次；断言写成"形状"而不是"某一行"才拦得住新增。
   判据取 `"/ f" 或 "/f"` 且同行含 `skill_name` —— 早期版本只匹配
   `f"{skill_name}` 会把 `f"{skill_name}@{old_aid}"` 这种纯日志标签误报。
3. **读侧**：seed 一条 `archive_path` 指向 archives root 之外的
   `skill_archives` 行（就是 dev 库 id=20 的形状），跑 `build_bundle`，断言
   canary 字节**没有**出现在导出包里、warning 提到该 skill、manifest 里没
   有 `archive_ref`。配一条正向用例保证守卫没把正常 zip 导出打死。

## Gotcha

- `archives_root` fixture monkeypatch 的是
  `skill_backup.SKILL_ARCHIVES_ROOT` 模块级常量（`_user_archive_dir` 每次
  调用时才读它），不是 `Path.home()`。真实 `~/.nexusagent` 不会被碰到。
- 读侧两条用例复用 [[test_skill_import.py]] 那套 `db_client` /
  `tmp_workspace_root` fixture 组合（隔离 sqlite + 覆盖
  `base_working_path` 和 HOME）。
- 这两条读侧用例是有牙的：把 builder 里的 `is_within_archives_root` 判断
  改成 `if False:` 会让 `test_poisoned_archive_path_row_is_not_packed` 失
  败——加新守卫时可以用同样的方式验证。
