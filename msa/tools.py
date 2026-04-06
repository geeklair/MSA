"""
msa/tools.py — Tool registry and stub implementations.

Tools are the agent's "embodiment" — what it can actually DO in the world.
Add new tools by subclassing BaseTool and registering in ToolRegistry.

Security model
--------------
All tool implementations apply defence-in-depth against a compromised or
adversarial model output:

  ShellTool    — uses shell=False + shlex.split to block shell-metacharacter
                 injection (e.g. "ls; rm -rf /").  Individual commands are still
                 passed through; add an allowlist for stricter production use.

  ReadFileTool
  WriteFileTool — constrain all paths to the project root via _validate_path(),
                 blocking traversal attacks such as "../../etc/passwd".

  HttpGetTool  — only http/https schemes are accepted, and all hostnames are
                 checked against private/reserved IP ranges before connecting
                 to prevent Server-Side Request Forgery (SSRF).
"""

import ipaddress
import json
import logging
import shlex
import socket
import subprocess
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

# All file operations (read_file / write_file) are constrained to this
# directory tree.  Resolved at import time so symlink attacks can't move it.
_BASE_DIR = Path(__file__).parent.parent.resolve()


# ---------------------------------------------------------------------------
# Base Tool
# ---------------------------------------------------------------------------

class BaseTool(ABC):
    """
    Abstract base for all agent tools.

    Subclasses must set class-level `name` and `description` strings and
    implement run(**kwargs). The name is the string the model places in the
    "tool" field of its JSON response. The description is shown to the model
    in the prompt so it knows when and how to use the tool.

    run() must accept **kwargs to absorb any extra keys the model may include
    in args without raising a TypeError.
    """

    name: str = ""
    description: str = ""

    @abstractmethod
    def run(self, **kwargs) -> str:
        """Execute the tool and return a string result."""

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description}


# ---------------------------------------------------------------------------
# Built-in Stub Tools
# ---------------------------------------------------------------------------

class EchoTool(BaseTool):
    name = "echo"
    description = "Echo a message back. Useful for testing."

    def run(self, message: str = "", **kwargs) -> str:
        return f"ECHO: {message}"


class ShellTool(BaseTool):
    name = "shell"
    description = "Run a shell command and return stdout. Use carefully."

    def run(self, command: str = "", **kwargs) -> str:
        # Security: shell=False prevents shell-metacharacter injection.
        # Without it, a model output like "ls; rm -rf /" would execute both
        # commands because the string is passed verbatim to /bin/sh.
        # shlex.split tokenises the command string into an argument list the
        # same way a POSIX shell would, without invoking a shell interpreter.
        #
        # NOTE: shell=False does NOT prevent the model from requesting
        # dangerous individual programs (e.g. ["rm", "-rf", "/"]).
        # For production deployments, add an explicit allowlist of permitted
        # command prefixes before calling subprocess.run.
        try:
            args = shlex.split(command)
        except ValueError as e:
            return f"ERROR: Could not parse command: {e}"

        if not args:
            return "ERROR: Empty command"

        try:
            result = subprocess.run(
                args, shell=False, capture_output=True, text=True, timeout=30
            )
            return result.stdout or result.stderr or "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out"
        except FileNotFoundError:
            return f"ERROR: Command not found: {args[0]}"
        except Exception as e:
            return f"ERROR: {e}"


def _validate_path(path_str: str) -> Path:
    """
    Resolve *path_str* and verify it lives inside _BASE_DIR.

    Raises ValueError if the resolved path escapes the project tree.
    This prevents path-traversal attacks such as:
        read_file("../../etc/passwd")
        read_file("/root/.ssh/id_rsa")
        write_file("/etc/cron.d/evil", ...)
    """
    resolved = Path(path_str).resolve()
    try:
        resolved.relative_to(_BASE_DIR)
    except ValueError:
        raise ValueError(
            f"Access denied: '{path_str}' resolves to '{resolved}', which is "
            f"outside the project directory ({_BASE_DIR}). "
            "Only paths within the project tree are permitted."
        )
    return resolved


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a file from disk and return its contents."

    def run(self, path: str = "", filename: str = "", **kwargs) -> str:
        path = path or filename
        # Security: _validate_path() blocks traversal outside the project root.
        try:
            validated = _validate_path(path)
            with open(validated) as f:
                return f.read()
        except ValueError as e:
            return f"ERROR: {e}"
        except Exception as e:
            return f"ERROR: {e}"


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file on disk."

    def run(self, path: str = "", content: str = "", filename: str = "", **kwargs) -> str:
        path = path or filename
        # Security: _validate_path() blocks writes outside the project root.
        try:
            validated = _validate_path(path)
            with open(validated, "w") as f:
                f.write(content)
            return f"Written to {validated}"
        except ValueError as e:
            return f"ERROR: {e}"
        except Exception as e:
            return f"ERROR: {e}"


