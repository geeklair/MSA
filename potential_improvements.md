# Potential Improvements

These are known security and quality improvements that have not yet been
implemented.  Each item is self-contained so it can be tackled in any order.

---

## 1. Log redaction for secrets

**File:** `msa/agent.py` (around the `logger.info("Model response: ...")` call)

**What:** Raw model responses and tool outputs are written to disk in `logs/`.
If the model ever echoes a credential (e.g. an API key passed in a prompt or
returned by a tool), it will be stored in plaintext in the log file.

**Why to fix:** Log files are typically less carefully protected than secret
stores, are often shipped to centralised logging systems, and may be readable
by other users on the same machine.  Scrubbing known secret patterns before
writing prevents accidental exfiltration.

**How:** Add a redaction filter to the logging pipeline that replaces strings
matching common secret patterns (e.g. `sk-ant-[A-Za-z0-9-]{20,}`,
`Bearer [A-Za-z0-9._-]+`) with `[REDACTED]` before the record hits any
handler.

---

## 2. JSON schema validation in the dispatcher

**File:** `msa/dispatcher.py` (`_parse_response` method)

**What:** The dispatcher parses the model's JSON response with no structural
validation.  If the model emits JSON that is missing the `tool` or `args`
keys, the error surfaces as a `KeyError` or `AttributeError` deep in the call
stack rather than a clear, handled failure.

**Why to fix:** Structured validation makes the failure mode explicit and
predictable, simplifies debugging, and prevents subtly malformed responses
from causing confusing exceptions.

**How:** After `json.loads` succeeds, assert the result is a `dict` containing
`"tool"` (a non-empty string) and `"args"` (a dict).  Return `None` with a
warning log if validation fails, the same as a parse error.  The `jsonschema`
package can do this in a few lines, or a manual check is equally fine for this
small schema.

---

## 3. Restrict file permissions on logs and scratchpads

**What:** `logs/` and `scratchpads/` are currently created with default umask
permissions (`664` files, `775` directories), making them readable by any
user on the machine.

**Why to fix:** Log files contain full execution traces.  Scratchpad files
contain the agent's complete state, task history, and any tool outputs — which
may include credentials or sensitive data the agent was asked to handle.
World-readable permissions make this available to any local user or process.

**How:** After creating these directories (in `agent.py` or wherever they are
initialised), call `os.chmod` to set them to `0o700`.  Newly written files
will then inherit restrictive permissions from the directory's ACL, or
`os.chmod` can be called on each file after creation.

---

## 4. Rate limiting on the Slack listener

**File:** `msa/scheduler.py` (`_run_slack_listener`)

**What:** The `/slack/trigger` endpoint has no rate limiting.  A client that
knows (or can forge) a valid signed request can hammer the endpoint and cause
the agent to spin up continuous background threads.

**Why to fix:** Unconstrained triggering can exhaust system resources (threads,
API quota, downstream service limits) and make the system unavailable.

**How:** Add per-IP rate limiting with `Flask-Limiter` (e.g. `"10 per minute"`
on the route).  Even a simple in-process counter with a time window is
sufficient for most deployments.

---

## 5. HTTP security headers on the Slack listener

**File:** `msa/scheduler.py` (`_run_slack_listener`)

**What:** The Flask app returns responses with no security-related HTTP
headers (`X-Content-Type-Options`, `X-Frame-Options`, etc.).

**Why to fix:** While this endpoint is not a browser-facing UI, adding headers
is a low-effort defence-in-depth measure and establishes good habits if the
Flask app is ever extended with additional routes.

**How:** Use `Flask-Talisman` or add an `@app.after_request` hook that injects
standard headers (`X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY`, `Content-Security-Policy: default-src 'none'`) on
every response.

---

## 6. ShellTool command allowlist

**File:** `msa/tools.py` (`ShellTool`)

**What:** The current fix (replacing `shell=True` with `shell=False` +
`shlex.split`) blocks shell-metacharacter injection but does not prevent the
model from requesting inherently dangerous programs such as `rm`, `curl`,
`python`, or `nc`.

**Why to fix:** For any deployment where the agent operates with limited
trust, or where auditability of agent actions is important, restricting the
set of permitted commands provides a clear security boundary and makes it
easier to reason about what the agent can do.

**How:** Define an allowlist of permitted command prefixes (e.g.
`["git", "ls", "cat", "grep"]`) in `config.yaml` and check `args[0]` against
it before calling `subprocess.run`.  Return an error if the command is not on
the list.

---

## 7. Encrypt or restrict access to scratchpad state

**File:** `msa/scratchpads/`

**What:** Scratchpad YAML files store the agent's full working state in
plaintext.  If the agent is ever given a credential to use (e.g. an API key
for an external service), it will be stored unencrypted in the scratchpad.

**Why to fix:** Plaintext state files are an easy target for exfiltration —
a single file read gives an attacker the agent's complete history and any
secrets it was handling.

**How:** The quickest improvement is restricting directory permissions (see
item 3 above).  For stronger protection, sensitive fields within the YAML
(e.g. anything under a `secrets:` key) could be encrypted at rest using the
`cryptography` package with a key derived from an environment variable.
