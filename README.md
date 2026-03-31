# MSA — Minimal Synthetic Agent: Introduction

## 1. What the MSA Is and Why It Exists

The **Minimal Synthetic Agent (MSA)** is a teaching codebase that shows exactly how an autonomous AI agent loop works — stripped of framework magic so every component is visible and editable.

Most agent frameworks abstract away the loop: you never see how state is persisted between calls, how model output gets routed to tools, or what happens when the model doesn't know what to do next. MSA makes all of that explicit. It is intentionally small (~600 lines across 7 modules) and intentionally unsophisticated — the goal is readability, not performance.

**What you learn by working with MSA:**
- How an agent maintains state across time using a scratchpad
- How a system prompt shapes agent behavior
- How tool dispatch works: parsing model output and routing it to real actions
- How to add new capabilities without touching the core loop
- How to audit and debug agent reasoning from logs and snapshots

MSA runs against the Anthropic API by default but also supports local models (vLLM, Ollama) so you can experiment without incurring API costs.

---

## 2. Architecture

Each agent cycle follows this sequence:

```
wake
  └─ load scratchpads/active.yaml
       └─ build prompt (rules.md + scratchpad state + tool list)
            └─ call model (Anthropic / vLLM / Ollama)
                 └─ dispatcher parses JSON response
                      ├─ tool call → tools.py → result logged to scratchpad
                      └─ update_scratchpad → merge args into state
                           └─ repeat until "done" or max_iterations
                                └─ update scratchpads/active.yaml
                                     └─ sleep (write log, create snapshots)
```

**State lives only in the scratchpad.** The agent itself holds no memory between cycles. This means any cycle can be replayed, rewound, or debugged by inspecting a single YAML file.

**One action per iteration.** The model emits exactly one JSON object per response — a tool call or a scratchpad update. This keeps the loop deterministic and the logs readable.

**Snapshots bracket every cycle.** Before and after each run, the scratchpad is snapshotted to `scratchpads/{timestamp}_before.yaml` and `scratchpads/{timestamp}_after.yaml`. You can always reconstruct what the agent was thinking at any point.

---

## 3. File Reference

| File | What it does | Edit? |
|------|-------------|-------|
| `msa/agent.py` | Orchestrates wake/run/sleep; CLI entry point (`--once`, `--schedule`) | No |
| `msa/scratchpad.py` | Loads, saves, and snapshots the YAML state file | No |
| `msa/dispatcher.py` | Parses model JSON output; routes to tools or scratchpad updates | No |
| `msa/model.py` | Multi-backend model client (Anthropic, vLLM, Ollama) | No |
| `msa/tools.py` | Tool registry + built-in tools (echo, shell, read\_file, write\_file, http\_get) | **Yes — add your tools here** |
| `msa/scheduler.py` | Determines when cycles run (interval, file watch, Slack webhook) | No |
| `msa/config.py` | Loads `config/config.yaml` and merges with defaults | No |
| `msa/__init__.py` | Package marker | No |
| `config/config.yaml` | Runtime settings: model backend, iteration limits, scheduler interval | **Yes** |
| `config/rules.md` | System prompt: agent identity, goals, tool list, response format | **Yes** |
| `scratchpads/active.yaml` | Live agent state: goals, current task, pending actions, notes | **Yes** |
| `scratchpads/*_before.yaml` | Pre-cycle snapshots (auto-generated) | No |
| `scratchpads/*_after.yaml` | Post-cycle snapshots (auto-generated) | No |
| `logs/cycle_*.log` | Full execution trace per cycle (auto-generated) | No |
| `reset.sh` | Resets `active.yaml` to a clean test state | Run it, don't edit |
| `requirements.txt` | Python dependencies | No |
| `README.md` | Quick reference and backend options | No |
| `MCP_SETUP.md` | Claude Code / MCP integration guide | No |

---

## 4. What to Customize First

Start with these three files in order.

### `config/rules.md` — Agent identity and behavior

This file is the system prompt. It tells the model who it is, what tools it has, and exactly what JSON format to emit. Open it and find the two `[CONFIGURE: ...]` placeholders:

```
[CONFIGURE: your hostname or environment description]
[CONFIGURE: your working directory]
```

Replace those with your actual host and directory. Then edit the **Your Goals** section to describe what you want the agent to accomplish. The rest of the file — response format, tool descriptions, decision process — can stay as-is until you add new tools.

### `scratchpads/active.yaml` — Starting state

This is the agent's memory. Edit it to set the initial goals and first task you want the agent to pursue:

```yaml
goals:
  - Your high-level objective here
current_task: "The first concrete thing to do"
pending_actions:
  - "Next step after current_task"
completed_tasks: []
notes: ""
last_updated: null
```

Keep `current_task` short and specific. The agent works best when it has one clear task per cycle rather than vague multi-step goals.

