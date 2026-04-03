import asyncio
import os
from reolink_aio.api import Host

async def test_camera():
    host = Host(
        host=os.environ["REOLINK_IP"],
        username="admin",
        password=os.environ["REOLINK_PASSWORD"]
    )
    
    await host.get_host_data()
    
    print(f"Model: {host.camera_model(0)}")
    print(f"Firmware: {host.camera_sw_version(0)}")
    print(f"Channels: {host.num_channels}")
    print(f"PTZ supported: {host.supported(0, 'pan_tilt')}")

    # Try getting current PTZ position
    try:
        pan = host.ptz_pan_position(0)
        tilt = host.ptz_tilt_position(0)
        print(f"Current PTZ position: pan={pan}, tilt={tilt}")
    except Exception as e:
        print(f"PTZ position query: {e}")

    # Try grabbing a snapshot
    try:
        img = await host.get_snapshot(0)
        if img:
            with open("test_snap.jpg", "wb") as f:
                f.write(img)
            print(f"Snapshot saved: test_snap.jpg ({len(img)} bytes)")
        else:
            print("Snapshot: returned None")
    except Exception as e:
        print(f"Snapshot: {e}")

    await host.logout()

asyncio.run(test_camera())
