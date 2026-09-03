---
code_file: tests/message_bus/test_team_reply_discipline_prompt.py
last_verified: 2026-09-03
stub: false
---

# test_team_reply_discipline_prompt — 团队房 prompt 的「回有实质要求的、不复述、不礼貌 @」措辞锁

2026-09-03 新建。锁 `_build_team_prompt` 四处改动(批量 @ 头句、默认应答者句、沉默无痕、
写作规则新两条),并断言旧句「Address ALL of them」「whoever on the roster is the better
owner」不再出现。prompt 测试是弱测试(铁律 #15),真守卫在 `test_errand_auto_board.py`
和 `test_undelivered_turn.py`。
