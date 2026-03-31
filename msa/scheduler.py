"""
msa/scheduler.py — Trigger-based scheduler for the MSA.

Supports:
  - Cron-style interval scheduling
  - Slack event trigger (webhook listener)
  - Manual trigger via file watch

Security model (Slack listener)
--------------------------------
Every inbound Slack request is authenticated with HMAC-SHA256 before the
agent is triggered.  Two defences are layered:

  1. Signature verification — Slack signs every request using a shared secret
     (SLACK_SIGNING_SECRET).  We recompute the signature from the raw request
     body and reject any request where it doesn't match, ensuring the payload
     originated from Slack and was not tampered with in transit.

  2. Timestamp check — requests older than five minutes are rejected to
     prevent replay attacks (an attacker capturing a valid signed request and
     replaying it later).

The listener binds to 127.0.0.1 by default so it is not exposed to the
network.  To accept external connections, set `slack_host: "0.0.0.0"` in
config and place a TLS-terminating reverse proxy (nginx, Caddy, etc.) in
front of it.
"""

import hashlib
import hmac
import logging
import os
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
        Authenticated Slack webhook listener.

        Required environment variable:
            SLACK_SIGNING_SECRET — the signing secret from your Slack app's
            Basic Information page (Settings > Basic Information > App Credentials).

        The listener refuses to start if SLACK_SIGNING_SECRET is absent so
        there is no way to accidentally run an unauthenticated endpoint.
        """
        try:
            from flask import Flask, request, jsonify, abort
        except ImportError:
            logger.error("Flask required for Slack listener: pip install flask")
            raise

        # Fail fast: refuse to start without the signing secret.
        # Accepting requests without it would let any HTTP client trigger the agent.
        signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
        if not signing_secret:
            raise RuntimeError(
                "SLACK_SIGNING_SECRET environment variable is not set. "
                "Set it to the signing secret from your Slack app's "
                "Basic Information page before starting the Slack listener."
            )

        def _verify_slack_signature(req) -> bool:
            """
            Verify that *req* carries a valid Slack request signature.

            Slack's signing algorithm (https://api.slack.com/authentication/
            verifying-requests-from-slack):
              1. Concatenate "v0:", the request timestamp, ":", and the raw body.
              2. Compute HMAC-SHA256 of that string using SLACK_SIGNING_SECRET.
              3. Prefix the hex digest with "v0=" and compare to X-Slack-Signature.

            Returns True only when the signature matches and the request is fresh
            (timestamp within ±5 minutes of the current time).
            """
            ts = req.headers.get("X-Slack-Request-Timestamp", "")
            sig = req.headers.get("X-Slack-Signature", "")

            if not ts or not sig:
                logger.warning("Slack request missing signature headers — rejected")
                return False

            # Reject stale requests to prevent replay attacks.
            try:
                age = abs(time.time() - float(ts))
            except ValueError:
                logger.warning("Slack request has non-numeric timestamp '%s' — rejected", ts)
                return False
            if age > 300:
                logger.warning("Slack request timestamp too old (%.0fs) — rejected", age)
                return False

            body = req.get_data(as_text=True)
            base_string = f"v0:{ts}:{body}"
            expected = "v0=" + hmac.new(
                signing_secret.encode(),
                base_string.encode(),
                hashlib.sha256,
            ).hexdigest()

            # compare_digest runs in constant time to prevent timing side-channels.
            return hmac.compare_digest(expected, sig)

        # Security: bind to loopback by default so the port is not reachable
        # from other machines.  Override with slack_host: "0.0.0.0" in config
        # only when a reverse proxy with TLS is terminating connections in front.
        host = self.config.get("slack_host", "127.0.0.1")
        port = self.config.get("slack_port", 3000)

        app = Flask(__name__)

        @app.route("/slack/trigger", methods=["POST"])
        def slack_trigger():
            if not _verify_slack_signature(request):
                abort(403)

            data = request.json or {}
            logger.info("Slack trigger received: %s", data)

            # Run agent in a background thread so we can return a 200 response
            # to Slack within its 3-second acknowledgement window.
            thread = threading.Thread(target=self.agent.run_once)
            thread.daemon = True
            thread.start()

            return jsonify({"text": "Agent cycle started."})

        logger.info("Slack listener starting on %s:%d", host, port)
        app.run(host=host, port=port)
