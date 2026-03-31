"""
msa/scheduler.py — Trigger-based scheduler for the MSA.

Supports:
  - Cron-style interval scheduling
  - Slack event trigger (webhook listener)
  - Manual trigger via file watch
"""

import logging
import time
import threading
from datetime import datetime

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, agent):
        self.agent = agent
        self.config = agent.config.get("scheduler", {})
        self.mode = self.config.get("mode", "interval")
        self.interval = self.config.get("interval_seconds", 300)  # default 5 min

    def run(self):
        logger.info("Scheduler starting in mode: %s", self.mode)
        if self.mode == "interval":
            self._run_interval()
        elif self.mode == "slack":
            self._run_slack_listener()
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
        import os
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

    def _run_slack_listener(self):
        """
        Simple Slack webhook listener.
        Listens for incoming Slack slash commands or event callbacks.
        
        Requires SLACK_SIGNING_SECRET in environment.
        """
        try:
            from flask import Flask, request, jsonify
        except ImportError:
            logger.error("Flask required for Slack listener: pip install flask")
            raise

        app = Flask(__name__)
        port = self.config.get("slack_port", 3000)

        @app.route("/slack/trigger", methods=["POST"])
        def slack_trigger():
            # In production: verify Slack signing secret here
            data = request.json or {}
            logger.info("Slack trigger received: %s", data)

            # Run agent in background thread so we can respond to Slack immediately
            thread = threading.Thread(target=self.agent.run_once)
            thread.daemon = True
            thread.start()

            return jsonify({"text": "Agent cycle started."})

        logger.info("Slack listener starting on port %d", port)
        app.run(host="0.0.0.0", port=port)
