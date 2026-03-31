"""
msa/scratchpad.py — Persistent scratchpad memory for the MSA.

The scratchpad is a YAML file that the agent reads at wake and writes at sleep.
Every cycle snapshots before/after for full auditability.
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
    def __init__(self, path: str = "scratchpads/active.yaml"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        """Load scratchpad from disk. Returns default schema if file missing."""
        if not self.path.exists():
            return dict(DEFAULT_SCHEMA)
        with open(self.path) as f:
            data = yaml.safe_load(f) or {}
        # Ensure all keys present
        for k, v in DEFAULT_SCHEMA.items():
            data.setdefault(k, v)
        return data

    def save(self, state: dict):
        """Write scratchpad to disk with updated timestamp."""
        state["last_updated"] = datetime.now().isoformat()
        with open(self.path, "w") as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

    def snapshot(self, state: dict, cycle_id: str, label: str):
        """Save a timestamped snapshot for audit trail."""
        snap_path = self.path.parent / f"{cycle_id}_{label}.yaml"
        with open(snap_path, "w") as f:
            yaml.dump(state, f, default_flow_style=False, sort_keys=False)

    def format(self, state: dict) -> str:
        """Return human-readable scratchpad for inclusion in prompt."""
        return yaml.dump(state, default_flow_style=False, sort_keys=False)

    def initialize(self, goals: list, first_task: str = None, notes: str = ""):
        """Bootstrap a fresh scratchpad with initial goals."""
        state = dict(DEFAULT_SCHEMA)
        state["goals"] = goals
        state["current_task"] = first_task or (goals[0] if goals else None)
        state["notes"] = notes
        self.save(state)
        print(f"Scratchpad initialized at {self.path}")