### `config/config.yaml` — Runtime parameters

The defaults work out of the box for Anthropic. The settings most likely to need adjustment:

```yaml
model:
  backend: "anthropic"          # or "vllm" or "ollama"
  model: "claude-sonnet-4-20250514"
  max_tokens: 1024

max_iterations: 5               # tool calls allowed per cycle

scheduler:
  mode: "interval"
  interval_seconds: 300         # how often --schedule wakes the agent
```

Increase `max_iterations` if your tasks require more than five steps. Reduce `interval_seconds` for tighter feedback loops during development.

---

## 5. Installation and First Run

### Prerequisites

- Python 3.10+
- An Anthropic API key (or a running vLLM / Ollama instance)

### Setup

```bash
# Clone or enter the project directory
cd /path/to/msa

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Export your API key
export ANTHROPIC_API_KEY=sk-ant-...
```

### First run

```bash
# Reset scratchpad to a clean demo state
bash reset.sh

# Run one complete agent cycle
python3 -m msa.agent --once
```

You should see log output describing the cycle: which task was loaded, what the model decided to do, which tool was called, and what the result was. A new file appears in `logs/` and two snapshot files appear in `scratchpads/`.

### Continuous scheduling

```bash
# Run indefinitely on the configured interval
python3 -m msa.agent --schedule
```

The agent will wake every `interval_seconds`, run a full cycle, and sleep. Use Ctrl-C to stop.

---

## 6. Reading the Output

### Log files — `logs/cycle_*.log`

Each cycle produces one log file. The filename encodes the start time:

```
logs/cycle_20260330_230709.log
```

Inside, you will find the full trace: the prompt sent to the model, the raw model response, the parsed action, the tool result, and any scratchpad updates. If something went wrong, the log shows exactly where — either the model returned malformed JSON, the tool raised an error, or the dispatcher couldn't route the action.

Read the most recent log with:

```bash
cat logs/$(ls -t logs/ | head -1)
```

### Scratchpad snapshots — `scratchpads/`

Every cycle creates two files:

```
scratchpads/20260330_230709_before.yaml   ← state at wake
scratchpads/20260330_230709_after.yaml    ← state at sleep
```

Compare before and after to see exactly what changed: which task moved to `completed_tasks`, what was written to `notes`, what was added to `pending_actions`. If the agent looped or stalled, the before/after pair will show it — the state will be nearly identical.

### Live state — `scratchpads/active.yaml`

This is the agent's current memory. Read it at any time to see where the agent is in its plan. After a successful cycle, `current_task` will have advanced to the next item in `pending_actions` and the previous task will appear in `completed_tasks`.

---

## 7. The `reset.sh` Workflow

Use `reset.sh` to start a clean test cycle without manually editing YAML:

```bash
# 1. Reset the scratchpad to the demo initial state
bash reset.sh

# 2. Optionally clear old logs and snapshots
rm -f logs/*.log scratchpads/*_before.yaml scratchpads/*_after.yaml

# 3. Run a fresh cycle
python3 -m msa.agent --once
```

`reset.sh` overwrites `active.yaml` with a known-good starting state (echo test → write hello.txt → signal done). It does not touch logs or old snapshots.

When you want to test your own goals, edit `reset.sh` to write your preferred initial state, or bypass it entirely and edit `active.yaml` directly.

---

## 8. Next Steps

### Add a custom tool

Open `msa/tools.py`. Every tool is a subclass of `BaseTool` with three things: a `name`, a `description` (shown to the model), and a `run(**kwargs)` method that returns a string.

```python
class MyTool(BaseTool):
    name = "my_tool"
    description = "Does something useful. Args: param (str)."

    def run(self, param: str = "") -> str:
        return f"Result: {param}"
```

Then register it inside `ToolRegistry.__init__()`:

```python
self.register(MyTool())
```

Finally, add the tool to `config/rules.md` in the **Available Actions** section so the model knows it exists. The description in `rules.md` and the description on the class can differ — the class description is shown in prompts, the `rules.md` entry shapes when the model chooses to use it.

### Give the agent real goals

Edit `scratchpads/active.yaml` and `config/rules.md` to describe a genuine recurring task: monitoring a directory, summarizing a log file, polling an API, or managing a queue of work items. The agent loop is already durable — it just needs meaningful goals and the tools to accomplish them.

### Switch to a local model

To run without API costs, set the backend in `config/config.yaml`:

```yaml
model:
  backend: "ollama"
  base_url: "http://localhost:11434/api/generate"
  model: "llama3"
```

Smaller models are less reliable at emitting well-formed JSON. If the dispatcher logs parse errors frequently, simplify the system prompt in `rules.md` and reduce `max_tokens`.

### Integrate with Claude Code via MCP

See `MCP_SETUP.md` for instructions on connecting MSA to Claude Code so a human can observe and intervene in agent cycles in real time.
