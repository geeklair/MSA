"""
msa/tools.py — Tool registry and stub implementations.

Tools are the agent's "embodiment" — what it can actually DO in the world.
Add new tools by subclassing BaseTool and registering in ToolRegistry.
"""

import logging
import subprocess
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base Tool
# ---------------------------------------------------------------------------

class BaseTool(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    def run(self, **kwargs) -> str:
        pass

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
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            return result.stdout or result.stderr or "(no output)"
        except subprocess.TimeoutExpired:
            return "ERROR: Command timed out"
        except Exception as e:
            return f"ERROR: {e}"


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a file from disk and return its contents."

    def run(self, path: str = "", filename: str = "", **kwargs) -> str:
        path = path or filename
        try:
            with open(path) as f:
                return f.read()
        except Exception as e:
            return f"ERROR: {e}"


class WriteFileTool(BaseTool):
    name = "write_file"
    description = "Write content to a file on disk."

    def run(self, path: str = "", content: str = "", filename: str = "", **kwargs) -> str:
        path = path or filename
        try:
            with open(path, "w") as f:
                f.write(content)
            return f"Written to {path}"
        except Exception as e:
            return f"ERROR: {e}"


class HttpGetTool(BaseTool):
    name = "http_get"
    description = "Make an HTTP GET request and return the response body."

    def run(self, url: str = "", **kwargs) -> str:
        try:
            import urllib.request
            with urllib.request.urlopen(url, timeout=10) as r:
                return r.read().decode()[:2000]
        except Exception as e:
            return f"ERROR: {e}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    def __init__(self, config: dict = None):
        self._tools: dict[str, BaseTool] = {}
        # Register defaults
        for tool_cls in [EchoTool, ShellTool, ReadFileTool, WriteFileTool, HttpGetTool]:
            self.register(tool_cls())

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s", tool.name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def call(self, name: str, args: dict) -> str:
        if not self.has(name):
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name].run(**args)

    def describe(self) -> str:
        lines = []
        for t in self._tools.values():
            lines.append(f"- {t.name}: {t.description}")
        return "\n".join(lines)
