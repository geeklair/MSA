"""
msa/dispatcher.py — Parses model output and routes to tools or scratchpad updates.

This is the critical bridge between model output and action execution.
The model is expected to respond with JSON action objects.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

DONE_SIGNAL = "done"


class Dispatcher:
    def __init__(self, tool_registry):
        self.tools = tool_registry

    def dispatch(self, response: str, state: dict) -> tuple[dict, bool]:
        """
        Parse model response, execute action, update state.
        Returns (updated_state, is_done).
        """
        action = self._parse_response(response)

        if action is None:
            logger.warning("Could not parse action from response. Logging and continuing.")
            state["notes"] += f"\n[PARSE ERROR] Could not parse: {response[:200]}"
            return state, False

        tool_name = action.get("tool", "")
        args = action.get("args", {})

        logger.info("Dispatching tool: %s with args: %s", tool_name, args)

        # Done signal
        if tool_name == DONE_SIGNAL:
            summary = args.get("summary", "Task complete.")
            state = self._mark_complete(state, summary)
            return state, True

        # Scratchpad update (no external tool call)
        if tool_name == "update_scratchpad":
            state = self._apply_scratchpad_update(state, args)
            return state, False

        # External tool call
        if self.tools.has(tool_name):
            try:
                result = self.tools.call(tool_name, args)
                logger.info("Tool result: %s", str(result)[:200])
                state = self._record_tool_result(state, tool_name, args, result)
            except Exception as e:
                logger.error("Tool %s failed: %s", tool_name, e)
                state["notes"] += f"\n[TOOL ERROR] {tool_name}: {e}"
        else:
            logger.warning("Unknown tool: %s", tool_name)
            state["notes"] += f"\n[UNKNOWN TOOL] {tool_name}"

        return state, False

    def _parse_response(self, response: str) -> dict | None:
        """Extract JSON action from model response."""
        # Try direct JSON parse
        try:
            return json.loads(response.strip())
        except json.JSONDecodeError:
            pass

        # Try to extract JSON block from prose response
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        return None

    def _apply_scratchpad_update(self, state: dict, args: dict) -> dict:
        """Merge args into state."""
        for key, value in args.items():
            if key == "completed_tasks" and isinstance(value, list):
                state["completed_tasks"].extend(value)
            elif key == "pending_actions" and isinstance(value, list):
                state["pending_actions"] = value
            else:
                state[key] = value
        return state

    def _mark_complete(self, state: dict, summary: str) -> dict:
        """Move current task to completed, clear pending."""
        if state.get("current_task"):
            state["completed_tasks"].append({
                "task": state["current_task"],
                "summary": summary
            })
        state["current_task"] = (
            state["pending_actions"].pop(0)
            if state.get("pending_actions") else None
        )
        state["notes"] += f"\n[COMPLETED] {summary}"
        return state

    def _record_tool_result(self, state: dict, tool: str, args: dict, result) -> dict:
        """Log tool execution into notes."""
        state["notes"] += f"\n[TOOL] {tool}({args}) → {str(result)[:300]}"
        return state
