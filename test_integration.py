import urllib.parse
from physics import generate_drone_trajectory, full_engagement_analysis
from config import INTERCEPTOR_SYSTEMS, DEFAULT_INTERCEPTOR

# Simulate what /intercept does
seed = 12345
system_key = "rbs70"
system = INTERCEPTOR_SYSTEMS[system_key]
wx = {"windspeed_kmh": 10, "wind_direction_deg": 270, "precipitation": 0}
interceptor_pos = system.get("base", DEFAULT_INTERCEPTOR)

try:
    print("Generating trajectory...")
    traj = generate_drone_trajectory(seed)
    print(f"Generated {len(traj['waypoints'])} waypoints.")
    
    print("Running engagement analysis...")
    result = full_engagement_analysis(traj, system_key, interceptor_pos, wx)
    print(f"Engagement complete. Optimal recommendation: {result['recommendation']}")
    print("Success!")
except Exception as e:
    import traceback
    traceback.print_exc()
    print(f"FAILED: {e}")