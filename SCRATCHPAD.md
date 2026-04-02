# The Scratchpad: Lifecycle and File Mechanics

This document explains precisely how the three scratchpad files are created, when each is written, and how state evolves through a cycle. It is intended for students who want to understand the agent's memory system from the code up.

For a high-level description of the files and the YAML schema, see **README.md §6**. For a quick operator reference, see **CLAUDE.md**.

---

## The three files

```
scratchpads/
  active.yaml                    ← the agent's live, durable memory
  20260401_100137_before.yaml    ← state as read at the start of that cycle
  20260401_100137_after.yaml     ← state as left at the end of that cycle
```

The timestamp prefix encodes the cycle start time (`YYYYMMDD_HHMMSS`). Every cycle produces one `_before` and one `_after` pair. `active.yaml` is overwritten every cycle.

---

## The code path

Everything happens inside `agent.run_once()`. Reading it literally:

```python
def run_once(self):
    cycle_id = datetime.now().strftime("%Y%m%d_%H%M%S")   # 1. name the cycle

    state_before = self.wake()                              # 2. read active.yaml
    self.scratchpad.snapshot(state_before, cycle_id, "before")  # 3. write _before

    try:
        state_after = self.run_cycle(state_before)         # 4. evolve state in memory
    except Exception as e:
        state_after = state_before
        state_after["notes"] = f"Cycle {cycle_id} failed: {e}"

    self.scratchpad.snapshot(state_after, cycle_id, "after")   # 5. write _after
    self.sleep(state_after)                                     # 6. write active.yaml
```

There are six distinct events. Each is explained below.

---

## Event 1 — Name the cycle

```python
cycle_id = datetime.now().strftime("%Y%m%d_%H%M%S")
```

`cycle_id` is just a timestamp string used as a filename prefix. It is set once and never changes within the cycle, so both snapshot files always share the same prefix.

---

## Event 2 — Read `active.yaml` into memory (`wake`)

```python
# agent.py
state_before = self.wake()

# scratchpad.py
def load(self) -> dict:
    if not self.path.exists():
        return dict(DEFAULT_SCHEMA)
    with open(self.path) as f:
        data = yaml.safe_load(f) or {}
    for k, v in DEFAULT_SCHEMA.items():
        data.setdefault(k, v)
    return data
```

`load()` reads `active.yaml` from disk and returns a plain Python **dict**. This is the only disk read of the cycle. From this point on the agent works entirely in memory — `active.yaml` is not touched again until Event 6.

If `active.yaml` does not exist (first run, or after `reset.sh`), `load()` returns the default schema:

```python
DEFAULT_SCHEMA = {
    "goals": [],
    "current_task": None,
    "pending_actions": [],
    "completed_tasks": [],
    "notes": "",
    "last_updated": None,
}
```

---

## Event 3 — Write `_before.yaml` (snapshot of incoming state)

```python
self.scratchpad.snapshot(state_before, cycle_id, "before")

def snapshot(self, state: dict, cycle_id: str, label: str):
    snap_path = self.path.parent / f"{cycle_id}_{label}.yaml"
    with open(snap_path, "w") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)
```

`snapshot()` writes the dict to disk **without mutating it**. The `_before` file is an exact copy of what `active.yaml` contained when the cycle started. It is never modified again.

**What `_before.yaml` tells you:** the state the agent *inherited* — what it was tasked with, what it already knew, what it had already done in previous cycles.

---

## Event 4 — Evolve state in memory (`run_cycle`)

This is where almost all the action happens, and it all occurs **in memory**. Nothing is written to disk during iteration.

```python
def run_cycle(self, state: dict) -> dict:
    iteration = 0
    while iteration < self.max_iterations:
        iteration += 1
        prompt = self._build_prompt(state)
        response = self.model.complete(system=self.rules, user=prompt)
        state, done = self.dispatcher.dispatch(response, state)
        if done:
            break
    return state
```

Each iteration:
1. Builds a prompt by serializing the current `state` dict into the user message
2. Sends it to the model and gets a JSON response
3. Passes the response to `dispatcher.dispatch()`, which mutates `state` and returns it

### How the dispatcher mutates state

`dispatcher.dispatch()` parses the model's JSON and takes one of three branches:

**Tool call** (`{"tool": "shell", "args": {"command": "date -u"}}`):
```python
result = self.tools.call(tool_name, args)
state["notes"] += f"\n[TOOL] {tool_name}({args}) → {str(result)[:300]}"
```
The tool result is appended to `notes`. The task queue is not changed.

**Scratchpad update** (`{"tool": "update_scratchpad", "args": {...}}`):
```python
for key, value in args.items():
    if key == "completed_tasks":
        state["completed_tasks"].extend(value)
    elif key == "pending_actions":
        state["pending_actions"] = value
    else:
        state[key] = value
```
Any field can be updated. This is how the agent sets `current_task`, rewrites `goals`, or clears `pending_actions`.

**Done signal** (`{"tool": "done", "args": {"summary": "..."}}`):
```python
if state.get("current_task"):
    state["completed_tasks"].append({
        "task": state["current_task"],
        "summary": summary
    })
state["current_task"] = (
    state["pending_actions"].pop(0)
    if state.get("pending_actions") else None
)
state["notes"] += f"\n[COMPLETED] {summary}"
return state, True   # done=True → exits the while loop
```
The current task is moved to `completed_tasks`, the next item from `pending_actions` becomes `current_task`, and the loop exits.

