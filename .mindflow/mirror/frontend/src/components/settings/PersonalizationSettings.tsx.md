---
code_file: frontend/src/components/settings/PersonalizationSettings.tsx
last_verified: 2026-08-19
stub: false
---

# PersonalizationSettings — 设置页「个性化」pane

主题(system/light/dark 三档 radio,走 [[useTheme]].setTheme)+ 语言
(SUPPORTED_LANGUAGES 列表,i18n.changeLanguage)。这两个控件**只**活在
这里——它们从侧栏账户弹层迁入,弹层从此只做身份(见 [[../layout/Sidebar]]
08-19 条)。标签复用 `sidebar.theme*`/`sidebar.language` 既有 key;栏目自身
的 key 是 `pages.settings.nav.personalization` / `pages.settings.personalization.*`。
将来新增个性化项(密度、字号…)加在此 pane,别再往弹层塞。
