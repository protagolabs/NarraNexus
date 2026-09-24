---
code_file: frontend/src/components/artifacts/renderers/BrowserApprovalPrompt.tsx
last_verified: 2026-09-23
stub: false
---

## 2026-09-22 - Explicit lifetimes and recoverable decisions

BrowserApprovalNotice in the app shell is the upstream caller. The prompt uses
NM buttons, exposes the complete origin and requested capability as a named
group, disables every decision while one is pending, and announces pending and
error states. A failed decision is caught here and leaves the same choices
available for retry, without an unhandled promise rejection.

Turn and conversation grants appear only when the backend's `allowed_lifetimes`
includes them. The handler also checks that list before submission. If the
optional field is absent, only permanent policy decisions are offered; context
identifiers must never be inferred. "Always allow" remains the quietest choice.
All buttons, including "Allow for this turn", use `type="button"` and no choice
is preselected. The origin wraps as a text element inside the flex row so a
long hostname stays visible on narrow screens.

# BrowserApprovalPrompt.tsx — 独立特权能力的明确答复

## 为什么存在 / 关键决定

只用于下载、上传、自动审核等独立能力，不再支持打开网址的访问审批。
普通网页无需许可，旧请求由后端退役；此处没有 access 能力或对应文案。

每一处取舍都在防「训练用户闭眼点同意」：

- **确切 origin 作标题、完整显示、不截断**。截断能藏住真实主机名
  （`accounts.google.com.evil.example` 被截成 `accounts.google.com…` 就是一次成功的钓鱼）。
- **点名是哪种能力**。下载、上传和自动审核使用不同动词，不能含糊称为打开网站。
- **没有默认按钮、Enter 按不动**。三个按钮都显式写了 `type="button"`——HTML 的 button
  默认是 `submit`，在表单里会被回车触发，于是一个用户根本没读的弹窗可能被一次误触回答掉。
  这条是测试先抓到的。
- **「始终允许」是三个里视觉上最轻的那个**。最容易点到的应该是最窄的授权，不是最宽的。
- **弹在画布上方而不是模态遮罩**。问题是关于用户正在看的那个页面的，把页面遮住
  等于抽掉了做出知情判断所需要的上下文。

## 上下游

- Upstream: BrowserApprovalNotice polls `/api/browser/approvals/{agent_id}` in the app shell.
- 下游：`POST /api/browser/approvals/{id}` → `ApprovalRegistry.resolve`
- 设计：`reference/self_notebook/specs/2026-09-21-in-app-browser-design.md` §6
