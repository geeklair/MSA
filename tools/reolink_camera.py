"""
reolink_camera.py — MSA tool for controlling a Reolink PTZ camera.

Loads calibration from calibration.json (project root).
Credentials via env vars: REOLINK_HOST, REOLINK_USER, REOLINK_PASS.

Designed to be called from the MSA agent as an async context manager:

    async with ReolinkCamera() as cam:
        await cam.pan(15)
        path = await cam.snapshot()

Or from the command line:

    python -m tools.reolink_camera pan 10
    python -m tools.reolink_camera tilt -5
    python -m tools.reolink_camera move_to 90 25
    python -m tools.reolink_camera get_position
    python -m tools.reolink_camera snapshot [filename]
    python -m tools.reolink_camera look_at_preset 1
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from reolink_aio.api import Host

# ---------------------------------------------------------------------------
# Calibration / credential defaults
# ---------------------------------------------------------------------------

_CAL_PATH = Path(__file__).parent / "calibration.json"

REOLINK_HOST = os.environ.get("REOLINK_IP", "")
REOLINK_USER = os.environ.get("REOLINK_USER", "admin")
REOLINK_PASS = os.environ.get("REOLINK_PASSWORD", "")

POLL_INTERVAL = 0.08   # seconds between position polls (fast — uses lightweight GetPtzCurPos)
MOVE_TIMEOUT  = 15.0   # seconds before giving up on a move
SETTLE_UNITS  = 4      # consider settled when within this many units of target
COAST_UNITS   = 18     # send Stop this many units early to account for motor coast


# ---------------------------------------------------------------------------
# Camera class
# ---------------------------------------------------------------------------

class ReolinkCamera:
    """
    Async context manager that wraps a reolink_aio Host connection and exposes
    a degree-based PTZ API calibrated from calibration.json.

    Pan:  0° = hard-left limit, 355° = hard-right limit
          positive degrees = right, negative = left
    Tilt: 0° = hard-down limit, 50° = hard-up limit
          positive degrees = up, negative = down
    """

    def __init__(self, calibration_path: Path = _CAL_PATH):
        with open(calibration_path) as f:
            cal = json.load(f)

        # Pan: internal units decrease as camera moves right (inverted).
        # We expose a natural API (positive = right) and handle inversion here.
        self._pan_min_units  = cal["pan_min"]   # 908  (hard-left)
        self._pan_max_units  = cal["pan_max"]   # 0    (hard-right)
        self._pan_upd        = abs(cal["pan_units_per_degree"])  # 2.56
        self._pan_inverted   = cal["pan_units_per_degree"] < 0   # True

        self._tilt_min_units = cal["tilt_min"]  # 0    (hard-down)
        self._tilt_max_units = cal["tilt_max"]  # 112  (hard-up)
        self._tilt_upd       = cal["tilt_units_per_degree"]      # 2.24

        self._pan_range_deg  = cal["pan_degrees"]   # 355
        self._tilt_range_deg = cal["tilt_degrees"]  # 50

        self._host: Host | None = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self):
        if self._host is None:
            self._host = Host(
                host=REOLINK_HOST,
                username=REOLINK_USER,
                password=REOLINK_PASS,
            )
            await self._host.get_host_data()

    async def disconnect(self):
        if self._host is not None:
            await self._host.logout()
            self._host = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.disconnect()

    # ------------------------------------------------------------------
    # Unit conversion helpers
    # ------------------------------------------------------------------

    def _pan_units_to_deg(self, units: int) -> float:
        """Convert raw pan position units to degrees (0° = hard-left)."""
        if self._pan_inverted:
            return (self._pan_min_units - units) / self._pan_upd
        return (units - self._pan_min_units) / self._pan_upd

    def _pan_deg_to_units(self, deg: float) -> int:
        """Convert pan degrees to raw position units."""
        deg = max(0.0, min(self._pan_range_deg, deg))
        if self._pan_inverted:
            return round(self._pan_min_units - deg * self._pan_upd)
        return round(self._pan_min_units + deg * self._pan_upd)

    def _tilt_units_to_deg(self, units: int) -> float:
        """Convert raw tilt position units to degrees (0° = hard-down)."""
        return (units - self._tilt_min_units) / self._tilt_upd

    def _tilt_deg_to_units(self, deg: float) -> int:
        """Convert tilt degrees to raw position units."""
        deg = max(0.0, min(self._tilt_range_deg, deg))
        return round(self._tilt_min_units + deg * self._tilt_upd)

    # ------------------------------------------------------------------
    # Low-level position fetch
    # ------------------------------------------------------------------

    async def _fetch_position(self) -> tuple[int, int]:
        """Lightweight position fetch via GetPtzCurPos (Baichuan channel)."""
        await self._host.baichuan.get_ptz_position(0)
        pu = self._host.ptz_pan_position(0)
        tu = self._host.ptz_tilt_position(0)
        return pu, tu

    # ------------------------------------------------------------------
    # Low-level move primitive
    # ------------------------------------------------------------------

    async def _move_axis(
        self,
        command_fwd: str,
        command_rev: str,
        axis: str,            # "pan" or "tilt"
        target_units: int,
        target_deg: float,
    ) -> int:
        """
        Move one axis toward target_units.

        Sends command_fwd when we need to increase units, command_rev to
        decrease.  Polls at POLL_INTERVAL, sends Stop COAST_UNITS before the
        target, then waits for settle.  Returns final position in units.
        """
        pu, tu = await self._fetch_position()
        current_units = pu if axis == "pan" else tu
        delta = target_units - current_units

        if abs(delta) <= SETTLE_UNITS:
            return current_units

        # Positive delta → need to increase units → use command_fwd
        cmd = command_fwd if delta > 0 else command_rev
        # Stop early enough to account for motor coast, but never more than half the move.
        stop_threshold = max(SETTLE_UNITS, min(COAST_UNITS, abs(delta) // 2))

        await self._host.set_ptz_command(0, command=cmd)
        stopped = False
        stable_count = 0        # consecutive polls with no position change after stop
        last_units = current_units
        deadline = time.monotonic() + MOVE_TIMEOUT

        while time.monotonic() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            pu, tu = await self._fetch_position()
            current_units = pu if axis == "pan" else tu
            current_deg = (self._pan_units_to_deg(current_units) if axis == "pan"
                           else self._tilt_units_to_deg(current_units))

            remaining = target_units - current_units
            print(f"  {axis}: {current_deg:.1f}°  (target {target_deg:.1f}°, "
                  f"Δ={remaining:+d} units)   ", end="\r", flush=True)

            if not stopped:
                # Stop early to account for motor coast
                if abs(remaining) <= stop_threshold:
                    await self._host.set_ptz_command(0, command="Stop")
                    stopped = True
                    stable_count = 0
                # Hard overshoot detection
                elif (delta > 0 and current_units > target_units + SETTLE_UNITS) or \
                     (delta < 0 and current_units < target_units - SETTLE_UNITS):
                    await self._host.set_ptz_command(0, command="Stop")
                    stopped = True
                    stable_count = 0
            else:
                # After stop: count how many polls position is unchanged
                if current_units == last_units:
                    stable_count += 1
                else:
                    stable_count = 0
                # Exit when within settle zone OR position has been stable for 3 polls
                if abs(remaining) <= SETTLE_UNITS or stable_count >= 3:
                    break

            last_units = current_units

        if not stopped:
            await self._host.set_ptz_command(0, command="Stop")

        # Brief settle wait after stop
        await asyncio.sleep(0.4)
        pu, tu = await self._fetch_position()
        return pu if axis == "pan" else tu

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_position(self) -> dict:
        """
        Return the current camera position in degrees.

        Returns a dict with:
            pan_deg:  0 = hard-left limit, 355 = hard-right limit
            tilt_deg: 0 = hard-down limit,  50 = hard-up limit
        """
        await self.connect()
        pu, tu = await self._fetch_position()
        return {
            "pan_deg":  round(self._pan_units_to_deg(pu),  1),
            "tilt_deg": round(self._tilt_units_to_deg(tu), 1),
        }

    async def pan(self, degrees: float) -> dict:
        """
        Pan the camera by the given number of degrees relative to current position.

        Positive = right, negative = left.
        Clamped so the result stays within [0°, 355°].

        Returns a dict with:
            pan_deg: new absolute pan position in degrees
        """
        await self.connect()
        pu, _tu = await self._fetch_position()
        current_deg = self._pan_units_to_deg(pu)
        target_deg  = max(0.0, min(self._pan_range_deg, current_deg + degrees))
        target_units = self._pan_deg_to_units(target_deg)

        direction = "right" if degrees >= 0 else "left"
        print(f"Panning {direction} {abs(degrees):.1f}°  "
              f"({current_deg:.1f}° → {target_deg:.1f}°)")

        # Pan is inverted: "Right" decreases units, "Left" increases units.
        # _move_axis uses command_fwd when delta > 0 (units need to increase).
        final_units = await self._move_axis(
            command_fwd="Left",
            command_rev="Right",
            axis="pan",
            target_units=target_units,
            target_deg=target_deg,
        )

        final_deg = self._pan_units_to_deg(final_units)
        print(f"\nPan done. Position: {final_deg:.1f}°")
        return {"pan_deg": round(final_deg, 1)}

    async def tilt(self, degrees: float) -> dict:
        """
        Tilt the camera by the given number of degrees relative to current position.

        Positive = up, negative = down.
        Clamped so the result stays within [0°, 50°].

        Returns a dict with:
            tilt_deg: new absolute tilt position in degrees
        """
        await self.connect()
        _pu, tu = await self._fetch_position()
        current_deg  = self._tilt_units_to_deg(tu)
        target_deg   = max(0.0, min(self._tilt_range_deg, current_deg + degrees))
        target_units = self._tilt_deg_to_units(target_deg)

        direction = "up" if degrees >= 0 else "down"
        print(f"Tilting {direction} {abs(degrees):.1f}°  "
              f"({current_deg:.1f}° → {target_deg:.1f}°)")

        final_units = await self._move_axis(
            command_fwd="Up",
            command_rev="Down",
            axis="tilt",
            target_units=target_units,
            target_deg=target_deg,
        )

        final_deg = self._tilt_units_to_deg(final_units)
        print(f"\nTilt done. Position: {final_deg:.1f}°")
        return {"tilt_deg": round(final_deg, 1)}

    async def move_to(self, pan_degrees: float, tilt_degrees: float) -> dict:
        """
        Move the camera to an absolute position in degrees.

        pan_degrees:  0 = hard-left limit, 355 = hard-right limit
        tilt_degrees: 0 = hard-down limit,  50 = hard-up limit

        Moves pan first, then tilt (this model does not support simultaneous
        pan+tilt via a single command).

        Returns a dict with:
            pan_deg: final pan position in degrees
            tilt_deg: final tilt position in degrees
        """
        await self.connect()
        pan_degrees  = max(0.0, min(self._pan_range_deg,  pan_degrees))
        tilt_degrees = max(0.0, min(self._tilt_range_deg, tilt_degrees))

        pu, tu = await self._fetch_position()
        current_pan_deg  = self._pan_units_to_deg(pu)
        current_tilt_deg = self._tilt_units_to_deg(tu)
        print(f"Moving to pan={pan_degrees:.1f}°, tilt={tilt_degrees:.1f}°  "
              f"(from pan={current_pan_deg:.1f}°, tilt={current_tilt_deg:.1f}°)")

        pan_result  = await self.pan(pan_degrees - current_pan_deg)
        tilt_result = await self.tilt(tilt_degrees - current_tilt_deg)

        return {"pan_deg": pan_result["pan_deg"], "tilt_deg": tilt_result["tilt_deg"]}

    async def snapshot(self, filename: str | None = None) -> str:
        """
        Capture a JPEG snapshot from the camera and save it to disk.

        Args:
            filename: Optional output path. If omitted, saves as
                      snapshot_YYYYMMDD_HHMMSS.jpg in the current directory.

        Returns:
            Absolute path to the saved JPEG file.
        """
        await self.connect()
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"snapshot_{ts}.jpg"
        path = Path(filename).resolve()
        print(f"Capturing snapshot → {path}")
        img = await self._host.get_snapshot(0)
        if img is None:
            raise RuntimeError("Camera returned no image data")
        path.write_bytes(img)
        print(f"Snapshot saved ({len(img):,} bytes)")
        return str(path)

    async def look_at_preset(self, preset_id: int) -> dict:
        """
        Move the camera to a predefined PTZ preset by ID.

        Preset IDs are configured in the camera's firmware/web interface.
        The move completes asynchronously in the camera; this method waits
        3 seconds then reads back the final position.

        Args:
            preset_id: Integer preset ID.

        Returns a dict with:
            pan_deg: final pan position in degrees
            tilt_deg: final tilt position in degrees
        """
        await self.connect()
        presets = self._host.ptz_presets(0)
        print(f"Available presets: {presets}")
        print(f"Moving to preset {preset_id}...")
        await self._host.set_ptz_command(0, preset=preset_id)
        await asyncio.sleep(3)
        pu, tu = await self._fetch_position()
        pan_deg  = self._pan_units_to_deg(pu)
        tilt_deg = self._tilt_units_to_deg(tu)
        print(f"Preset reached. Position: pan={pan_deg:.1f}°, tilt={tilt_deg:.1f}°")
        return {"pan_deg": round(pan_deg, 1), "tilt_deg": round(tilt_deg, 1)}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def _main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd, *rest = args

    async with ReolinkCamera() as cam:
        if cmd == "pan":
            print(await cam.pan(float(rest[0])))

        elif cmd == "tilt":
            print(await cam.tilt(float(rest[0])))

        elif cmd == "move_to":
            print(await cam.move_to(float(rest[0]), float(rest[1])))

        elif cmd == "get_position":
            print(await cam.get_position())

        elif cmd == "snapshot":
            print(await cam.snapshot(rest[0] if rest else None))

        elif cmd == "look_at_preset":
            print(await cam.look_at_preset(int(rest[0])))

        else:
            print(f"Unknown command: {cmd}")
            print("Commands: pan, tilt, move_to, get_position, snapshot, look_at_preset")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_main())
