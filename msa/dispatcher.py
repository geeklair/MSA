"""
msa/dispatcher.py — Parses model output and routes to tools or scratchpad updates.

This is the bridge between raw model text and executable action. It has two jobs:

Parse
-----
_parse_response() tries two strategies in order:
  1. Direct json.loads() — works when the model returns pure JSON.
  2. Regex extraction — extracts the first {...} block from prose responses.
If both fail, returns None and the caller appends a [PARSE ERROR] to notes.

Route
-----
dispatch() branches on the parsed "tool" field:
  "done"               → _mark_complete(): move current_task to completed_tasks,
                         promote next pending_actions item, return is_done=True.
  "update_scratchpad"  → _apply_scratchpad_update(): merge args into state dict.
  <any registered tool> → tools.call(): execute the tool, append [TOOL] to notes.
  <unknown name>       → append [UNKNOWN TOOL] to notes, continue.

All failures (parse errors, unknown tools, tool exceptions) are non-fatal: they
write a tagged line into state["notes"] and return is_done=False so the next
iteration can observe the error and adapt.

Notes field conventions
-----------------------
The dispatcher appends structured tags to state["notes"] after each action:
  [TOOL] name(args) → result[:300]
  [COMPLETED] summary
  [PARSE ERROR] raw_response[:200]
  [TOOL ERROR] name: exception
  [UNKNOWN TOOL] name
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

DONE_SIGNAL = "done"


class Dispatcher:
    """
    Parses one model response string and executes the action it encodes.

    Receives a ToolRegistry at construction time. Never imports or instantiates
    tools directly — all tool calls go through the registry.
    """

    def __init__(self, tool_registry):
        self.tools = tool_registry

    def dispatch(self, response: str, state: dict) -> tuple[dict, bool]:
        """
        Parse one model response string and execute the encoded action.

        Args:
            response: Raw text returned by the model. Expected to contain a
                      JSON object of the form {"tool": "...", "args": {...}}.
            state:    The current scratchpad dict. Modified in place and also
                      returned for clarity.

        Returns:
            (updated_state, is_done) — is_done is True only when the model
            emitted {"tool": "done", ...}.
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
        """
        Merge args directly into state without calling any external tool.

        Special cases:
          completed_tasks — extends the existing list (append-only history).
          pending_actions — replaces the list outright (the model resets the queue).
          all other keys  — assign directly (overwrites current value).
        """
        for key, value in args.items():
            if key == "completed_tasks" and isinstance(value, list):
                state["completed_tasks"].extend(value)
            elif key == "pending_actions" and isinstance(value, list):
                state["pending_actions"] = value
            else:
                state[key] = value
        return state

    def _mark_complete(self, state: dict, summary: str) -> dict:
        """
        Handle the "done" signal.

        Records current_task in completed_tasks with the provided summary,
        then advances current_task to the next item in pending_actions (pop(0)).
        If pending_actions is empty, current_task becomes None, which the model
        interprets as "nothing left to do."
        """
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
        """
        Append a [TOOL] line to notes so the model can read the result next iteration.

        Result is truncated to 300 characters to keep the prompt from growing
        unbounded when tools return large outputs (e.g. read_file on a big file).
        """
        state["notes"] += f"\n[TOOL] {tool}({args}) → {str(result)[:300]}"
        return state
