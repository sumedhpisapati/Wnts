import urllib.parse
from engine.physics import generate_drone_trajectory
from core.config import INTERCEPTOR_SYSTEMS, DEFAULT_INTERCEPTOR
import time

seed = 1000
wx = {"windspeed_kmh": 10, "wind_direction_deg": 270, "precipitation": 0}

for i in range(100):
    try:
        traj = generate_drone_trajectory(seed + i)
    except Exception as e:
        print(f"  -> Failed at {seed+i}: {e}")
