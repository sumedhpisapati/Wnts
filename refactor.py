import re

with open("physics.py", "r", encoding="utf-8") as f:
    code = f.read()

new_func = """def generate_drone_trajectory(seed, target_key=None, weather=None):
    \"\"\"
    Generates a realistic evasive drone trajectory within the scored BBOX using the
    new probabilistic route prediction engine (A* + Tactical behaviors).
    \"\"\"
    import random
    import route_prediction
    import behavior_models
    import intent_inference
    
    rng = random.Random(seed)
    S, N, W, E = BBOX

    SEA_ENTRIES = [
        {"lat": S+0.01, "lon": W+(E-W)*0.55, "label":"South approach"},
        {"lat": S+0.01, "lon": W+(E-W)*0.35, "label":"South-west approach"},
        {"lat": S+0.01, "lon": W+(E-W)*0.75, "label":"South-east approach"},
        {"lat": S+(N-S)*0.35, "lon": E-0.01, "label":"East approach"},
        {"lat": S+(N-S)*0.65, "lon": E-0.01, "label":"North-east approach"},
        {"lat": N-0.01, "lon": W+(E-W)*0.45, "label":"North approach"},
    ]

    ALL_IMPORTANT = [
        {"key":"naval_base", "name":"Naval Base Karlskrona",   "lat":56.1614,"lon":15.5869,"value":1.0},
        {"key":"ferry",      "name":"Ferry Terminal",           "lat":56.1607,"lon":15.5950,"value":0.8},
        {"key":"fort",       "name":"Kungsholms Fort",          "lat":56.1050,"lon":15.5906,"value":0.9},
        {"key":"radar",      "name":"Aspö Island Radar",        "lat":56.0700,"lon":15.7100,"value":0.85},
        {"key":"port",       "name":"Dragsö Industrial Port",   "lat":56.1750,"lon":15.6200,"value":0.7},
        {"key":"city",       "name":"City Centre",              "lat":56.1608,"lon":15.5865,"value":0.6},
        {"key":"stumholmen", "name":"Stumholmen Naval Museum",   "lat":56.1590,"lon":15.5920,"value":0.65},
        {"key":"power",      "name":"Power Substation Lyckeby", "lat":56.1820,"lon":15.5600,"value":0.55},
        {"key":"bergasa",    "name":"Bergåsa Industrial Zone",   "lat":56.1800,"lon":15.6100,"value":0.50},
        {"key":"rosenholm",  "name":"Rosenholm Port Area",       "lat":56.1650,"lon":15.6300,"value":0.55},
    ]

    drone_type_key = rng.choice(["tactical_isr", "male_strike", "cruise_missile", "fpv_kamikaze"])
    profile = behavior_models.DRONE_PROFILES.get(drone_type_key, behavior_models.DRONE_PROFILES["tactical_isr"])
    speed_ms = profile["speed_ms"]
    
    entry = rng.choice(SEA_ENTRIES)
    if target_key:
        target = next((t for t in ALL_IMPORTANT if t["key"] == target_key), ALL_IMPORTANT[0])
    else:
        target = rng.choices(ALL_IMPORTANT, weights=[t["value"] for t in ALL_IMPORTANT])[0]

    # Calculate initial heading
    initial_heading_rad = math.atan2(
        math.sin(math.radians(target["lon"] - entry["lon"])) * math.cos(math.radians(target["lat"])),
        math.cos(math.radians(entry["lat"])) * math.sin(math.radians(target["lat"])) -
        math.sin(math.radians(entry["lat"])) * math.cos(math.radians(target["lat"])) * math.cos(math.radians(target["lon"] - entry["lon"]))
    )
    initial_heading = (math.degrees(initial_heading_rad) + 360) % 360

    # 1. Use new route prediction engine to get base waypoints (A* on H3 grid)
    base_waypoints = route_prediction.a_star_route(
        entry["lat"], entry["lon"], target["lat"], target["lon"],
        res=9, profile=profile, weather=weather, initial_heading=initial_heading
    )

    # 2. Enrich waypoints with Altitude, Tactical Behavior, and Time
    wpts = []
    current_behavior = behavior_models.TacticalState.APPROACH
    current_time = 0.0
    total_dist = 0.0
    
    for i, wp in enumerate(base_waypoints):
        dist_to_target = haversine_m(wp["lat"], wp["lon"], target["lat"], target["lon"])
        cid = h3.latlng_to_cell(wp["lat"], wp["lon"], 9)
        props = state.GRID_INDEX.get(cid, {})
        
        # Determine next tactical behavior
        current_behavior = behavior_models.TacticalState.transition(current_behavior, props, dist_to_target)
        
        # Determine altitude based on behavior
        alt = profile["min_alt_m"] + 100
        if current_behavior == behavior_models.TacticalState.TERRAIN_MASK:
            alt = profile["min_alt_m"] + 20
        elif current_behavior == behavior_models.TacticalState.TERMINAL:
            alt = max(30, profile["min_alt_m"])
        
        # Add small randomness to alt
        alt += rng.uniform(-10, 10)
        alt = max(10, alt)

        # Calculate time
        if i == 0:
            step_dist = 0.0
        else:
            prev_wp = wpts[-1]
            # 3D distance
            h_dist = haversine_m(prev_wp["lat"], prev_wp["lon"], wp["lat"], wp["lon"])
            v_dist = abs(alt - prev_wp["alt_m"])
            step_dist = math.sqrt(h_dist**2 + v_dist**2)
            total_dist += step_dist
            
            # Simple speed: assume constant speed for now, or slow down in turns
            current_time += step_dist / speed_ms

        wpts.append({
            "lat": wp["lat"],
            "lon": wp["lon"],
            "alt_m": int(alt),
            "label": f"WP {i} ({current_behavior})",
            "behavior": current_behavior,
            "t": 0 # This 't' is for the physics interpolation later, which ignores it or recalculates it
        })

    # The existing physics logic expects 't' in the output dict but 
    # interpolate_trajectory uses cumulative distance. We leave 't' as 0 in waypoints.
    
    route_desc = f"via tactical A*"
    
    return {
        "seed":         seed,
        "type":         profile.get("notes", drone_type_key),
        "pattern":      "probabilistic_a_star",
        "target":       target,
        "entry":        entry,
        "via_regions":  [],
        "waypoints":    wpts,
        "speed_ms":     speed_ms,
        "speed_kmh":    round(speed_ms * 3.6),
        "n_waypoints":  len(wpts),
        "total_dist_m": round(total_dist),
        "total_time_s": round(current_time),
        "from_lat":     entry["lat"],
        "from_lon":     entry["lon"],
        "to_lat":       target["lat"],
        "to_lon":       target["lon"],
        "description":  (f"[{drone_type_key.upper()}] "
                         f"{entry['label']} {route_desc} → {target['name']} | "
                         f"{speed_ms * 3.6:.0f}km/h | {total_dist/1000:.1f}km"),
        "assumptions":  f"Speed={speed_ms * 3.6:.0f}km/h, Probabilistic A* routing, Tactical Behaviors",
    }
"""

# Regex to match from def generate_drone_trajectory(seed, target_key=None): to the end of the dictionary return
pattern = re.compile(r"def generate_drone_trajectory\(seed, target_key=None\):.*?return \{\n.*?\n    \}", re.DOTALL)

new_code = pattern.sub(new_func, code)

with open("physics.py", "w", encoding="utf-8") as f:
    f.write(new_code)

print("Replaced generate_drone_trajectory in physics.py")
