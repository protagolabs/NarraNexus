---
code_file: frontend/src/lib/artifactEditing/mdEditSafety.ts
last_verified: 2026-08-20
stub: false
---

# mdEditSafety.ts — md 块编辑器的防丢守卫

## 为什么存在

2026-08-19 spike 实证:字节级往返在块编辑器世界不存在(remark 系
serializer 必然规范化列表符号/表格分隔线/强调标记),而 frontmatter
会被**摧毁**(解析成分割线+标题,真数据丢失)。守卫因此是语义的:

- `extractFrontmatter`:不解析 YAML,纯字面切分(首行 `---`、无空行、
  `---`/`...` 收口),保证 frontmatter+body 逐字节重组回原文。
- `mdAstEqual`:remark-parse+gfm 双方解析,剥 position 后比树——
  结构/文字变化=不等价(停用编辑);纯风格差=等价(放行)。

## 实测边界(2026-08-19 spike,Crepe)

无损:html 块、html 注释、GFM 脚注、数学 $..$/$$..$$、:::directive、
setext 标题(风格级)。**有损:reference 式链接**(linkReference+
definition 被解成内联 link)——守卫测试用它当样本。守卫是探针不是
黑名单:Crepe 升级后边界自动跟随。

## 坑

parser 崩溃 → 返回不等价(不能担保就不放行),别改成放行。

## 2026-08-20 — extractFrontmatter 行尾感知(#334 I7)

按文档自己的 eol(含 \r\n)切围栏;切分结果**保原始字节**(frontmatter
+body 可逐字节重组)——保存时的 LF 归一政策在 MarkdownRenderer,不在
这里。空行在闭合围栏前 → 仍判无 frontmatter(CommonMark 语义)。