**Parse failure**: if the model returns something that isn't valid JSON, the dispatcher logs a `[PARSE ERROR]` to `notes` and returns `done=False`. The loop continues to the next iteration.

### What the state dict looks like mid-cycle

After two tool calls and before `done` is signaled, `state` in memory might look like:

```python
{
    "goals": ["Demonstrate the wake/run/sleep cycle", ...],
    "current_task": "Run the echo tool with a hello message",
    "pending_actions": ["Write hello.txt", "Signal done"],
    "completed_tasks": [],
    "notes": "\n[TOOL] echo({'message': 'Hello!'}) → ECHO: Hello!\n[TOOL] shell({'command': 'date -u'}) → Wed Apr 1 15:01:45 UTC 2026\n",
    "last_updated": None,
}
```

Note that `current_task` has **not** changed yet — it only advances when `done` is dispatched. Every tool call just appends to `notes`.

---

## Event 5 — Write `_after.yaml` (snapshot of outgoing state)

```python
self.scratchpad.snapshot(state_after, cycle_id, "after")
```

Same `snapshot()` call as Event 3, same dict-to-YAML write, no mutation. The `_after` file captures the fully-evolved in-memory state at the moment the cycle ended — either because `done` was signaled or `max_iterations` was reached.

**Important detail:** `snapshot()` does not set `last_updated`. That field is only written by `save()` (Event 6). So `_after.yaml` will show `last_updated: null` (or whatever value it had at the start of the cycle). This is visible in real output:

```yaml
# 20260401_100137_after.yaml
notes: "\n[TOOL] echo(...) → ...\n[TOOL] shell(...) → ..."
last_updated: null        # ← snapshot was taken before save() ran
```

---

## Event 6 — Write `active.yaml` (`sleep`)

```python
self.sleep(state_after)

def save(self, state: dict):
    state["last_updated"] = datetime.now().isoformat()   # mutates the dict
    with open(self.path, "w") as f:
        yaml.dump(state, f, default_flow_style=False, sort_keys=False)
```

`save()` **mutates** `state` before writing — it stamps `last_updated` with the current time. Because Python dicts are passed by reference, this mutation happens *after* the `_after` snapshot was written, so the snapshot does not get the timestamp.

`active.yaml` is then nearly identical to `_after.yaml` with one difference: a fresh `last_updated` timestamp.

```yaml
# active.yaml (after same cycle)
notes: "\n[TOOL] echo(...) → ...\n[TOOL] shell(...) → ..."
last_updated: '2026-04-01T15:01:54.203841'   # ← set by save()
```

The next cycle's `_before.yaml` will be an exact copy of this `active.yaml`.

---

## The full picture

```
Disk                      Memory (state dict)             Disk
────────────────────────────────────────────────────────────────────────
active.yaml (N-1)
  │
  │ load()  [Event 2]
  ▼
              state_before ─────────────────────────────► _before.yaml [Event 3]
                  │
                  │  dispatcher iteration 1:
                  │    notes += "[TOOL] ..."
                  ▼
              state (mid-cycle, notes growing)
                  │
                  │  dispatcher iteration 2:
                  │    notes += "[TOOL] ..."
                  ▼
              state (mid-cycle)
                  │
                  │  dispatcher iteration N:
                  │    done=True → current_task advances
                  ▼
              state_after ──────────────────────────────► _after.yaml [Event 5]
                  │                                        (last_updated: null)
                  │ save() stamps last_updated [Event 6]
                  ▼
                                                         active.yaml [Event 6]
                                                          (last_updated: now)
```

---

## What to look for when debugging

**Agent completed the task correctly:**
- `_before.yaml`: `current_task` = the task; `completed_tasks` is shorter
- `_after.yaml`: `current_task` has advanced to the next item; old task is in `completed_tasks`; `notes` shows the tool calls that were made

**Agent hit `max_iterations` without finishing:**
- `_before.yaml` and `_after.yaml` have the **same `current_task`** — it never signaled `done`
- `notes` in `_after.yaml` shows how many iterations were spent and what tools were called
- Common cause: the model called the same tool repeatedly (e.g., `date -u` four times) instead of advancing

**Cycle crashed:**
- `state_after = state_before` is used as the fallback
- `state_after["notes"]` is overwritten with `"Cycle {cycle_id} failed: {error}"`
- Both `active.yaml` and `_after.yaml` will show this error in `notes`
- The full traceback is in `logs/cycle_{cycle_id}.log`

**Nothing changed between before and after:**
- The dispatcher received `update_scratchpad` but the args didn't change any meaningful fields, or
- The model emitted well-formed JSON for a no-op, or
- `max_iterations` was 1 and only one non-completing action was taken

---

## How this relates to other documentation

| Document | Focus |
|----------|-------|
| `README.md §6` | What the files are; how to read them from the command line |
| `CLAUDE.md` | Schema field reference; operator quick-start |
| `SCRATCHPAD.md` (this file) | Precise lifecycle mechanics; how state evolves; debugging |

The README gives you orientation. This document gives you the code-level mechanics you need to understand why the files contain what they contain and how to interpret discrepancies between them.
