"""
msa/scratchpad.py — Persistent scratchpad memory for the MSA.

The scratchpad is the agent's only persistent memory. It is a YAML file
(scratchpads/active.yaml by default) that is:
  - Read at the start of every cycle (wake).
  - Mutated in memory throughout the cycle by the dispatcher.
  - Written back at the end of every cycle (sleep).

No state is held in Python memory between cycles. This means any cycle can be
replayed or debugged by restoring active.yaml to a prior snapshot.

State schema (DEFAULT_SCHEMA)
------------------------------
goals           list  High-level objectives. Stable across cycles.
current_task    str   One concrete thing to do this cycle. Advances on "done".
pending_actions list  Queue of future tasks. pop(0) feeds current_task.
completed_tasks list  Append-only history of {task, summary} dicts.
notes           str   Working memory and tool output log for the current cycle.
last_updated    str   ISO timestamp. Set by save(), not by the agent itself.

Snapshotting
------------
snapshot() is called twice per cycle — before and after run_cycle() — writing
files named <cycle_id>_before.yaml and <cycle_id>_after.yaml. Comparing the
pair shows exactly what changed: which task completed, what tools ran, what
the model wrote to notes.
"""

import yaml
from datetime import datetime
from pathlib import Path


DEFAULT_SCHEMA = {
    "goals": [],
    "current_task": None,
    "pending_actions": [],
    "completed_tasks": [],
    "notes": "",
    "last_updated": None,
}


class Scratchpad:
    """
    Reads, writes, and snapshots the YAML state file.

    Does not interpret the meaning of any field — that is the dispatcher's job.
    Knows only the expected keys (via DEFAULT_SCHEMA) and how to serialize them.
    """

    def __init__(self, path: str = "scratchpads/active.yaml"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """
        Load scratchpad from disk and fill in any missing keys from DEFAULT_SCHEMA.

        Returns DEFAULT_SCHEMA if the file does not exist, allowing the agent to
        start fresh without requiring a pre-existing file.
        """
        if not self.path.exists():
            return dict(DEFAULT_SCHEMA)
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}
        # Ensure all keys present
        for k, v in DEFAULT_SCHEMA.items():
            data.setdefault(k, v)
        return data

    def save(self, state: dict):
        """Write state to active.yaml, stamping last_updated with the current time."""
        state["last_updated"] = datetime.now().isoformat()
        with open(self.path, "w") as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

    def snapshot(self, state: dict, cycle_id: str, label: str):
        """
        Write a read-only copy of state to scratchpads/<cycle_id>_<label>.yaml.

        Called with label="before" at wake and label="after" at sleep.
        These files are never modified by the agent; they exist only for auditing.
        """
        snap_path = self.path.parent / f"{cycle_id}_{label}.yaml"
        with open(snap_path, "w") as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

    def format(self, state: dict) -> str:
        """Serialize state to a YAML string for inclusion in the model prompt."""
        return yaml.dump(state, default_flow_style=False, sort_keys=False)

    def initialize(self, goals: list, first_task: str = None, notes: str = ""):
        """
        Write a clean initial state to active.yaml.

        Used by reset.sh (via direct invocation) to establish a known-good
        starting state before a test cycle. Not called during normal operation.
        """
        state = dict(DEFAULT_SCHEMA)
        state["goals"] = goals
        state["current_task"] = first_task or (goals[0] if goals else None)
        state["notes"] = notes
        self.save(state)
        print(f"Scratchpad initialized at {self.path}")
