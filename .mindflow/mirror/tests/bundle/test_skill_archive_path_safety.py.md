---
code_file: tests/bundle/test_skill_archive_path_safety.py
last_verified: 2026-08-18
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
2. **不许再手拼**：一条 grep 式断言扫 bundle 路由 + 整个 `bundle/` 包，
   禁止出现"看起来在拼路径、且同行提到 `skill_name`"的行（`/`、
   `.joinpath(`、`os.path.join(`）。**实参位置的拼接也算**——
   `copy2(src, base / f"{skill_name}.zip")` 会被抓到，不只是赋值语句。
   豁免只有两类、按形状不按行号：sanctioned builder（`archive_target` /
   `prepare_archive_target`）和日志/标签行（`logger.` 开头，或 f-string 喂
   给 `append(`，`f"{skill_name}@{old_aid}"` 就在这一类）。
   演化史（两次都是"守卫比它自称的窄"）：v1 只匹配 `f"{skill_name}`，漏
   `.joinpath` / `os.path.join`，且扫描面写死 3 个文件；v2 放宽了这些但**新
   加了一道"必须赋给 tgt/out/target/…"的过滤**，反而对实参位置和无空格
   `base/f"…"` 比 v1 更窄，而 docstring 没写这道过滤（mirror 写了，两边打
   架）。v3 去掉赋值要求、改成排除已知误报，docstring 与本文件同步。
   验过：往 importer 注入一行实参位置的拼接，v3 会红。
3. **读侧，三种形状**（初版只有第一种，是个真空档）：
   - root **之外**的绝对路径 —— `/export` 那条读侧漏洞的形状
   - root **散装层**（`{root}/marker.zip`）—— **dev 库 id=20 的真实形状**，
     因为存的字符串 `{root}/{uid}/../marker.zip` resolve 之后就在 root 里
   - **别人的用户目录**（`{root}/other_user/x.zip`）—— 跨用户读

   后两条在 root 锚点的判据下会放行，初版恰好只测了第一条，所以"读侧兜住
   id=20"这句话既没被代码实现、也没被测试发现。三条都断言 canary 不进包 +
   warning 提到该 skill + manifest 无 `archive_ref`；另有正向用例保证守卫
   没把正常 zip 导出打死。
4. **导入侧归档登记**：`test_imported_zip_skill_registers_archive` 走完整
   export → 删行 → preflight → confirm，断言 `skill_archives` 行被重新写
   出来。钉的是 `SameFileError` 那个既有缺陷（见 [[importer.py]]）。

## Gotcha

- `archives_root` fixture monkeypatch 的是
  `skill_backup.SKILL_ARCHIVES_ROOT` 模块级常量（`_user_archive_dir` 每次
  调用时才读它），不是 `Path.home()`。真实 `~/.nexusagent` 不会被碰到。
- 读侧用例复用 [[test_skill_import.py]] 那套 `db_client` /
  `tmp_workspace_root` fixture 组合（隔离 sqlite + 覆盖
  `base_working_path` 和 HOME）。
- 三条读侧用例都验过牙口：把 builder 的判断换回
  `is_within_archives_root`，散装层和跨用户两条**会红**、root 外那条仍绿
  （这正是初版漏掉差异的原因）；换成 `if False:` 三条全红。导入侧那条把
  `register_archive` 换回 `backup_after_api_install` 也会红。加新守卫时用
  同样的方式验。
- 形状断言的覆盖面写在它自己的 docstring 里，**故意写成"能挡什么、挡不住
  什么"**：改名变量、跨行拼接它都看不见。它是防复发的窄网，不是不存在
  的证明——别因为它绿就跳过人工核。
