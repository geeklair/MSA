"""
msa/agent.py — Core wake/run/sleep loop for the Minimal Synthetic Agent.
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
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self.scratchpad = Scratchpad(self.config["scratchpad_path"])
        self.tools = ToolRegistry(self.config.get("tools", {}))
        self.model = ModelClient(self.config["model"])
        self.dispatcher = Dispatcher(self.tools)
        self.rules = self._load_rules()
        self.max_iterations = self.config.get("max_iterations", 5)

    def _load_rules(self) -> str:
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
        """Execute one full think → act → update cycle."""
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
        """Single wake → run → sleep cycle."""
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
