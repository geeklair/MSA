# MSA Roadmap

Planned enhancements, roughly ordered by priority.

---

## 1. Pass Slack message text as `current_task`

When a DM or @mention triggers a cycle, forward the message body as the agent's `current_task` so the agent acts on what the user actually asked, rather than whatever was last in the scratchpad.

## 2. Tool containment

Reduce the blast radius of a runaway or misbehaving agent:
- Shell tool: blocklist dangerous commands (`rm -rf`, `sudo`, `curl | sh`, etc.)
- File tools: sandbox reads/writes to the MSA working directory; reject paths outside it

## 3. Run as a dedicated low-privilege user

Create a dedicated system user (e.g. `msa`) with no login shell and minimal filesystem permissions. Run the agent process under that account so OS-level containment backs up tool-level containment.

## 4. Docker containerization

Provide a `Dockerfile` and `compose.yaml` for running MSA in a container:
- Pinned Python version and dependencies
- Mounts for `scratchpads/`, `logs/`, and `config/` so state survives restarts
- Non-root user inside the container

## 5. systemd service

A `msa.service` unit file for persistent, auto-restarting operation on Linux hosts (e.g. Spark nodes). Includes `Restart=on-failure`, `EnvironmentFile` for secrets, and `StandardOutput=journal`.

## 6. Slack command prefix support

Recognize prefixed commands in Slack messages so users can control behavior without modifying config:
- `!run` — trigger a cycle immediately
- `!status` — reply with current scratchpad state (no cycle)
- `!reset` — clear `current_task` and `pending_actions`, signal a fresh start

## 7. Multi-turn conversation support

Let the agent maintain context across multiple Slack exchanges within a thread: accumulate the thread history and pass it as additional context to the model so follow-up messages can refine or extend a task in progress.

## 8. Signal messenger integration

Alternative trigger/reply channel using Signal (via `signal-cli` or the unofficial REST API). Harder than Slack due to phone-number registration and lack of an official bot API, but useful for secure or off-Slack deployments.

## 9. Sage/Waggle-specific tools

Domain tools for the Sage/Waggle edge-computing platform:
- Query node status and sensor streams
- Push/pull manifests or plugin configs
- Trigger edge jobs and poll results

## 10. vLLM backend support

Complete the vLLM / OpenAI-compatible backend path so MSA can run entirely on local models. Includes testing with common open-weight models and documenting the `base_url` + model-name config needed.
