"""
camera_chat.py — Natural-language REPL for Reolink PTZ camera control.

Usage:
    python -m tools.camera_chat

Connects to the camera, then accepts plain-English commands like:
    "pan right 20 degrees"
    "take a picture"
    "move to 180 degrees pan, 25 tilt"
    "pan left 10 and take a snapshot"

Type "quit" or "exit" to disconnect and exit.
"""

import asyncio
import os
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.reolink_camera import ReolinkCamera

# ---------------------------------------------------------------------------
# Tool definitions for the Anthropic API
# ---------------------------------------------------------------------------

TOOLS: list[anthropic.types.ToolParam] = [
    {
        "name": "get_position",
        "description": (
            "Return the current camera position in degrees. "
            "pan_deg: 0 = hard-left limit, 355 = hard-right limit. "
            "tilt_deg: 0 = hard-down limit, 50 = hard-up limit."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "pan",
        "description": (
            "Pan the camera by a relative number of degrees. "
            "Positive degrees = right, negative degrees = left. "
            "Clamped so the result stays within [0°, 355°]. "
            "Returns the new absolute pan position in degrees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "degrees": {
                    "type": "number",
                    "description": "Degrees to pan. Positive = right, negative = left.",
                }
            },
            "required": ["degrees"],
        },
    },
    {
        "name": "tilt",
        "description": (
            "Tilt the camera by a relative number of degrees. "
            "Positive degrees = up, negative degrees = down. "
            "Clamped so the result stays within [0°, 50°]. "
            "Returns the new absolute tilt position in degrees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "degrees": {
                    "type": "number",
                    "description": "Degrees to tilt. Positive = up, negative = down.",
                }
            },
            "required": ["degrees"],
        },
    },
    {
        "name": "move_to",
        "description": (
            "Move the camera to an absolute position in degrees. "
            "pan_degrees: 0 = hard-left limit, 355 = hard-right limit. "
            "tilt_degrees: 0 = hard-down limit, 50 = hard-up limit. "
            "Moves pan first, then tilt. "
            "Returns the final pan and tilt position in degrees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pan_degrees": {
                    "type": "number",
                    "description": "Absolute pan target in degrees [0, 355].",
                },
                "tilt_degrees": {
                    "type": "number",
                    "description": "Absolute tilt target in degrees [0, 50].",
                },
            },
            "required": ["pan_degrees", "tilt_degrees"],
        },
    },
    {
        "name": "snapshot",
        "description": (
            "Capture a JPEG snapshot from the camera and save it to disk. "
            "Returns the absolute file path of the saved image."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": (
                        "Optional output filename. If omitted, a timestamped "
                        "name like snapshot_20260402_153000.jpg is used."
                    ),
                }
            },
            "required": [],
        },
    },
    {
        "name": "look_at_preset",
        "description": (
            "Move the camera to a predefined PTZ preset by integer ID. "
            "Preset positions are configured in the camera firmware. "
            "Returns the final pan and tilt position in degrees."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "preset_id": {
                    "type": "integer",
                    "description": "Integer preset ID as configured in the camera.",
                }
            },
            "required": ["preset_id"],
        },
    },
]

SYSTEM_PROMPT = """You are a camera control assistant for a Reolink E1 Outdoor SE PoE PTZ camera.

The camera has:
- 355° horizontal pan range (pan_deg: 0 = hard-left, 355 = hard-right)
- 50° vertical tilt range  (tilt_deg: 0 = hard-down, 50 = hard-up)

When the user gives a natural-language command, translate it into one or more tool calls.
For compound commands like "pan right 20 and take a picture", issue both tool calls.
For ambiguous directions ("look left", "look up"), use pan/tilt with a sensible default
amount (e.g., 15° for vague movement commands).

Always use tool calls — do not describe what you would do, just do it.
If the user asks a question about the camera or its state, call get_position first,
then answer using the result. If the user asks something unrelated to the camera,
answer directly in text without tool calls."""


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

async def dispatch(cam: ReolinkCamera, tool_name: str, tool_input: dict) -> str:
    """Execute a tool call against the camera and return a string result."""
    if tool_name == "get_position":
        result = await cam.get_position()
        return f"pan={result['pan_deg']}°, tilt={result['tilt_deg']}°"

    elif tool_name == "pan":
        result = await cam.pan(float(tool_input["degrees"]))
        pos = await cam.get_position()
        return f"pan done → pan={result['pan_deg']}°  (tilt={pos['tilt_deg']}°)"

    elif tool_name == "tilt":
        result = await cam.tilt(float(tool_input["degrees"]))
        pos = await cam.get_position()
        return f"tilt done → tilt={result['tilt_deg']}°  (pan={pos['pan_deg']}°)"

    elif tool_name == "move_to":
        result = await cam.move_to(
            float(tool_input["pan_degrees"]),
            float(tool_input["tilt_degrees"]),
        )
        return f"moved to pan={result['pan_deg']}°, tilt={result['tilt_deg']}°"

    elif tool_name == "snapshot":
        filename = tool_input.get("filename") or None
        path = await cam.snapshot(filename)
        size = Path(path).stat().st_size
        return f"snapshot saved: {path}  ({size:,} bytes)"

    elif tool_name == "look_at_preset":
        result = await cam.look_at_preset(int(tool_input["preset_id"]))
        return f"preset reached → pan={result['pan_deg']}°, tilt={result['tilt_deg']}°"

    else:
        return f"unknown tool: {tool_name}"


# ---------------------------------------------------------------------------
# REPL
# ---------------------------------------------------------------------------

async def repl():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("Connecting to camera...")
    async with ReolinkCamera() as cam:
        pos = await cam.get_position()
        print(f"Connected. Position: pan={pos['pan_deg']}°, tilt={pos['tilt_deg']}°")
        print("Type a command in plain English, or 'quit' to exit.\n")

        while True:
            try:
                user_input = input("camera> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nDisconnecting...")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                print("Disconnecting...")
                break

            # Ask the model to parse the command into tool calls
            try:
                response = client.messages.create(
                    model="claude-sonnet-4-20250514",
                    max_tokens=1024,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=[{"role": "user", "content": user_input}],
                )
            except anthropic.APIError as e:
                print(f"API error: {e}")
                continue

            # Process all content blocks in order
            for block in response.content:
                if block.type == "text" and block.text:
                    print(block.text)

                elif block.type == "tool_use":
                    print(f"[{block.name}({_fmt_args(block.input)})]")
                    try:
                        result = await dispatch(cam, block.name, block.input)
                        print(f"  → {result}")
                    except Exception as e:
                        print(f"  ✗ {e}")

            print()


def _fmt_args(args: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.items())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(repl())
