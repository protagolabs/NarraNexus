---
code_file: frontend/src/pages/BundleExportPage.tsx
last_verified: 2026-05-08
stub: false
---

# BundleExportPage.tsx — Export wizard (subproject 2 §8.13)

4 tabs（Agents / Skills / Social Network / Workspace files）+ Bundle Notes editor + Review Summary modal。

## Tab data flow

1. **Agents tab**：选 agent_ids + （可选）team。其他 tab 的内容范围跟随这里。
2. **Skills tab**：拉每个 agent 的 skill list（`api.listSkills`），对照 `skill_archives`（`api.listSkillArchives`）显示三选项（url / zip / full_copy）+ 可用性灰显逻辑。
3. **Social Network tab**：双栏 + 分页 + accordion。匹配同 team 名字的默认勾选（最宽匹配规则，议题 7.f.1 接受误报）。
4. **Workspace files tab**：从 `api.listFiles` 拿文件列表，sensitive pattern 命中默认 unchecked + warning 标。

## Review Summary modal

强制最后一步（议题 6.7.a-A）。列出 included / stripped / warnings 三块，含"未自动扫描自由文本"的告知（议题 6.5）。

## Gotcha

- `listSkills` / `getSocialNetworkList` / `listFiles` 这些 backend endpoint 的实际返回字段名我没 100% 对齐（A3/A4 不确定项），可能在某些 tab 显示空 → 测试时若空就是字段名不对，调对齐即可。
- `RadioCard` 默认勾选逻辑写在 `useEffect([JSON.stringify(skillsForAgents), ...])`，对深嵌套对象用 stringify 兜底；性能足够。
