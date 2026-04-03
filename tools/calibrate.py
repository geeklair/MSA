# calibrate.py — measure position units per degree
import asyncio
import os
from reolink_aio.api import Host

async def calibrate():
    host = Host(host=os.environ["REOLINK_IP"], username="admin", password=os.environ["REOLINK_PASSWORD"])
    await host.get_host_data()

    # Record starting position
    pan_start = host.ptz_pan_position(0)
    tilt_start = host.ptz_tilt_position(0)
    print(f"Start: pan={pan_start}, tilt={tilt_start}")

    # Pan right for 2 seconds at speed 10 (slow)
    await host.set_ptz_command(0, command="Right")
    await asyncio.sleep(2)
    await host.set_ptz_command(0, command="Stop")
    await asyncio.sleep(1)  # let it settle

    # Re-fetch position data
    await host.get_host_data()
    pan_end = host.ptz_pan_position(0)
    tilt_end = host.ptz_tilt_position(0)
    print(f"After pan right 2s: pan={pan_end}, tilt={tilt_end}")
    print(f"Pan delta: {pan_end - pan_start} units")

    await host.logout()

asyncio.run(calibrate())
