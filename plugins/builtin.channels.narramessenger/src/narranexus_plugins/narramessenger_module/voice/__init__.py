"""
@file_name: __init__.py
@date: 2026-11-19
@description: NarraMessenger voice-call subsystem (RTC voice contract).

Package marker only — nothing is re-exported; import the module you need:

- ``_rtc_voice``      voice-input metadata, correlation ids, the reply binding
- ``_voice_delivery`` the live reply lifecycle (one visible event + edits)
- ``_voice_call``     contract v15 control plane, pure logic
- ``_voice_control``  contract v15 control plane, I/O (mixed into MatrixTrigger)
"""
