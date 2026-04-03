"""
msa/agent.py — Core wake/run/sleep loop for the Minimal Synthetic Agent.

Entry point for both CLI modes:
  --once      Run a single wake → run_cycle → sleep and exit.
  --schedule  Hand off to Scheduler, which calls run_once() repeatedly.

Agent owns the object graph: it instantiates every other module and wires
them together. No other module imports from agent.

Cycle lifecycle
---------------
1. wake()         Load state from scratchpad; create _before snapshot.
2. run_cycle()    Loop: build prompt → call model → dispatch response.
                  Exits when dispatcher signals done or max_iterations hit.
3. sleep()        Save updated state; create _after snapshot.

Logging
-------
Each run_once() call opens a new FileHandler writing to
logs/cycle_YYYYMMDD_HHMMSS.log. The root logger is set to DEBUG so every
model response, tool call, and state change is captured.
"""

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path

from .scratchpad import Scratchpad
from .dispatcher import Dispatcher
from .tools import ToolRegistry
from .model import ModelClient
from .config import load_config

logger = logging.getLogger(__name__)


class Agent:
    """
    The agent object graph.

    Instantiated once per process. Holds references to all subsystems.
    Stateless between cycles — no instance variables are mutated during
    run_once(); all mutable state lives in the scratchpad YAML file.
    """

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self.scratchpad = Scratchpad(self.config["scratchpad_path"])
        self.tools = ToolRegistry(self.config.get("tools", {}))
        self.model = ModelClient(self.config["model"])
        self.dispatcher = Dispatcher(self.tools)
        self.rules = self._load_rules()
        self.max_iterations = self.config.get("max_iterations", 5)

    def _load_rules(self) -> str:
        """Read rules.md into a string used as the system prompt every iteration."""
        rules_path = Path(self.config.get("rules_path", "config/rules.md"))
        if rules_path.exists():
            return rules_path.read_text()
        logger.warning("No rules file found at %s", rules_path)
        return "You are a helpful agent. Complete tasks listed in your scratchpad."

    def wake(self) -> dict:
        """Load state at the start of a cycle."""
        logger.info("=== AGENT WAKING ===")
        state = self.scratchpad.load()
        logger.info("Current task: %s", state.get("current_task", "none"))
        return state

    def run_cycle(self, state: dict) -> dict:
        """
        Run the inner iteration loop for one agent cycle.

        Each iteration:
          1. Builds a prompt from rules.md + current state + tool list.
          2. Sends the prompt to the model; receives one action as text.
          3. Passes the text to the dispatcher, which parses and executes it.
          4. Checks the dispatcher's is_done flag.

        Exits when the model signals "done" or max_iterations is exhausted.
        Returns the final (mutated) state dict.
        """
        iteration = 0

        while iteration < self.max_iterations:
            iteration += 1
            logger.info("--- Iteration %d/%d ---", iteration, self.max_iterations)

            # Build prompt from rules + scratchpad state
            prompt = self._build_prompt(state)

            # Call the model
            response = self.model.complete(
                system=self.rules,
                user=prompt
            )
            logger.info("Model response: %s", response[:200])

            # Dispatch: parse response, call tools, update state
            state, done = self.dispatcher.dispatch(response, state)

            if done:
                logger.info("Agent signaled completion.")
                break

        return state

    def sleep(self, state: dict):
        """Persist updated state at end of cycle."""
        logger.info("=== AGENT SLEEPING ===")
        self.scratchpad.save(state)

    def _build_prompt(self, state: dict) -> str:
        """
        Assemble the user-turn message sent to the model each iteration.

        Combines three elements:
          - The current scratchpad state as YAML (so the model can read tool
            results appended to notes in prior iterations).
          - The available tool descriptions from the registry.
          - Fixed instructions specifying the required JSON response format.

        The system turn (rules.md) is set separately in model.complete() and
        does not change between iterations within a cycle.
        """
        return f"""
Current scratchpad state:
{self.scratchpad.format(state)}

Available tools:
{self.tools.describe()}

Instructions:
- Review your current_task and pending_actions
- Take the next appropriate action using an available tool, OR update your scratchpad
- To call a tool, respond with JSON: {{"tool": "tool_name", "args": {{...}}}}
- To update scratchpad only, respond with JSON: {{"tool": "update_scratchpad", "args": {{...}}}}
- To signal you are done, respond with JSON: {{"tool": "done", "args": {{"summary": "..."}}}}
"""

    def run_once(self):
        """
        Execute one complete agent cycle: wake, run, sleep.

        Creates a unique cycle_id (timestamp string) used for:
          - The log file name: logs/cycle_<cycle_id>.log
          - The snapshot file names: scratchpads/<cycle_id>_before/after.yaml

        On any unhandled exception in run_cycle(), the pre-cycle state is
        preserved (not lost), an error note is appended, and the after
        snapshot is still written so the failure is auditable.
        """
        cycle_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._setup_logging(cycle_id)

        state_before = self.wake()
        self.scratchpad.snapshot(state_before, cycle_id, "before")

        try:
            state_after = self.run_cycle(state_before)
        except Exception as e:
            logger.error("Agent cycle failed: %s", e, exc_info=True)
            state_after = state_before
            state_after["notes"] = f"Cycle {cycle_id} failed: {e}"

        self.scratchpad.snapshot(state_after, cycle_id, "after")
        self.sleep(state_after)
        logger.info("Cycle %s complete.", cycle_id)

    def _setup_logging(self, cycle_id: str):
        log_path = Path("logs") / f"cycle_{cycle_id}.log"
        log_path.parent.mkdir(exist_ok=True)
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        logging.getLogger().addHandler(fh)
        logging.getLogger().setLevel(logging.DEBUG)


def main():
    parser = argparse.ArgumentParser(description="Minimal Synthetic Agent")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--schedule", action="store_true", help="Run on scheduler")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    agent = Agent(config_path=args.config)

    if args.once:
        agent.run_once()
    elif args.schedule:
        from .scheduler import Scheduler
        scheduler = Scheduler(agent)
        scheduler.run()
    else:
        print("Specify --once or --schedule")


if __name__ == "__main__":
    main()
