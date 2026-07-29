---
code_file: src/xyz_agent_context/agent_framework/nexus_loop/assembly.py
last_verified: 2026-07-29
stub: false
---
# assembly — 唯一装配点:TurnRequest 进、类型化事件流出

LoopAssembly 是循环的全部依赖(硬组件无默认、策略缝带默认,R1:装配复杂度有意集中于此文件);run_turn_events 是框架顶层入口:装配→初始展开→跑循环。TurnRequest 整包可 JSON 序列化,这是 runner 跨进程传输的前提。测试用 dataclasses.replace 换件,永不 patch。坑:harness system 消息插在平台前导 system 段末尾(_insert_harness),不能追加在 user 之后;output_schema v1 显式 fail loud(schema 诚实)。
