---
code_file: frontend/src/lib/replyLanguageSync.ts
last_verified: 2026-08-12
stub: false
---
# replyLanguageSync — 回复语言同步(PR #284 review #2)

UI 语言多数是**被检测**的(localStorage nx_lang / navigator),不是被点出来的——只挂在 LanguageToggle 上的写透覆盖不到存量用户。本模块两件事:① 订阅 i18n `languageChanged`,任何改语言路径都 PUT 落库;② 认证后 boot 一次对账:服务端 null + 检测语言受支持 → 一次性回填 PUT。全程 fire-and-forget,失败静默(下次切换自然重写)。幂等 init 守卫防重复订阅。调用方:[[MainLayout.tsx]]。
