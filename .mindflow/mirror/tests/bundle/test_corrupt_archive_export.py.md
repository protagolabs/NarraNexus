---
code_file: tests/bundle/test_corrupt_archive_export.py
last_verified: 2026-08-18
stub: false
---

# test_corrupt_archive_export.py — 坏归档不该拖垮整份导出

## 为什么存在

上传侧的 zip 校验（见 [[bundle.py]]）挡的是**新**的坏包；这个文件钉的是另
一半：**已经在库里的坏行**。它们挡不住，因为来源不止一种——早于校验写入
的、写了一半被截断的、磁盘上损坏的。

分成两条，是因为它们证明的东西不同：

1. `test_corrupt_archive_is_skipped_with_a_warning_not_a_500` —— 导出仍然产
   出、warning **点名了是哪个 skill**、坏字节没进包。
2. `test_one_corrupt_archive_does_not_sink_the_healthy_ones` —— 真正的代价所
   在：修复前一条坏行会让**整份**导出失败，同一个 agent 上健康的 skill 跟着
   陪葬。只测第一条的话，"坏的被跳过"和"全都失败"都能满足。

## Gotcha

- 跳过的 skill 是**整条从 manifest 消失**，不是"出现但没有 `archive_ref`"。
  这是照抄 builder 里 `zip not found, skipping` 的既有约定，让导入侧只需要
  认一种形状。初版断言写成 `all(not e.get('archive_ref') for e in entries)`，
  entries 为空时**恒真**——是个会因为错误的理由变绿的断言，已改成显式断言
  该 skill 不在 manifest 里，并补查包内不含它的文件名。
- 两条用例都验过牙口：把 builder 的 `except (BadZipFile, OSError)` 换成别的
  异常类型，两条都会红。
- `archives_root` fixture monkeypatch `skill_backup.SKILL_ARCHIVES_ROOT`；坏
  归档要用 `prepare_archive_target` 落到用户目录里，否则会先被 SEC-07 的读侧
  守卫 `is_within_user_archive_dir` 拦掉，测到的就不是这个 bug 了。
