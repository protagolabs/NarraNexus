"""
@file_name: lark_trigger.py
@date: 2026-04-10
@description: Lark event trigger — listens for incoming messages via
lark-cli event +subscribe (WebSocket long connection, NDJSON output).

Architecture: 1 subscribe process per bound bot + N shared workers.
When a colleague sends a message to the bot, the trigger:
1. Parses the event
2. Builds context via LarkContextBuilder
3. Runs AgentRuntime
4. Writes result to Inbox
"""

from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Optional

from loguru import logger

from xyz_agent_context.schema.channel_tag import ChannelTag
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredential,
    LarkCredentialManager,
)
from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient
from xyz_agent_context.module.lark_module.lark_context_builder import LarkContextBuilder


class LarkTrigger:
    """
    Poll-free trigger using lark-cli event +subscribe per bot.

    Each active + logged_in credential gets its own subscribe process.
    Events are dispatched to a shared task queue processed by N workers.
    """

    def __init__(self, max_workers: int = 3):
        self.max_workers = max_workers
        self._subscribers: Dict[str, asyncio.subprocess.Process] = {}
        self._task_queue: asyncio.Queue = asyncio.Queue()
        self._workers: List[asyncio.Task] = []
        self._monitor_tasks: List[asyncio.Task] = []
        self.running = False
        self._cli = LarkCLIClient()
        self._bot_open_ids: set[str] = set()  # Cache of bot open_ids to filter echo

    async def start(self, db) -> None:
        """Start workers and credential watcher."""
        self.running = True
        self._db = db

        # Start workers
        for i in range(self.max_workers):
            worker = asyncio.create_task(self._worker(i))
            self._workers.append(worker)

        # Start credential watcher (checks for new/changed credentials periodically)
        watcher = asyncio.create_task(self._credential_watcher())
        self._monitor_tasks.append(watcher)

        logger.info(f"LarkTrigger started: {self.max_workers} workers, watching for credentials")

    async def _credential_watcher(self, poll_interval: int = 10) -> None:
        """
        Periodically check for new credentials and start/stop subscribers.
        This allows users to bind a bot without restarting the service.
        """
        active_apps: set[str] = set()  # app_ids with running subscribers

        while self.running:
            try:
                mgr = LarkCredentialManager(self._db)
                creds = await mgr.get_active_credentials()

                # Deduplicate by app_id
                seen_apps: dict[str, LarkCredential] = {}
                for cred in creds:
                    if cred.app_id not in seen_apps:
                        seen_apps[cred.app_id] = cred

                # Start subscribers for new app_ids
                for app_id, cred in seen_apps.items():
                    if app_id not in active_apps:
                        # Validate before starting
                        result = await self._cli.auth_status(cred.profile_name)
                        if result.get("success"):
                            task = asyncio.create_task(self._subscribe_loop(cred))
                            self._monitor_tasks.append(task)
                            active_apps.add(app_id)
                            logger.info(f"LarkTrigger: started subscriber for {cred.profile_name}")
                        else:
                            logger.warning(
                                f"LarkTrigger: skipping {cred.profile_name} — "
                                f"credential invalid: {result.get('error', 'unknown')}"
                            )
                            await mgr.update_auth_status(cred.agent_id, "expired")

            except Exception as e:
                logger.warning(f"LarkTrigger credential watcher error: {e}")

            await asyncio.sleep(poll_interval)

    async def _subscribe_loop(self, cred: LarkCredential) -> None:
        """
        Run event +subscribe for one bot. Restart on failure with backoff.
        """
        backoff = 5
        max_backoff = 120

        while self.running:
            try:
                proc = await self._cli.subscribe_events(cred.profile_name)
                self._subscribers[cred.agent_id] = proc

                async for line in proc.stdout:
                    if not self.running:
                        break
                    line_str = line.decode().strip()
                    if not line_str:
                        continue
                    try:
                        event = json.loads(line_str)
                        if self._is_message_event(event):
                            await self._task_queue.put((cred, event))
                    except json.JSONDecodeError:
                        logger.warning(f"LarkTrigger: invalid JSON line: {line_str[:200]}")

                # Process ended
                returncode = await proc.wait()
                logger.warning(
                    f"LarkTrigger subscribe ended for {cred.profile_name} "
                    f"(code={returncode}), restarting in {backoff}s"
                )
            except Exception as e:
                logger.error(f"LarkTrigger subscribe error for {cred.profile_name}: {e}")

            if not self.running:
                break

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    def _is_message_event(self, event: dict) -> bool:
        """Check if event is an incoming message."""
        event_type = event.get("type", event.get("header", {}).get("event_type", ""))
        return event_type in (
            "im.message.receive_v1",
            "im.message.receive",
        )

    async def _worker(self, worker_id: int) -> None:
        """Process events from the shared queue."""
        while self.running:
            try:
                cred, event = await asyncio.wait_for(
                    self._task_queue.get(), timeout=5.0
                )
            except asyncio.TimeoutError:
                continue

            try:
                await self._process_message(cred, event, worker_id)
            except Exception as e:
                logger.error(
                    f"LarkTrigger worker {worker_id} error: {e}",
                    exc_info=True,
                )

    async def _process_message(
        self, cred: LarkCredential, event: dict, worker_id: int
    ) -> None:
        """
        Process a single incoming message event:
        1. Extract message fields
        2. Build context prompt
        3. Run AgentRuntime
        4. Write to Inbox
        """
        # --compact format: flat JSON with top-level fields
        # e.g. {"chat_id":"oc_xxx","content":"hi","sender_id":"ou_xxx","type":"im.message.receive_v1"}
        #
        # Non-compact (raw) format: nested under event.message / event.sender
        # We support both.

        if "message" in event and isinstance(event["message"], dict):
            # Raw/nested format
            message = event.get("event", event).get("message", {})
            sender = event.get("event", event).get("sender", {})
            chat_id = message.get("chat_id", "")
            sender_id = sender.get("sender_id", {}).get("open_id", sender.get("open_id", ""))
            sender_name = sender.get("sender_id", {}).get("name", sender.get("name", "Unknown"))
            content_str = message.get("content", "{}")
            message_id = message.get("message_id", "")
        else:
            # Compact/flat format
            chat_id = event.get("chat_id", "")
            sender_id = event.get("sender_id", "")
            sender_name = event.get("sender_name", "Unknown")
            content_str = event.get("content", "")
            message_id = event.get("message_id", event.get("id", ""))

        # Skip messages sent by the bot itself (prevents echo loops)
        # Check sender_type if available (raw format)
        sender_type = event.get("sender_type", "")
        if sender_type in ("bot", "app"):
            return
        # Also check by bot open_id (compact format may not have sender_type)
        if not self._bot_open_ids:
            # Lazy-load bot open_id on first message
            try:
                bot_info = await self._cli._run(
                    ["api", "GET", "/open-apis/bot/v3/info"],
                    profile=cred.profile_name,
                )
                if bot_info.get("success"):
                    bot_oid = bot_info.get("data", {}).get("bot", {}).get("open_id", "")
                    if bot_oid:
                        self._bot_open_ids.add(bot_oid)
            except Exception:
                pass
        if sender_id in self._bot_open_ids:
            return

        # Parse message content (may be JSON-encoded or plain text)
        text = content_str
        if text.startswith("{"):
            try:
                content_obj = json.loads(text)
                text = content_obj.get("text", text)
            except (json.JSONDecodeError, TypeError):
                pass

        if not text or not text.strip():
            return

        # Resolve sender name if unknown (compact format only has sender_id)
        if sender_name == "Unknown" and sender_id:
            try:
                user_info = await self._cli.get_user(cred.profile_name, user_id=sender_id)
                if user_info.get("success"):
                    outer = user_info.get("data", {})
                    inner = outer.get("data", outer)
                    user_obj = inner.get("user", inner)
                    sender_name = (
                        user_obj.get("name")
                        or user_obj.get("en_name")
                        or user_obj.get("email", "").split("@")[0].replace(".", " ").title()
                        or "Unknown"
                    )
            except Exception:
                pass  # Keep "Unknown" if lookup fails

        logger.info(
            f"LarkTrigger [{cred.profile_name}] message from {sender_name} ({sender_id}): "
            f"{text[:100]}"
        )

        # Build normalized event dict for context builder
        normalized_event = {
            "chat_id": chat_id,
            "chat_type": event.get("chat_type", "p2p"),
            "chat_name": event.get("chat_name", ""),
            "sender_id": sender_id,
            "sender_name": sender_name,
            "content": text,
            "message_id": message_id,
            "create_time": event.get("create_time", ""),
        }

        # Build context
        builder = LarkContextBuilder(
            event=normalized_event,
            credential=cred,
            cli=self._cli,
            agent_id=cred.agent_id,
        )

        from xyz_agent_context.channel.channel_context_builder_base import ChannelHistoryConfig
        history_config = ChannelHistoryConfig(
            load_conversation_history=True,
            history_limit=20,
            history_max_chars=3000,
        )
        prompt = await builder.build_prompt(history_config)

        # Create ChannelTag
        channel_tag = ChannelTag.lark(
            sender_name=sender_name,
            sender_id=sender_id,
            chat_id=chat_id,
            chat_name=normalized_event.get("chat_name", ""),
        )

        tagged_prompt = f"{channel_tag.format()}\n{prompt}"

        # Run AgentRuntime
        from xyz_agent_context.agent_runtime.agent_runtime import AgentRuntime
        from xyz_agent_context.agent_runtime.logging_service import LoggingService

        runtime = AgentRuntime(logging_service=LoggingService(enabled=False))
        final_output = []
        lark_replies = []  # Track what Agent actually sent via lark_send_message

        async for response in runtime.run(
            agent_id=cred.agent_id,
            user_id=sender_id,
            input_content=tagged_prompt,
            working_source=WorkingSource.LARK,
            trigger_extra_data={"channel_tag": channel_tag.to_dict()},
        ):
            from xyz_agent_context.schema.runtime_message import MessageType
            if response.message_type == MessageType.AGENT_RESPONSE:
                final_output.append(response.delta)
            # Capture tool output from lark_send_message to confirm it was called
            # Log response attributes once for debugging
            if not final_output and not lark_replies:
                logger.debug(f"LarkTrigger response attrs: {[a for a in dir(response) if not a.startswith('_')]}")
            # The raw response stream includes tool_call items with arguments
            if hasattr(response, "raw") and response.raw:
                raw = response.raw
                if isinstance(raw, dict):
                    item = raw.get("item", {})
                    # tool_call_item contains the arguments
                    if item.get("type") == "tool_call_item" and "lark_send_message" in item.get("tool_name", ""):
                        args = item.get("arguments", {})
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                args = {}
                        sent_text = args.get("text", "") or args.get("markdown", "")
                        if sent_text:
                            lark_replies.append(sent_text)

        if lark_replies:
            output_text = "\n".join(lark_replies)
        elif "".join(final_output).strip():
            output_text = "(Replied on Lark)"
        else:
            output_text = ""
        logger.info(
            f"LarkTrigger [{cred.profile_name}] agent responded: "
            f"{output_text[:200]}"
        )

        # Write to Inbox so the agent owner sees the notification
        await self._write_to_inbox(
            cred=cred,
            sender_name=sender_name,
            sender_id=sender_id,
            original_message=text,
            agent_response=output_text,
            chat_id=chat_id,
        )

    async def _write_to_inbox(
        self,
        cred: LarkCredential,
        sender_name: str,
        sender_id: str,
        original_message: str,
        agent_response: str,
        chat_id: str,
    ) -> None:
        """
        Write Lark messages to MessageBus tables so they appear in the
        frontend Inbox (which reads from bus_channels / bus_messages).
        """
        try:
            from xyz_agent_context.utils.db_factory import get_db_client
            from xyz_agent_context.utils.timezone import utc_now
            import uuid

            db = await get_db_client()
            now = utc_now()
            brand_display = "Lark" if cred.brand == "lark" else "Feishu"

            # Resolve name if still Unknown
            if sender_name == "Unknown" and sender_id:
                try:
                    user_info = await self._cli.get_user(cred.profile_name, user_id=sender_id)
                    if user_info.get("success"):
                        # CLI returns {"success":true,"data":{"ok":true,"data":{"user":{...}}}}
                        outer = user_info.get("data", {})
                        inner = outer.get("data", outer)
                        user_obj = inner.get("user", inner)
                        sender_name = (
                            user_obj.get("name")
                            or user_obj.get("en_name")
                            or user_obj.get("email", "").split("@")[0].replace(".", " ").title()
                            or "Unknown"
                        )
                except Exception:
                    pass

            # Use chat_id as channel_id (one Lark chat = one inbox channel)
            channel_id = f"lark_{chat_id}"
            display_name = sender_name if sender_name != "Unknown" else sender_id
            channel_name = f"{brand_display}: {display_name}"

            # Register Lark user as a pseudo-agent so Inbox can resolve the name
            lark_agent_id = f"lark_user_{sender_id}"
            existing_agent = await db.get_one("bus_agent_registry", {"agent_id": lark_agent_id})
            if not existing_agent:
                await db.insert("bus_agent_registry", {
                    "agent_id": lark_agent_id,
                    "owner_user_id": "",
                    "capabilities": f"{brand_display} user",
                    "description": display_name,
                    "visibility": "public",
                    "registered_at": now,
                })
            elif sender_name != "Unknown" and existing_agent.get("description") != sender_name:
                await db.update("bus_agent_registry",
                    {"agent_id": lark_agent_id},
                    {"description": sender_name})

            # Ensure channel exists
            existing_channel = await db.get_one("bus_channels", {"channel_id": channel_id})
            if not existing_channel:
                await db.insert("bus_channels", {
                    "channel_id": channel_id,
                    "name": channel_name,
                    "channel_type": "direct",
                    "created_by": cred.agent_id,
                    "created_at": now,
                })

            # Ensure agent is a member of this channel
            existing_member = await db.get_one("bus_channel_members", {
                "channel_id": channel_id,
                "agent_id": cred.agent_id,
            })
            if not existing_member:
                await db.insert("bus_channel_members", {
                    "channel_id": channel_id,
                    "agent_id": cred.agent_id,
                    "joined_at": now,
                })

            # Write the incoming message
            await db.insert("bus_messages", {
                "message_id": f"lark_in_{uuid.uuid4().hex[:12]}",
                "channel_id": channel_id,
                "from_agent": lark_agent_id,
                "content": original_message,
                "msg_type": "text",
                "created_at": now,
            })

            # Write the agent's response summary (skip thinking, just note it replied)
            if agent_response and agent_response.strip():
                # agent_response may contain thinking process; use a clean summary
                summary = "(Replied on Lark)"
                await db.insert("bus_messages", {
                    "message_id": f"lark_out_{uuid.uuid4().hex[:12]}",
                    "channel_id": channel_id,
                    "from_agent": cred.agent_id,
                    "content": summary,
                    "msg_type": "text",
                    "created_at": now,
                })

            logger.info(f"Wrote Lark messages to inbox channel {channel_id}")
        except Exception as e:
            logger.warning(f"Failed to write to inbox: {e}")

    async def stop(self) -> None:
        """Gracefully stop all subscribers and workers."""
        self.running = False

        # Terminate subscribe processes
        for agent_id, proc in self._subscribers.items():
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                proc.kill()
            logger.info(f"LarkTrigger stopped subscriber for {agent_id}")

        self._subscribers.clear()

        # Cancel workers and monitors
        for task in self._workers + self._monitor_tasks:
            task.cancel()

        self._workers.clear()
        self._monitor_tasks.clear()
        logger.info("LarkTrigger stopped")
