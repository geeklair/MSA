# MSA — Internals Reference

This document explains how the MSA codebase works at the implementation level. It is a companion to `README.md` (which covers setup and usage) and `CLAUDE.md` (which covers configuration). Read this when you want to understand *how* the code actually executes.

---

## Table of Contents

1. [One complete cycle, traced](#1-one-complete-cycle-traced)
2. [Module responsibilities](#2-module-responsibilities)
3. [The dispatcher in depth](#3-the-dispatcher-in-depth)
4. [State: what it is and how it flows](#4-state-what-it-is-and-how-it-flows)
5. [The prompt: what the model actually sees](#5-the-prompt-what-the-model-actually-sees)
6. [Tools: registration and security](#6-tools-registration-and-security)
7. [Scheduler modes](#7-scheduler-modes)
8. [Configuration loading](#8-configuration-loading)
9. [Error handling and failure modes](#9-error-handling-and-failure-modes)

---

## 1. One complete cycle, traced

A single `python3 -m msa.agent --once` call walks through these steps in order:

```
main()                          agent.py:120
  └─ Agent.__init__()           agent.py:21
       load_config()            config.py:26   — deep-merge defaults with config.yaml
       Scratchpad(path)         scratchpad.py:24
       ToolRegistry()           tools.py:232   — registers 5 built-in tools
       ModelClient(config)      model.py:17
       Dispatcher(tools)        dispatcher.py:18
       _load_rules()            agent.py:30    — reads config/rules.md into a string

  └─ agent.run_once()           agent.py:92
       _setup_logging()         agent.py:111   — opens logs/cycle_TIMESTAMP.log
       wake()                   agent.py:37
         scratchpad.load()      scratchpad.py:28  — reads active.yaml, fills in missing keys
       scratchpad.snapshot()    scratchpad.py:45  — writes TIMESTAMP_before.yaml

       run_cycle(state)         agent.py:44
         [loop up to max_iterations times]
           _build_prompt(state) agent.py:76    — formats rules.md + YAML state + tool list
           model.complete()     model.py:24    — sends to Anthropic / vLLM / Ollama, returns str
           dispatcher.dispatch() dispatcher.py:21
             _parse_response()  dispatcher.py:64  — extracts JSON from model text
             [branch on tool name]
               "done"           → _mark_complete(), returns is_done=True
               "update_scratchpad" → _apply_scratchpad_update(), continues loop
               other            → tools.call(), _record_tool_result(), continues loop
         if done or max_iterations reached → exit loop

       scratchpad.snapshot()    scratchpad.py:45  — writes TIMESTAMP_after.yaml
       sleep(state)             agent.py:71
         scratchpad.save()      scratchpad.py:39  — writes active.yaml, updates last_updated
```

The iteration loop is the heart of the system. The model is called once per iteration. Each call returns exactly one action. The action is executed, state is updated, and the model is called again with the new state — until it signals `done` or `max_iterations` is reached.

---

## 2. Module responsibilities

| Module | Single responsibility |
|---|---|
| `agent.py` | Owns the wake/run/sleep lifecycle. Knows about all other modules. Nothing else does. |
| `dispatcher.py` | Parses model text into a structured action; routes the action to the right handler. Knows about tools and state shape, but not about the model or scheduler. |
| `model.py` | Abstracts the LLM API call. Accepts `system` and `user` strings; returns a string. Knows nothing about state, tools, or the agent loop. |
| `scratchpad.py` | Reads and writes `active.yaml`. Handles snapshotting. Knows the state schema (via `DEFAULT_SCHEMA`) but not what any field means. |
| `tools.py` | Defines what the agent can do. Each tool is independent. The registry maps names to tool instances. |
| `scheduler.py` | Decides *when* to call `agent.run_once()`. The three modes share no code. |
| `config.py` | Loads `config/config.yaml`, deep-merges it over hardcoded defaults, returns a plain dict. |

The dependency graph flows one way: `agent` → everything else. No other module imports from `agent`. `dispatcher` imports `tools` (indirectly, via the registry it receives). Everything else is independent.

---

## 3. The dispatcher in depth

`dispatcher.py` is the bridge between model text and executable action. It has two jobs: parse and route.

### Parsing: `_parse_response()` (line 64)

The model returns a raw string. The dispatcher tries two strategies in order:

**Strategy 1 — direct parse:**
```python
return json.loads(response.strip())
```
Works when the model returns pure JSON and nothing else, which well-instructed models do reliably.

**Strategy 2 — regex extraction:**
```python
match = re.search(r'\{.*\}', response, re.DOTALL)
```
If the model wrapped its JSON in prose — e.g. `"Here is my action: {"tool": "echo", ...}"` — this strips the surrounding text and parses just the `{...}` block. The `re.DOTALL` flag allows the JSON to span multiple lines.

If both strategies fail, `_parse_response` returns `None`. The caller (`dispatch`) writes a `[PARSE ERROR]` note to the scratchpad and returns `is_done=False`, allowing the next iteration to try again rather than crashing the cycle.

### Routing: `dispatch()` (line 21)

After parsing, `dispatch` branches on the `tool` field:

| `tool` value | Handler | Effect |
|---|---|---|
| `"done"` | `_mark_complete()` | Moves `current_task` into `completed_tasks`; promotes the first item of `pending_actions` to `current_task`; appends `[COMPLETED] <summary>` to `notes`; returns `is_done=True` |
| `"update_scratchpad"` | `_apply_scratchpad_update()` | Merges `args` into state dict; `completed_tasks` and `pending_actions` receive special list handling; returns `is_done=False` |
| anything else | `tools.call()` | Looks up the tool by name; calls `run(**args)`; appends `[TOOL] name(args) → result` to `notes`; returns `is_done=False` |

If the tool name is not registered, the dispatcher writes `[UNKNOWN TOOL] name` to notes rather than raising.

### Notes field as execution log

Every action the dispatcher takes appends a tagged line to `state["notes"]`:

```
[TOOL] shell({'command': 'date -u'}) → Thu Apr  3 12:00:00 UTC 2026
[COMPLETED] Retrieved the current UTC time.
[PARSE ERROR] Could not parse: I'm sorry, I think I should...
[TOOL ERROR] http_get: Connection refused
[UNKNOWN TOOL] web_search
```

This means `notes` serves double duty: it is the agent's working memory *and* the execution log for the current cycle. The agent can read its own tool results via the scratchpad in subsequent iterations.

---

## 4. State: what it is and how it flows

State is a plain Python dict that mirrors `active.yaml`. Its canonical schema, defined in `scratchpad.py:13`:

```python
DEFAULT_SCHEMA = {
    "goals": [],           # Stable across cycles. What the agent is for.
    "current_task": None,  # One concrete thing to do this cycle.
    "pending_actions": [], # Queue of future tasks.
    "completed_tasks": [], # Append-only history.
    "notes": "",           # Working memory + tool output log.
    "last_updated": None,  # Set by scratchpad.save(), not by the agent.
}
```

**Flow within a cycle:**

1. `scratchpad.load()` reads YAML → Python dict. Missing keys are filled from `DEFAULT_SCHEMA`.
2. The dict is passed by reference through `run_cycle`. Each `dispatcher.dispatch()` call mutates it in place and returns the updated dict.
3. `scratchpad.save()` serializes the final dict back to YAML and stamps `last_updated`.

**Nothing persists in memory between cycles.** `Agent` holds no instance-level state from one `run_once()` call to the next. Every cycle starts from a cold read of `active.yaml`.

**`pending_actions` is a queue.** When the agent signals `done`, `_mark_complete()` does `pending_actions.pop(0)` and assigns the result to `current_task`. If `pending_actions` is empty, `current_task` becomes `None`, which the model interprets as "nothing left to do."

---

## 5. The prompt: what the model actually sees

Every iteration, `_build_prompt()` (agent.py:76) assembles a user-turn message from three parts:

```
Current scratchpad state:
<YAML dump of full state dict>

Available tools:
<tool registry description lines>

Instructions:
- Review your current_task and pending_actions
- Take the next appropriate action using an available tool, OR update your scratchpad
- To call a tool, respond with JSON: {"tool": "tool_name", "args": {...}}
- To update scratchpad only, respond with JSON: {"tool": "update_scratchpad", "args": {...}}
- To signal you are done, respond with JSON: {"tool": "done", "args": {"summary": "..."}}
```

The system turn is the full text of `config/rules.md`, loaded once at `Agent.__init__()` and reused every iteration.

The tool descriptions come from `ToolRegistry.describe()` (tools.py:250), which renders each tool's `description` field as a bullet list. This is what the model reads to know which tools exist and what arguments they take.

**The model sees updated state on every iteration.** Because `_build_prompt` receives the current `state` dict each time, the model can read the `[TOOL]` notes appended by prior iterations and use those results to decide what to do next.

---

## 6. Tools: registration and security

### Adding a tool

Every tool is a subclass of `BaseTool` (tools.py:46) with three things:

```python
class MyTool(BaseTool):
    name = "my_tool"              # The string the model uses in {"tool": "my_tool"}
    description = "Does X."       # Shown to the model in the prompt

    def run(self, param: str = "", **kwargs) -> str:
        return f"result: {param}"
```

Register it in `ToolRegistry.__init__()`:
```python
self.register(MyTool())
```

`**kwargs` in `run()` absorbs any extra keys the model includes in `args` without raising.

`YoloDetectTool` (already registered) is a worked example of a heavier tool: it uses a lazy `from ultralytics import YOLO` inside `run()` so the import only fires when the tool is actually called, and it calls `_validate_path()` before touching the filesystem. It returns a JSON string (a list of `{class, confidence, box}` objects) rather than plain text, which the agent can write to a file or log to notes. Use it as a reference when adding tools that have large or optional dependencies.

### Security controls

**ShellTool** uses `subprocess.run(..., shell=False)` with `shlex.split()`. This prevents shell metacharacter injection: if the model emits `"ls; rm -rf /"`, `shlex.split` tokenizes it into `["ls;", "rm", "-rf", "/"]` and `subprocess.run` tries to execute a binary literally named `ls;`, which does not exist. It does *not* prevent the model from requesting dangerous but valid commands like `rm -rf /`; add an allowlist for production.

**ReadFileTool and WriteFileTool** call `_validate_path()` (tools.py:106) before touching the filesystem. `_validate_path` resolves the full absolute path and calls `resolved.relative_to(_BASE_DIR)`. If the resolved path is outside the project directory, it raises `ValueError`. This blocks traversal attacks like `read_file("../../etc/passwd")`.

**HttpGetTool** enforces two checks before opening a connection:
1. Scheme must be `http` or `https`.
2. The hostname is resolved via DNS and the resulting IP is checked against `_BLOCKED_NETWORKS` (tools.py:167): loopback, RFC 1918 private ranges, link-local (which includes the AWS instance metadata endpoint at `169.254.169.254`), and IPv6 equivalents. This prevents SSRF attacks where a model requests internal infrastructure.

---

## 7. Scheduler modes

The scheduler's only job is to call `agent.run_once()` at the right time. All three modes share that single call.

### `interval` (default)

```python
while True:
    agent.run_once()
    time.sleep(interval_seconds)
```

Runs immediately on start, then sleeps. No drift correction — if a cycle takes 30 seconds and `interval_seconds` is 60, the next cycle starts 90 seconds after the first.

### `file_watch`

Polls for the existence of `tmp/msa_trigger` every 2 seconds. When found, deletes the file and runs a cycle. Useful for manual one-shot triggering without restart. Create the trigger with `touch tmp/msa_trigger`.

### `slack` (Socket Mode)

Uses `slack_bolt` with `SocketModeHandler`. No inbound port is needed — the bolt library opens an outbound WebSocket to Slack's servers.

Key implementation details:
- `ack()` is called as the **first line** of every event handler. Slack retries events that are not acknowledged within 3 seconds. The agent cycle runs *after* ack, in a background thread, so Slack's window is always met.
- A `threading.Lock` (`cycle_lock`) prevents two simultaneous cycles if messages arrive faster than one cycle runs. If locked, the handler posts a "already running" message to the channel.
- `seen_event_ids` is a belt-and-suspenders deduplication set on top of Bolt's built-in dedup. Belt-and-suspenders because Bolt's dedup operates per-handler; the explicit set guards against edge cases in multi-handler setups.
- The bot's own user ID is fetched once at startup via `auth_test()`. Any event from that user ID is dropped to prevent reply loops.

---

## 8. Configuration loading

`load_config()` (config.py:26) applies a deep merge: `DEFAULT_CONFIG` provides every possible key with a sensible default; `config.yaml` overrides only what you specify.

`_deep_merge` (config.py:37) recurses into nested dicts, so you can override a single model field without repeating the others:

```yaml
# config.yaml — only overrides model.backend; model.model and model.max_tokens keep defaults
model:
  backend: ollama
```

The `scheduler` field accepts either a plain string or a dict (handled in `Scheduler.__init__` at scheduler.py:24):

```yaml
scheduler: interval          # equivalent to {mode: interval}
scheduler:
  mode: interval
  interval_seconds: 120
```

---

## 9. Error handling and failure modes

| Failure | Where caught | What happens |
|---|---|---|
| `config.yaml` missing | `load_config()` | Silently uses `DEFAULT_CONFIG` |
| `active.yaml` missing | `scratchpad.load()` | Returns `DEFAULT_SCHEMA`; a fresh cycle starts |
| `rules.md` missing | `_load_rules()` | Falls back to a minimal default prompt string |
| Model API error | `model.py` backends | Re-raises; caught by `run_once()`, logs error, keeps `state_before` as `state_after` |
| Malformed model JSON | `_parse_response()` | Returns `None`; dispatcher writes `[PARSE ERROR]` to notes; cycle continues |
| Unknown tool name | `dispatch()` | Writes `[UNKNOWN TOOL]` to notes; cycle continues |
| Tool raises exception | `dispatch()` | Writes `[TOOL ERROR]` to notes; cycle continues |
| Cycle exception (uncaught) | `run_once()` | Logs error; saves pre-cycle state with an error note; still writes `_after.yaml` |

The general philosophy: individual iteration failures are non-fatal. The agent writes evidence of the failure into `notes`, which the model can read on the next iteration and adapt to. Only an exception that escapes `run_cycle` entirely causes the cycle to abort with the pre-cycle state preserved.
