# CLAUDE.md — MSA Project Guide

## What this project is

The **Minimal Synthetic Agent (MSA)** is an intentionally small (~600 lines across 7 modules) autonomous agent loop. Its purpose is pedagogy: every component is visible and editable. There is no framework magic hiding the loop.

The agent wakes on a schedule, reads a YAML scratchpad, calls a language model, dispatches the model's JSON response to tools, updates the scratchpad, and sleeps. All state lives in `scratchpads/active.yaml`. No state is held in memory between cycles.

---

## Running the agent

```bash
# First-time setup
bin/install.sh
source .venv/bin/activate
export ANTHROPIC_API_KEY=sk-ant-...

# Verify environment
bin/check-env.sh

# Reset scratchpad and run one cycle
bin/reset.sh
bin/run.sh

# Continuous scheduled run
bin/run.sh --schedule

# Reset everything (scratchpad + old logs/snapshots)
bin/reset.sh --clean
```

Required environment variables:
```bash
export ANTHROPIC_API_KEY=sk-ant-...     # always required for default backend

# Only needed for scheduler mode: slack
export SLACK_BOT_TOKEN=xoxb-...
export SLACK_APP_TOKEN=xapp-...
```

Read the most recent cycle log:
```bash
cat logs/$(ls -t logs/ | head -1)
```

---

## File map

| File | Role | Edit? |
|------|------|-------|
| `msa/agent.py` | `wake → run_cycle → sleep` loop; CLI entry point | No |
| `msa/dispatcher.py` | Parses model JSON, routes to tools or scratchpad | No |
| `msa/model.py` | Multi-backend model client (Anthropic / vLLM / Ollama) | No |
| `msa/scratchpad.py` | Loads, saves, and snapshots `active.yaml` | No |
| `msa/scheduler.py` | Determines when cycles fire (interval / file_watch / slack) | No |
| `msa/config.py` | Loads `config/config.yaml` with deep-merge defaults | No |
| `msa/tools.py` | Tool registry + built-ins (echo, shell, read\_file, write\_file, http\_get, yolo\_detect) | **Add tools here** |
| `config/config.yaml` | Runtime settings: backend, iterations, scheduler mode | **Yes** |
| `config/rules.md` | System prompt: identity, goals, tool list, response format | **Yes** |
| `scratchpads/active.yaml` | Live agent state | **Yes** |
| `scratchpads/active.reset.yaml` | Default reset template (echo demo) | **Yes** |
| `scratchpads/active.yolo.yaml` | YOLO detection example template | **Yes** |
| `scratchpads/*_before/after.yaml` | Per-cycle snapshots (auto-generated) | No |
| `logs/cycle_*.log` | Full execution trace per cycle (auto-generated) | No |
| `bin/install.sh` | Create venv + install deps | No |
| `bin/run.sh` | Run the agent (handles venv activation) | No |
| `bin/reset.sh` | Reset scratchpad from template; `--clean` removes logs/snapshots | No |
| `bin/check-env.sh` | Verify API keys, venv, and config | No |
| `bin/status.sh` | Print current scratchpad and storage summary | No |
| `bin/logs.sh` | Show most recent cycle log (`-f` to follow) | No |
| `bin/trigger.sh` | Fire a file\_watch cycle trigger | No |

---

## The three files to configure

### 1. `config/rules.md` — system prompt

This is what the model reads. Find the `[CONFIGURE: ...]` placeholders and replace them. The **Your Goals** section drives everything — be specific. Vague goals produce vague cycles.

Rules added to this project:
- Call `date -u` via shell **once per cycle**, store the result in `notes`, then reuse it — do not call `date` again.
- Signal `done` **exactly once**. After emitting `{"tool": "done", ...}`, stop — no further actions.
- After completing `current_task`, signal `done` immediately. Do not run echo or other follow-up commands.
- Only use the `echo` tool if it is explicitly listed as a task. Never use it as a filler action.
- If there are no pending tasks, signal `done` immediately with a summary.

### 2. `scratchpads/active.yaml` — agent memory

```yaml
goals:
  - High-level objective (stable across cycles)
current_task: "One concrete thing to do this cycle"
pending_actions:
  - "Next step"
completed_tasks: []   # historical log — never modify past entries
notes: ""             # trim each cycle; only keep what's relevant going forward
last_updated: null
```

One specific task per cycle works far better than vague multi-step goals.

### 3. `config/config.yaml` — runtime settings

```yaml
model:
  backend: anthropic      # anthropic | vllm | ollama
  model: claude-sonnet-4-20250514
  max_tokens: 1024

max_iterations: 5         # max tool calls per cycle

scheduler: interval       # shorthand string OR dict form:
# scheduler:
#   mode: interval        # interval | file_watch | slack
#   interval_seconds: 300
```

`scheduler` accepts either a plain string (`scheduler: slack`) or the full dict form. Both work.

---

## Adding a tool

All tools live in `msa/tools.py`. Subclass `BaseTool`, implement `run()`, register in `ToolRegistry.__init__()`, and add the tool to `config/rules.md` so the model knows it exists.

```python
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does X. Args: param (str)."

    def run(self, param: str = "", **kwargs) -> str:
        return f"result: {param}"
```

Then in `ToolRegistry.__init__()`:
```python
self.register(MyTool())
```

File-path tools should call `_validate_path()` to stay inside the project root. Shell commands should avoid `shell=True`.

---

## Dispatcher contract

The model must emit exactly one JSON object per response:

```json
{"tool": "tool_name", "args": {"key": "value"}}
```

Special tool names handled internally (not in `tools.py`):
- `"update_scratchpad"` — merges `args` into state without calling any external tool
- `"done"` — moves `current_task` to `completed_tasks`, advances to next `pending_actions` item, signals cycle end

If the model returns malformed JSON, the dispatcher logs a `[PARSE ERROR]` to `notes` and continues to the next iteration rather than crashing.

---

## Scheduler modes

| Mode | How it triggers | Notes |
|------|----------------|-------|
| `interval` | Fixed sleep loop | Default; `interval_seconds` in config |
| `file_watch` | `touch tmp/msa_trigger` (project root) | Useful for manual testing |
| `slack` | DM or @mention via Socket Mode | Requires `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` |

The Slack mode uses `slack_bolt` with `SocketModeHandler`. The handler calls `ack()` as its literal first action so Slack's 3-second acknowledgment window is always satisfied. The agent cycle runs in a background thread after acking. Duplicate events are dropped via `event_id` deduplication.

---

## Security model

- **Shell tool** — `shell=False` + `shlex.split` blocks metacharacter injection. Individual dangerous commands are not blocked; add an allowlist for production.
- **File tools** — `_validate_path()` in `tools.py` rejects any path that resolves outside the project root, blocking path-traversal attacks.
- **HTTP tool** — scheme restricted to `http`/`https`; all hostnames resolved and checked against private/reserved IP ranges before connecting (blocks SSRF, AWS metadata endpoint, etc.).
- **Slack** — `bot_id` field and bot user ID checked before dispatching to prevent the bot from triggering itself on its own reply events.

---

## Dependency notes

```
anthropic>=0.25.0       # default model backend
pyyaml>=6.0             # scratchpad serialization
openai>=1.0.0           # vLLM / OpenAI-compat backend
requests>=2.31.0        # Ollama backend
flask>=3.0.0            # legacy webhook listener (unused by current slack mode)
slack-bolt>=1.18.0      # Slack Socket Mode scheduler
ultralytics>=8.0.0      # yolo_detect tool (lazy import — only loaded when tool is called)
```

Install: `pip install -r requirements.txt`
