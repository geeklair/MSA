"""
msa/config.py — Configuration loader.
"""

import yaml
from pathlib import Path


DEFAULT_CONFIG = {
    "model": {
        "backend": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
    },
    "scratchpad_path": "scratchpads/active.yaml",
    "rules_path": "config/rules.md",
    "max_iterations": 5,
    "tools": {},
    "scheduler": {
        "mode": "interval",
        "interval_seconds": 300,
    }
}


def load_config(path: str = "config/config.yaml") -> dict:
    config = dict(DEFAULT_CONFIG)
    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        # Deep merge
        _deep_merge(config, user_config)
    return config


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
