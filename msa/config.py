"""
msa/config.py — Configuration loader with deep-merge defaults.

load_config() returns a fully-populated dict by merging DEFAULT_CONFIG (all
possible keys with sensible values) with whatever the user has set in
config/config.yaml. Only the keys the user specifies are overridden.

Deep merge
----------
_deep_merge() recurses into nested dicts, so a user can override a single
field inside "model" without repeating the others:

  # config.yaml — only overrides model.backend
  model:
    backend: ollama

This produces:
  {
    "model": {
      "backend": "ollama",            ← from config.yaml
      "model": "claude-sonnet-...",   ← from DEFAULT_CONFIG
      "max_tokens": 1024,             ← from DEFAULT_CONFIG
    }, ...
  }

Scheduler shorthand
-------------------
The scheduler value may be a plain string or a dict. Scheduler.__init__
handles the normalization; config.py just passes it through unchanged.

  scheduler: interval            # string shorthand
  scheduler:                     # equivalent dict form
    mode: interval
    interval_seconds: 300
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
    """
    Load config/config.yaml and deep-merge it over DEFAULT_CONFIG.

    If the file does not exist, returns DEFAULT_CONFIG unchanged — the agent
    runs with all defaults, which is sufficient for a basic Anthropic setup.
    """
    config = dict(DEFAULT_CONFIG)
    config_path = Path(path)
    if config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        # Deep merge
        _deep_merge(config, user_config)
    return config


def _deep_merge(base: dict, override: dict):
    """
    Recursively merge override into base in place.

    Nested dicts are merged recursively so that inner keys not present in
    override are preserved from base. All other types are overwritten.
    """
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
