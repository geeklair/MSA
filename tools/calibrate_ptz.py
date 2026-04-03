# calibrate_ptz.py — measure PTZ range and units-per-degree for Reolink E1 Outdoor SE PoE
import asyncio
import json
import os
from reolink_aio.api import Host

REOLINK_HOST = os.environ.get("REOLINK_IP", "")
REOLINK_USER = os.environ.get("REOLINK_USER", "admin")
REOLINK_PASS = os.environ.get("REOLINK_PASSWORD", "")

PAN_DEGREES  = 355.0
TILT_DEGREES =  50.0


async def move_and_read(host, command, duration, label):
    print(f"  Moving {command} for {duration}s...")
    await host.set_ptz_command(0, command=command)
    await asyncio.sleep(duration)
    await host.set_ptz_command(0, command="Stop")
    await asyncio.sleep(1)  # let motor settle
    await host.get_host_data()
    pan  = host.ptz_pan_position(0)
    tilt = host.ptz_tilt_position(0)
    print(f"  {label}: pan={pan}, tilt={tilt}")
    return pan, tilt


async def calibrate():
    print("Connecting to camera...")
    host = Host(host=REOLINK_HOST, username=REOLINK_USER, password=REOLINK_PASS)
    await host.get_host_data()

    pan0  = host.ptz_pan_position(0)
    tilt0 = host.ptz_tilt_position(0)
    print(f"Start position: pan={pan0}, tilt={tilt0}\n")

    # --- Short symmetry test ---
    print("=== Pan symmetry test (2 s each) ===")
    pan_r, _ = await move_and_read(host, "Right", 2, "After Right 2s")
    print(f"  Pan delta Right: {pan_r - pan0:+d} units")

    pan_l, _ = await move_and_read(host, "Left",  2, "After Left 2s")
    print(f"  Pan delta Left (from Right pos): {pan_l - pan_r:+d} units\n")

    print("=== Tilt symmetry test (2 s each) ===")
    _, tilt_u = await move_and_read(host, "Up",   2, "After Up 2s")
    print(f"  Tilt delta Up: {tilt_u - tilt0:+d} units")

    _, tilt_d = await move_and_read(host, "Down", 2, "After Down 2s")
    print(f"  Tilt delta Down (from Up pos): {tilt_d - tilt_u:+d} units\n")

    # --- Full range sweep ---
    print("=== Pan full range ===")
    print("  Driving all the way Left (15 s)...")
    pan_min, _ = await move_and_read(host, "Left",  15, "Hard-left position")

    print("  Driving all the way Right (15 s)...")
    pan_max, _ = await move_and_read(host, "Right", 15, "Hard-right position")

    pan_range = pan_max - pan_min
    pan_upd   = pan_range / PAN_DEGREES
    print(f"  Pan range: {pan_min} → {pan_max}  ({pan_range} units over {PAN_DEGREES}°)")
    print(f"  Pan units/degree: {pan_upd:.2f}\n")

    print("=== Tilt full range ===")
    print("  Driving all the way Down (15 s)...")
    _, tilt_min = await move_and_read(host, "Down", 15, "Hard-down position")

    print("  Driving all the way Up (15 s)...")
    _, tilt_max = await move_and_read(host, "Up",   15, "Hard-up position")

    tilt_range = tilt_max - tilt_min
    tilt_upd   = tilt_range / TILT_DEGREES
    print(f"  Tilt range: {tilt_min} → {tilt_max}  ({tilt_range} units over {TILT_DEGREES}°)")
    print(f"  Tilt units/degree: {tilt_upd:.2f}\n")

    # --- Save results ---
    results = {
        "pan_min": pan_min,
        "pan_max": pan_max,
        "pan_range": pan_range,
        "pan_degrees": PAN_DEGREES,
        "pan_units_per_degree": pan_upd,
        "tilt_min": tilt_min,
        "tilt_max": tilt_max,
        "tilt_range": tilt_range,
        "tilt_degrees": TILT_DEGREES,
        "tilt_units_per_degree": tilt_upd,
    }
    with open("calibration.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Calibration saved to calibration.json")
    print(json.dumps(results, indent=2))

    await host.logout()


asyncio.run(calibrate())
