"""
msa/scheduler.py — Trigger-based scheduler for the MSA.

The scheduler's only job is to decide *when* to call agent.run_once().
All three modes share that single call site and nothing else.

Modes
-----
interval    Fixed sleep loop. Fires once on startup, then every interval_seconds.
            No drift correction: if a cycle takes T seconds, the next fires
            at T + interval_seconds, not at interval_seconds.

file_watch  Polls for tmp/msa_trigger every 2 seconds. When found, deletes
            the file and fires one cycle. Use `touch tmp/msa_trigger` to trigger.
            Useful for manual testing without restarting the process.

slack       Slack Socket Mode via slack_bolt. No inbound port required.
            SocketModeHandler opens an outbound WebSocket to Slack's servers.
            The bot fires a cycle when it receives a DM or @mention.

Slack implementation notes
---------------------------
ack() is called as the first action in every handler, before the agent runs.
Slack retries events that are not acknowledged within 3 seconds; calling ack()
immediately satisfies that requirement regardless of cycle duration.

The agent cycle runs in a background daemon thread after ack() so the handler
can return promptly. A threading.Lock prevents concurrent cycles when messages
arrive faster than one cycle completes.

seen_event_ids provides explicit deduplication on top of slack_bolt's built-in
dedup to guard against edge cases in multi-handler setups.

The bot's own user_id is fetched at startup and filtered out to prevent the
bot from triggering itself when it posts cycle results to the channel.

Required env vars for Slack mode:
  SLACK_BOT_TOKEN  — bot OAuth token (xoxb-…)
  SLACK_APP_TOKEN  — app-level token with connections:write scope (xapp-…)
"""

import logging
import os
import time
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class Scheduler:
    """
    Wraps an Agent and calls run_once() based on the configured trigger mode.

    Accepts the scheduler config as either a plain string ("interval") or a
    dict ({"mode": "interval", "interval_seconds": 300}). Both forms are
    normalized to a dict in __init__.
    """

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

        Create trigger with: touch tmp/msa_trigger  (relative to project root)
        """
        _project_root = Path(__file__).parent.parent.resolve()
        default_trigger = str(_project_root / "tmp" / "msa_trigger")
        trigger_path = self.config.get("trigger_file", default_trigger)
        Path(trigger_path).parent.mkdir(parents=True, exist_ok=True)
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

        Uses slack_bolt's SocketModeHandler so that ack() is an explicit
        argument in every handler and is called as the very first action,
        guaranteeing Slack receives the acknowledgment well within its 3 s
        window regardless of how long the agent cycle takes.

        Required environment variables:
            SLACK_BOT_TOKEN  — bot OAuth token  (xoxb-…)
            SLACK_APP_TOKEN  — app-level token for Socket Mode (xapp-…)

        Slack app setup:
            • Enable Socket Mode in your app's settings.
            • Subscribe to the `message.im` and `app_mention` bot events.
            • Generate an App-Level Token with the `connections:write` scope.
        """
        try:
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler
        except ImportError:
            logger.error("slack-bolt required for Socket Mode: pip install slack-bolt")
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

        app = App(token=bot_token)

        # Resolve the bot's own user ID once to filter self-triggered events.
        bot_user_id = app.client.auth_test()["user_id"]
        logger.info("Slack bot user ID: %s", bot_user_id)

        # Prevent concurrent agent cycles triggered by rapid messages.
        cycle_lock = threading.Lock()
        # Belt-and-suspenders dedup on top of Bolt's built-in deduplication.
        seen_event_ids = set()
        seen_event_ids_lock = threading.Lock()

        def _dispatch(ack, event, body, client):
            # ack() MUST be the first call — Slack retries if it doesn't arrive
            # within 3 seconds.  Everything else, including the agent, runs after.
            ack()

            # Deduplicate retries by event_id (Bolt also does this internally,
            # but an explicit set guards against edge cases).
            event_id = body.get("event_id")
            if event_id:
                with seen_event_ids_lock:
                    if event_id in seen_event_ids:
                        logger.info("Duplicate event_id %s — skipping.", event_id)
                        return
                    seen_event_ids.add(event_id)

            # Ignore the bot's own messages to prevent reply loops.
            if event.get("bot_id") or event.get("user") == bot_user_id:
                return

            # Skip edits, deletions, and other message subtypes.
            if event.get("subtype"):
                return

            channel = event.get("channel")
            thread_ts = event.get("thread_ts") or event.get("ts")

            logger.info("Slack event received: type=%s channel=%s event_id=%s",
                        event.get("type"), channel, event_id)

            if not cycle_lock.acquire(blocking=False):
                logger.info("Agent cycle already in progress — skipping.")
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="A cycle is already running. Please wait for it to finish.",
                )
                return

            def run_cycle():
                try:
                    self.agent.run_once()
                except Exception as e:
                    logger.error("Agent cycle failed: %s", e)
                    client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"Agent cycle failed: {e}",
                    )
                    return
                finally:
                    cycle_lock.release()

                state = self.agent.scratchpad.load()
                notes = state.get("notes") or "(no notes)"
                client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"Cycle complete.\n\n*Notes:*\n{notes}",
                )

            threading.Thread(target=run_cycle, daemon=True).start()

        @app.event("message")
        def handle_message(ack, event, body, client):
            _dispatch(ack, event, body, client)

        @app.event("app_mention")
        def handle_mention(ack, event, body, client):
            _dispatch(ack, event, body, client)

        logger.info("Slack Socket Mode listener starting...")
        SocketModeHandler(app, app_token).start()
