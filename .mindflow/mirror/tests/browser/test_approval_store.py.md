---
code_file: tests/browser/test_approval_store.py
last_verified: 2026-09-23
stub: false
---

# 能力审批跨进程存储

两个存储实例共享数据库即可看到独立下载或上传请求；验证去重、原子消费、ID 对外映射、
数据库故障和已禁用能力不能创建请求。HTTP(S) 访问不使用此表发起询问。