# Private and reserved IP ranges that http_get must never reach.
# Allowing connections to these would enable Server-Side Request Forgery (SSRF)
# attacks — e.g. a model could request http://169.254.169.254/latest/meta-data/
# to harvest cloud credentials, or http://localhost/admin to hit internal APIs.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),    # loopback
    ipaddress.ip_network("10.0.0.0/8"),     # RFC 1918 private
    ipaddress.ip_network("172.16.0.0/12"),  # RFC 1918 private
    ipaddress.ip_network("192.168.0.0/16"), # RFC 1918 private
    ipaddress.ip_network("169.254.0.0/16"), # link-local / AWS instance metadata
    ipaddress.ip_network("::1/128"),        # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),       # IPv6 unique local
]


def _is_private_host(hostname: str) -> bool:
    """
    Return True if *hostname* resolves to a private or reserved IP address.

    DNS resolution is performed so that hostnames like "internal-api.corp"
    that point to a 10.x.x.x address are blocked, not just bare IP literals.
    Returns False on DNS failure so the connection attempt can surface a
    descriptive error from urllib rather than a confusing security rejection.
    """
    if hostname.lower() in ("localhost", "metadata.google.internal"):
        return True
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(hostname))
        return any(addr in net for net in _BLOCKED_NETWORKS)
    except (socket.gaierror, ValueError):
        # DNS failure or unparseable address — let urllib report the real error.
        return False


class HttpGetTool(BaseTool):
    name = "http_get"
    description = (
        "Make an HTTP GET request and return the response body. "
        "Only http/https URLs are accepted; internal/private addresses are blocked."
    )

    def run(self, url: str = "", **kwargs) -> str:
        # Security: validate scheme and block SSRF before opening any connection.
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return (
                f"ERROR: Only http and https URLs are allowed "
                f"(got scheme '{parsed.scheme}')"
            )
        if not parsed.hostname:
            return "ERROR: URL must include a hostname"
        if _is_private_host(parsed.hostname):
            return (
                f"ERROR: Requests to private or internal addresses are not "
                f"allowed ({parsed.hostname})"
            )

        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.read().decode()[:2000]
        except Exception as e:
            return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# YOLO Tool
# ---------------------------------------------------------------------------

class YoloDetectTool(BaseTool):
    name = "yolo_detect"
    description = (
        "Run YOLO object detection on an image file. "
        "Returns a JSON array of detections, each with 'class', 'confidence', and 'box' (x1,y1,x2,y2). "
        "Args: path (str) — path to image file within the project directory."
    )

    def run(self, path: str = "", **kwargs) -> str:
        try:
            validated = _validate_path(path)
        except ValueError as e:
            return json.dumps({"error": str(e)})

        if not validated.exists():
            return json.dumps({"error": f"File not found: {path}"})

        try:
            from ultralytics import YOLO  # lazy import — only required if tool is used
        except ImportError:
            return json.dumps({"error": "ultralytics is not installed. Run: pip install ultralytics"})

        try:
            model = YOLO("yolo11n.pt")
            results = model(str(validated), verbose=False)
            detections = []
            for r in results:
                for box in r.boxes:
                    x1, y1, x2, y2 = [round(float(v), 1) for v in box.xyxy[0]]
                    detections.append({
                        "class": r.names[int(box.cls)],
                        "confidence": round(float(box.conf), 3),
                        "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                    })
            return json.dumps(detections)
        except Exception as e:
            return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Maps tool names to BaseTool instances and provides a describe() string
    that is injected into the model prompt each iteration.

    The five built-in tools are registered automatically at construction.
    To add a custom tool, call register() with an instance of your BaseTool
    subclass, then add it to config/rules.md so the model knows it exists.
    """

    def __init__(self, config: dict = None):
        self._tools: dict[str, BaseTool] = {}
        # Register defaults
        for tool_cls in [EchoTool, ShellTool, ReadFileTool, WriteFileTool, HttpGetTool, YoloDetectTool]:
            self.register(tool_cls())

    def register(self, tool: BaseTool):
        """Add a tool to the registry, keyed by tool.name."""
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def has(self, name: str) -> bool:
        """Return True if a tool with this name is registered."""
        return name in self._tools

    def call(self, name: str, args: dict) -> str:
        """
        Look up tool by name and call run(**args).

        Raises ValueError if the tool is not registered (caller should check
        has() first, but the error is catchable either way).
        """
        if not self.has(name):
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name].run(**args)

    def describe(self) -> str:
        """Return a bullet-list string of all registered tools for the model prompt."""
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.name}: {t.description}")
        return "\n".join(lines)
