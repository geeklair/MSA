"""
msa/scheduler.py — Trigger-based scheduler for the MSA.

Supports:
  - interval    — run the agent on a fixed time interval
  - file_watch  — watch for a trigger file at /tmp/msa_trigger
  - slack       — Socket Mode listener for DMs and @mentions
"""

import logging
import os
import time
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agent):
        self.agent = agent
        scheduler_config = agent.config.get("scheduler", {})
        if isinstance(scheduler_config, str):
            self.config = {"mode": scheduler_config}
        else:
            self.config = scheduler_config or {}
        self.mode = self.config.get("mode", "interval")
        self.interval = self.config.get("interval_seconds", 300)  # default 5 min

    def run(self):
        logger.info("Scheduler starting in mode: %s", self.mode)
        if self.mode == "interval":
            self._run_interval()
        elif self.mode == "slack":
            self._run_slack_socket_mode()
        elif self.mode == "file_watch":
            self._run_file_watch()
        else:
            raise ValueError(f"Unknown scheduler mode: {self.mode}")

    def _run_interval(self):
        """Run agent on a fixed interval."""
        logger.info("Interval scheduler: every %d seconds", self.interval)
        while True:
            logger.info("Scheduler firing at %s", datetime.now().isoformat())
            try:
                self.agent.run_once()
            except Exception as e:
                logger.error("Agent run failed: %s", e)
            logger.info("Sleeping %d seconds...", self.interval)
            time.sleep(self.interval)

    def _run_file_watch(self):
        """
        Watch for a trigger file. When it appears, run the agent and delete it.
        Useful for testing without a real event source.

        Create trigger with: touch /tmp/msa_trigger
        """
        trigger_path = self.config.get("trigger_file", "/tmp/msa_trigger")
        logger.info("Watching for trigger file: %s", trigger_path)
        while True:
            if os.path.exists(trigger_path):
                logger.info("Trigger file detected!")
                os.remove(trigger_path)
                try:
                    self.agent.run_once()
                except Exception as e:
                    logger.error("Agent run failed: %s", e)
            time.sleep(2)

    def _run_slack_socket_mode(self):
        """
        Slack Socket Mode listener — no open port required.

        Listens for direct messages and @mentions via a persistent WebSocket
        connection to Slack. When a qualifying message arrives the agent runs
        one full cycle, then replies in-thread with the scratchpad notes.

        Required environment variables:
            SLACK_BOT_TOKEN  — bot/user OAuth token  (xoxb-…)
            SLACK_APP_TOKEN  — app-level token for Socket Mode (xapp-…)

        Slack app setup:
            • Enable Socket Mode in your app's settings.
            • Subscribe to the `message.im` and `app_mention` bot events.
            • Generate an App-Level Token with the `connections:write` scope.
        """
        try:
            from slack_sdk import WebClient
            from slack_sdk.socket_mode import SocketModeClient
            from slack_sdk.socket_mode.response import SocketModeResponse
        except ImportError:
            logger.error("slack-sdk required for Socket Mode: pip install slack-sdk")
            raise

        bot_token = os.environ.get("SLACK_BOT_TOKEN", "")
        app_token = os.environ.get("SLACK_APP_TOKEN", "")

        if not bot_token:
            raise RuntimeError(
                "SLACK_BOT_TOKEN environment variable is not set. "
                "Set it to your bot OAuth token (xoxb-…)."
            )
        if not app_token:
            raise RuntimeError(
                "SLACK_APP_TOKEN environment variable is not set. "
                "Set it to an app-level token with the connections:write scope (xapp-…)."
            )

        web_client = WebClient(token=bot_token)
        socket_client = SocketModeClient(app_token=app_token, web_client=web_client)

        # Prevent concurrent agent cycles triggered by rapid Slack messages.
        cycle_lock = threading.Lock()

        def handle_event(client: SocketModeClient, req):
            if req.type != "events_api":
                return

            # Acknowledge immediately — Slack requires a response within 3 s.
            client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))

            event = req.payload.get("event", {})
            event_type = event.get("type", "")

            # Only act on direct messages and channel @mentions.
            if event_type not in ("message", "app_mention"):
                return

            # Skip bot messages and message edits/deletes to avoid loops.
            if event.get("bot_id") or event.get("subtype"):
                return

            channel = event.get("channel")
            thread_ts = event.get("thread_ts") or event.get("ts")

            logger.info("Slack event received: type=%s channel=%s", event_type, channel)

            if not cycle_lock.acquire(blocking=False):
                logger.info("Agent cycle already in progress — skipping duplicate trigger.")
                web_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="A cycle is already running. Please wait for it to finish.",
                )
                return

            try:
                self.agent.run_once()
            except Exception as e:
                logger.error("Agent cycle failed: %s", e)
                web_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"Agent cycle failed: {e}",
                )
                return
            finally:
                cycle_lock.release()

            # Reply with the scratchpad notes produced during the cycle.
            state = self.agent.scratchpad.load()
            notes = state.get("notes") or "(no notes)"
            web_client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=f"Cycle complete.\n\n*Notes:*\n{notes}",
            )

        socket_client.socket_mode_request_listeners.append(handle_event)

        logger.info("Slack Socket Mode listener connecting...")
        socket_client.connect()
        logger.info("Slack Socket Mode listener connected.")

        # Block the main thread; the socket client runs its own threads.
        threading.Event().wait()
