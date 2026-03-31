# Minimal Synthetic Agent (MSA)

A minimal, teachable agent architecture designed for students and researchers
to understand the core loop of an autonomous agent system.

## Architecture Overview

```
Rules (system prompt)
     ↓
Scheduler (cron / event / Slack trigger)
     ↓
Load Scratchpad → Build Prompt → Model
                                   ↓
                            Output Dispatcher
                            ↙            ↘
                       Tool Calls      Scratchpad Update
                            ↘            ↙
                          Log & Sleep
```

## Components

| Component | File | Purpose |
|-----------|------|---------|
| Agent Loop | `msa/agent.py` | Core wake/run/sleep cycle |
| Scratchpad | `msa/scratchpad.py` | Persistent memory (YAML file) |
| Dispatcher | `msa/dispatcher.py` | Parses model output, routes to tools |
| Tools | `msa/tools.py` | Tool registry and stub implementations |
| Scheduler | `msa/scheduler.py` | Cron and event-based triggers |
| Rules | `config/rules.md` | System prompt / agent identity |
| Config | `config/config.yaml` | Runtime configuration |

## Scratchpad Schema

The scratchpad is a YAML file that persists between wake cycles:

```yaml
goals:           # What the agent is trying to accomplish (stable)
current_task:    # What it's working on right now
pending_actions: # Queued actions to take next wake
completed_tasks: # History of what's been done
notes:           # Agent's working memory / observations
last_updated:    # Timestamp of last modification
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the agent once (manual trigger)
python -m msa.agent --once

# Run with scheduler (cron mode)
python -m msa.agent --schedule

# Watch scratchpad evolve
watch -n 5 cat scratchpads/active.yaml
```

## Logging

Every wake/sleep cycle is logged to `logs/`. Each cycle produces:
- `logs/cycle_TIMESTAMP.log` — full trace of the cycle
- `scratchpads/TIMESTAMP_before.yaml` — scratchpad state at wake
- `scratchpads/TIMESTAMP_after.yaml` — scratchpad state at sleep

This allows full reconstruction of agent reasoning over time.

## Connecting to Claude in the Loop

See `config/config.yaml` for model configuration. The agent supports:
- **Local models** via vLLM (recommended for students)
- **Anthropic API** via `claude-sonnet-4-20250514`
- **Ollama** for fully local operation

## MCP / Remote Access

See `MCP_SETUP.md` for configuring Claude to interact directly with
this codebase via the Model Context Protocol (MCP).
