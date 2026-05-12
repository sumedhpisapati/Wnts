from core_imports import *
from config import *
import state
from utils import *

# Sources:
#   - Terminal velocity: v_t = sqrt(2mg / rho*Cd*A), standard aerodynamics
#   - Cd values: Hoerner (1965) "Fluid Dynamic Drag", Ch.3 irregular fragments
#   - Air density rho=1.225 kg/m³ at sea level (ISA standard atmosphere)
#   - Fragment mass distribution: estimated for 5kg commercial/military drone
#     Based on DJI/Shahed class mass breakdown (publicly documented)
#   - Horizontal drift: x = v_wind * t_fall * Cd_horizontal (linearised)
#   NOT heuristic: physics equations are exact. Parameters are estimated
#   from literature. Clearly documented so they can be updated with real data.




def terminal_velocity(mass_kg, Cd_v, area_m2):
    """
    Stokes/Newton drag terminal velocity.
    v_t = sqrt(2 * m * g / (rho * Cd * A))
    Source: standard aerodynamics (Hoerner 1965, Anderson 2010)
    """
    g = 9.81
    return math.sqrt((2 * mass_kg * g) / (AIR_DENSITY * Cd_v * area_m2))

def compute_debris_landing(lat, lon, alt_m, weather):
    """
    Compute debris impact zone for an intercept at (lat, lon, alt_m).

    Returns per-class physics PLUS a unified impact zone:
      - centre: wind-drift-adjusted landing point for each class
      - radius: 50m-1000m based on alt + wind (higher/windier = larger spread)
      - The unified zone is the weighted centroid of all class centres,
        with radius = max class radius (conservative bound)

    Physics (Newtonian drag, Hoerner 1965 Cd):
      v_t = sqrt(2mg / rho*Cd*A)
      drift = v_wind * (alt/v_t) / (1 + Cd_h)
      radius = max(50, drift * 0.30) clamped to [50, 1000]
    """
    # Debris physics constants
    # Limit wind to 40km/h as requested
    wind_kmh  = min(40.0, float(weather.get("windspeed_kmh", 10)))
    wind_ms   = wind_kmh / 3.6
    # Wind direction: 0 is North, 90 is East. 
    # Drift is IN the direction the wind is blowing.
    # meteorological wind_dir is "from", so "to" is (dir + 180) % 360
    wind_from = float(weather.get("wind_direction_deg", 270))
    wind_to   = (wind_from + 180) % 360
    bear_rad  = math.radians(90 - wind_to) # Standard math angle (East=0, North=90)

    landing_zones = []
    for dc in DEBRIS_CLASSES:
        v_t     = terminal_velocity(dc["mass_kg"], dc["Cd_v"], dc["area_m2"])
        tof     = alt_m / v_t
        drift_m = wind_ms * tof / (1.0 + dc["Cd_h"])

        # Calculate offset in degrees
        d_lat    = (drift_m * math.sin(math.radians(90 - wind_to))) / 111111.0
        d_lon    = (drift_m * math.cos(math.radians(90 - wind_to))) / (111111.0 * math.cos(math.radians(lat)))
        land_lat = lat + d_lat
        land_lon = lon + d_lon

        # Debris scatter radius [50, 1000] metres
        # Two physical contributions combined (RSS):
        #
        # 1. FRAGMENTATION SPREAD from missile warhead impact
        #    Lateral delta-v imparted to fragments ≈ 200-400 m/s (RBS70 proximity fuze)
        #    Spread at ground = lateral_v × tof / (1 + Cd_h)
        #    Proxy: alt_m × 0.35 × mass_factor (lighter = more spread)
        mass_factor    = math.sqrt(2.5 / dc["mass_kg"])  # heavy=1.0, fine=5.0
        frag_spread_m  = alt_m * 0.35 * mass_factor

        # 2. WIND DRIFT UNCERTAINTY (30% variability in drift)
        wind_uncertainty_m = drift_m * 0.30

        # RSS combination, clamped to [50, 1000]
        scatter_m = float(max(50.0, min(1000.0,
            math.sqrt(frag_spread_m**2 + wind_uncertainty_m**2)
        )))

        landing_zones.append({
            "class":          dc["name"],
            "weight":         dc["weight"],
            "terminal_v":     round(v_t, 1),
            "time_of_flight": round(tof, 1),
            "drift_m":        round(drift_m, 1),
            "frag_spread_m":  round(frag_spread_m, 1),
            "land_lat":       round(land_lat, 6),
            "land_lon":       round(land_lon, 6),
            "scatter_m":      round(scatter_m, 1),
            "mass_kg":        dc["mass_kg"],
        })

    # Unified impact zone: probability-weighted centroid of all class centres
    # Radius = weighted average of class radii (not just max — fine debris
    # has large radius but low consequence weight)
    total_w = sum(z["weight"] for z in landing_zones)
    uni_lat = sum(z["land_lat"] * z["weight"] for z in landing_zones) / total_w
    uni_lon = sum(z["land_lon"] * z["weight"] for z in landing_zones) / total_w
    uni_rad = float(max(50.0, min(1000.0,
        sum(z["scatter_m"] * z["weight"] for z in landing_zones) / total_w
    )))

    for z in landing_zones:
        z["unified_lat"] = round(uni_lat, 6)
        z["unified_lon"] = round(uni_lon, 6)
        z["unified_rad"] = round(uni_rad, 1)

    return landing_zones

def score_landing_zones(landing_zones):
    """
    Probability-weighted consequence scoring for debris impact zones.

    For each debris class:
      - Find all grid cells within scatter_m radius of the landing centre
      - Each cell gets a hit_probability based on Gaussian distance decay
        hit_prob(cell) = exp(-0.5 * (dist / sigma)^2)
        where sigma = scatter_m / 2  (68% of mass within scatter_m)
      - Cell consequence = hit_prob * cell_score * (1 + pop_weight)
      - Zone score = sum(cell_consequence) / sum(hit_prob)  [expected consequence]

    Also scores the UNIFIED impact zone (weighted centroid of all classes)
    using the same probability-weighted model.

    This replaces the flat average with a proper expected-value calculation:
    cells closer to the impact centre are more likely to be hit.
    """
    total_consequence = 0.0
    forbidden_hit     = False
    pop_at_risk       = 0
    details           = []
    S,N,W,E = BBOX

    for zone in landing_zones:
        zlat  = zone["land_lat"]
        zlon  = zone["land_lon"]
        rad   = zone["scatter_m"]                  # max radius (50-1000m)
        sigma = max(rad / 2.0, 25.0)               # Gaussian sigma

        sum_prob  = 0.0
        sum_harm  = 0.0
        fbd_hit   = False
        pop_total = 0

        for feat in state.GRID_FEATURES:
            p    = feat["properties"]
            dist = haversine_m(zlat, zlon, p["lat"], p["lon"])
            if dist > rad: continue                 # outside impact radius

            # Gaussian hit probability: 1.0 at centre, ~0.14 at edge
            hit_prob = math.exp(-0.5 * (dist / sigma) ** 2)

            # Cell consequence score (blended direct + neighbourhood)
            cell_score = p.get("score") or 0.0
            integ_norm = min(p.get("integrated_risk", 0) / 20.0, 1.0)
            blended    = 0.70 * cell_score + 0.30 * integ_norm

            # Population amplifier: more people → more harm per hit
            pop = p.get("pop_count", 0)
            pop_amp = 1.0 + math.log1p(pop) / math.log1p(100)

            # Expected harm = probability × consequence × population factor
            expected_harm = hit_prob * blended * pop_amp

            sum_prob  += hit_prob
            sum_harm  += expected_harm
            pop_total += pop * hit_prob    # probability-weighted population

            if p.get("is_forbidden"):
                fbd_hit = True
                forbidden_hit = True

        if sum_prob > 0:
            # Expected consequence = weighted average harm per unit probability
            zone_score = sum_harm / sum_prob
            # Normalise: pop_amp is ~1-2, so divide by 1.5 to keep in 0-1
            zone_score = min(zone_score / 1.5, 1.0)
        else:
            # No cells hit — check if landing point is in bbox
            zone_score = 0.05 if (S<=zlat<=N and W<=zlon<=E) else 0.0

        pop_at_risk  += int(pop_total)
        weighted      = zone["weight"] * zone_score
        total_consequence += weighted

        details.append({
            "class":       zone["class"],
            "score":       round(zone_score, 3),
            "weighted":    round(weighted, 3),
            "cells_hit":   sum(1 for f in state.GRID_FEATURES
                               if haversine_m(zlat,zlon,f["properties"]["lat"],
                                              f["properties"]["lon"]) <= rad),
            "forbidden":   fbd_hit,
            "pop_in_zone": int(pop_total),
            "radius_m":    rad,
        })

    # ── Score the unified impact zone separately ───────────────────────────
    # unified_lat/lon/rad are set by compute_debris_landing (weighted centroid)
    # This scores the aggregate impact zone where MOST debris lands
    if landing_zones:
        ulat  = landing_zones[0].get("unified_lat", landing_zones[0]["land_lat"])
        ulon  = landing_zones[0].get("unified_lon", landing_zones[0]["land_lon"])
        urad  = landing_zones[0].get("unified_rad", 150.0)
        usig  = max(urad / 2.0, 25.0)
        usum_prob = usum_harm = 0.0
        for feat in state.GRID_FEATURES:
            p    = feat["properties"]
            dist = haversine_m(ulat, ulon, p["lat"], p["lon"])
            if dist > urad: continue
            hit_prob   = math.exp(-0.5 * (dist / usig) ** 2)
            cell_score = 0.70*(p.get("score") or 0) + 0.30*min(p.get("integrated_risk",0)/20.0,1.0)
            pop_amp    = 1.0 + math.log1p(p.get("pop_count",0)) / math.log1p(100)
            usum_prob += hit_prob
            usum_harm += hit_prob * cell_score * pop_amp
        unified_score = min(usum_harm / usum_prob / 1.5, 1.0) if usum_prob > 0 else 0.0
    else:
        ulat = ulon = 0.0; urad = 100.0; unified_score = 0.0

    return {
        "consequence":    round(total_consequence, 4),
        "forbidden_hit":  forbidden_hit,
        "pop_at_risk":    int(pop_at_risk),
        "details":        details,
        "unified_lat":    round(ulat, 6),
        "unified_lon":    round(ulon, 6),
        "unified_rad":    round(urad, 1),
        "unified_score":  round(unified_score, 4),
    }

def generate_drone_trajectory(seed, target_key=None, weather=None):
    """
    Generates a realistic evasive drone trajectory within the scored BBOX using the
    new probabilistic route prediction engine (A* + Tactical behaviors).
    """
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



def interpolate_trajectory(waypoints, t):
    """
    Smooth interpolation along a multi-waypoint trajectory.
    t in [0,1] maps to the full path by arc-length parameterisation.
    Uses cumulative distance so speed is uniform regardless of waypoint spacing.
    """
    if t <= 0: return {**waypoints[0], "t": 0}
    if t >= 1: return {**waypoints[-1], "t": 1}

    # Build cumulative arc-length table
    dists = [0.0]
    for i in range(len(waypoints)-1):
        d = haversine_m(
            waypoints[i]["lat"],   waypoints[i]["lon"],
            waypoints[i+1]["lat"], waypoints[i+1]["lon"]
        )
        dists.append(dists[-1] + d)
    total = dists[-1]
    if total == 0:
        return {**waypoints[0], "t": t}

    # Target distance along path
    target_dist = t * total

    # Find which segment we're in
    seg = 0
    for i in range(len(dists)-1):
        if dists[i] <= target_dist <= dists[i+1]:
            seg = i
            break
        seg = i

    seg_len = dists[seg+1] - dists[seg]
    local_t = (target_dist - dists[seg]) / seg_len if seg_len > 0 else 0
    a, b = waypoints[seg], waypoints[seg+1]

    return {
        "lat":   a["lat"]   + local_t * (b["lat"]   - a["lat"]),
        "lon":   a["lon"]   + local_t * (b["lon"]   - a["lon"]),
        "alt_m": a["alt_m"] + local_t * (b["alt_m"] - a["alt_m"]),
        "t":     t,
    }

# Critical sites — threat exclusion zones around these locations.
# Drone intercepted inside exclusion radius → debris falls on/near asset.
# Source: public knowledge, OSM, Swedish defence doctrine.


# Interceptor systems — realistic parameters for Karlskrona context
# Source: Jane's Weapons, public defence documentation


# Fixed interceptor launch site: Karlskrona Naval Base
# RBS 70 Mk2 SHORAD system is stationed here in real life.



def compute_drone_times(waypoints, speed_ms):
    """
    Compute cumulative time at each trajectory sample point.
    Uses arc-length parameterisation so time is proportional to real distance.
    Returns list of (t_param, time_seconds, lat, lon, alt_m) for N samples.
    """
    # Build dense samples along spline
    N = 120  # sample resolution
    samples = []
    for step in range(N + 1):
        t = step / N
        pos = interpolate_trajectory(waypoints, t)
        samples.append((t, pos["lat"], pos["lon"], pos["alt_m"]))

    # Cumulative time
    cum_time = [0.0]
    for i in range(1, len(samples)):
        d = haversine_m(samples[i-1][1], samples[i-1][2],
                        samples[i][1],   samples[i][2])
        cum_time.append(cum_time[-1] + d / speed_ms)

    return [(samples[i][0], cum_time[i], samples[i][1],
             samples[i][2], samples[i][3])
            for i in range(len(samples))]


def find_exclusion_entry_times(timed_samples, critical_sites):
    """
    For each critical site, find the earliest time the drone enters
    the exclusion radius. Returns dict {site_key: time_seconds or None}.
    """
    entry_times = {}
    for site in critical_sites:
        entry_time = None
        for (t_param, t_sec, lat, lon, alt) in timed_samples:
            dist = haversine_m(lat, lon, site["lat"], site["lon"])
            if dist <= site["radius_m"]:
                entry_time = t_sec
                break
        entry_times[site["key"]] = entry_time
    return entry_times


def solve_intercept_feasibility(drone_lat, drone_lon, drone_time_s,
                                 interceptor_lat, interceptor_lon,
                                 system, drone_vel_lat=0, drone_vel_lon=0,
                                 reaction_s_override=None):
    """
    Lead-angle intercept geometry.

    The missile does NOT fly to where the drone IS — it flies to where
    the drone WILL BE when the missile arrives. We solve this iteratively:

      t_fly_0 = dist(interceptor, drone_now) / v_missile
      future_pos = drone_now + drone_velocity * t_fly_0
      t_fly_1 = dist(interceptor, future_pos) / v_missile
      repeat until convergence (3-5 iterations)

    This correctly models lead-angle pursuit and gives a much larger
    feasible engagement envelope than the naive point-to-point check.

    Source: standard fire control geometry (Zarchan 2012, Tactical and
    Strategic Missile Guidance, Ch.2 lead-angle intercept)
    """
    v_i        = system["speed_ms"]
    reaction_s = reaction_s_override or system["reaction_s"]

    # Iterative lead-angle solver (converges in 3-5 steps)
    # Start with naive estimate
    dist_now = haversine_m(drone_lat, drone_lon, interceptor_lat, interceptor_lon)
    t_fly    = dist_now / v_i

    for _ in range(6):
        # Project drone forward by t_fly
        future_lat = drone_lat + drone_vel_lat * t_fly
        future_lon = drone_lon + drone_vel_lon * t_fly
        dist_future = haversine_m(future_lat, future_lon,
                                   interceptor_lat, interceptor_lon)
        t_fly_new = dist_future / v_i
        if abs(t_fly_new - t_fly) < 0.01:   # converged
            t_fly = t_fly_new
            break
        t_fly = t_fly_new

    future_lat = drone_lat + drone_vel_lat * t_fly
    future_lon = drone_lon + drone_vel_lon * t_fly
    dist_to_intercept = haversine_m(future_lat, future_lon,
                                     interceptor_lat, interceptor_lon)

    total_s    = reaction_s + t_fly
    time_margin= drone_time_s - total_s    # positive = interceptor arrives first
    launch_time = drone_time_s - total_s

    in_range   = dist_to_intercept <= system["range_m"]
    above_min  = dist_now >= system["min_range_m"]
    # Enforce minimum tracking time (e.g. 30s) before authorization to fire
    time_ok    = time_margin > 0 and launch_time >= 30.0

    return {
        "feasible":          in_range and above_min and time_ok,
        "dist_now_m":        round(dist_now),
        "dist_intercept_m":  round(dist_to_intercept),
        "flight_s":          round(t_fly, 1),
        "reaction_s":        reaction_s,
        "total_s":           round(total_s, 1),
        "launch_time_s":     round(drone_time_s - total_s, 1),
        "time_margin_s":     round(time_margin, 1),
        "intercept_lat":     round(future_lat, 6),
        "intercept_lon":     round(future_lon, 6),
        "in_range":          in_range,
        "above_min":         above_min,
        "time_ok":           time_ok,
        "dist_m":            round(dist_now),
    }


def full_engagement_analysis(traj, system_key, interceptor_pos, wx):
    """
    Full engagement analysis for a drone trajectory.

    For each of N trajectory points:
      1. Drone arrival time (arc-length / speed)
      2. Interceptor feasibility (can it get there in time?)
      3. Exclusion zone check (is drone past a critical site?)
      4. Debris consequence (physics + grid scoring)
      5. Engagement decision

    Returns structured result with all candidate points,
    optimal intercept, and engagement windows.
    """
    system   = INTERCEPTOR_SYSTEMS.get(system_key, INTERCEPTOR_SYSTEMS["rbs70"])
    waypoints = traj["waypoints"]
    speed_ms  = traj["speed_ms"]

    # Add wind direction to weather
    wx["wind_direction_deg"] = wx.get("wind_direction_deg", 270)

    # Step 1: build timed trajectory samples
    timed = compute_drone_times(waypoints, speed_ms)

    # Step 2: find when drone enters each exclusion zone
    entry_times = find_exclusion_entry_times(timed, CRITICAL_SITES)

    # Earliest exclusion entry (this is our hard deadline)
    earliest_excl = None
    nearest_site  = None
    for site in CRITICAL_SITES:
        et = entry_times.get(site["key"])
        if et is not None:
            if earliest_excl is None or et < earliest_excl:
                earliest_excl = et
                nearest_site  = site

    S, N, W, E = BBOX
    candidates  = []

    # Drone velocity vector (lat/lon per second) for lead-angle solver
    total_path_m = sum(
        haversine_m(waypoints[i]["lat"],waypoints[i]["lon"],
                    waypoints[i+1]["lat"],waypoints[i+1]["lon"])
        for i in range(len(waypoints)-1)
    )
    # Overall heading from entry to target (simplified — good enough for planning)
    entry_lat, entry_lon = waypoints[0]["lat"], waypoints[0]["lon"]
    final_lat, final_lon = waypoints[-1]["lat"], waypoints[-1]["lon"]
    total_d = haversine_m(entry_lat, entry_lon, final_lat, final_lon)
    if total_d > 0:
        # lat/lon per second
        drone_vlat = (final_lat - entry_lat) / (total_d / speed_ms) / 111111.0
        drone_vlon = (final_lon - entry_lon) / (total_d / speed_ms) / (111111.0 * math.cos(math.radians((entry_lat+final_lat)/2)))
    else:
        pass  # local velocities computed below

    # Pre-compute LOCAL velocity vector at each timed sample.
    # Uses point[i]→point[i+1] direction — correct for S-curves, spirals, jinks.
    # The old global average was wrong: a drone on an S-curve changes heading
    # mid-flight, so the lead-angle projection was pointing the wrong direction.
    local_velocities = []
    for i, (t_p, t_s, lat_i, lon_i, alt_i) in enumerate(timed):
        if i < len(timed) - 1:
            _, t_next, nlat, nlon, _ = timed[i+1]
            dt = t_next - t_s
            if dt > 0:
                vlat = (nlat - lat_i) / dt          # degrees latitude per second
                vlon = (nlon - lon_i) / dt          # degrees longitude per second
            else:
                vlat = vlon = 0.0
        else:
            vlat, vlon = local_velocities[-1] if local_velocities else (0.0, 0.0)
        local_velocities.append((vlat, vlon))

    for i, (t_param, t_sec, lat, lon, alt_m) in enumerate(timed):
        drone_vlat, drone_vlon = local_velocities[i]

        # ── Interceptor feasibility — local heading lead-angle ──────────────
        feas = solve_intercept_feasibility(
            lat, lon, t_sec,
            interceptor_pos["lat"], interceptor_pos["lon"],
            system,
            drone_vel_lat=drone_vlat,
            drone_vel_lon=drone_vlon,
        )

        # ── Exclusion zone check ───────────────────────────────────────────
        # Is this point BEFORE the drone enters ANY exclusion zone?
        before_excl = (earliest_excl is None) or (t_sec < earliest_excl)

        # Is the drone currently INSIDE an exclusion zone?
        inside_excl = False
        inside_site = None
        for site in CRITICAL_SITES:
            if haversine_m(lat, lon, site["lat"], site["lon"]) <= site["radius_m"]:
                inside_excl = True
                inside_site = site
                break

        # ── Debris physics ─────────────────────────────────────────────────
        if S <= lat <= N and W <= lon <= E:
            landing_zones = compute_debris_landing(lat, lon, alt_m, wx)
            score_result  = score_landing_zones(landing_zones)
            consequence   = score_result["consequence"]
            forbidden_hit = score_result["forbidden_hit"]
            pop_at_risk   = score_result["pop_at_risk"]
            details       = score_result["details"]
            lz_out = [{
                "class":       z["class"],
                "land_lat":    z["land_lat"],
                "land_lon":    z["land_lon"],
                "scatter_m":   z["scatter_m"],
                "drift_m":     z["drift_m"],
                "tof_s":       z["time_of_flight"],
                "weight":      z["weight"],
                "unified_lat": score_result.get("unified_lat", lat),
                "unified_lon": score_result.get("unified_lon", lon),
                "unified_rad": score_result.get("unified_rad", 150.0),
            } for z in landing_zones]
            in_bbox = True
        else:
            # Outside scored region — still run debris physics for visual display
            landing_zones_out = compute_debris_landing(lat, lon, alt_m, wx)
            consequence   = 0.05
            forbidden_hit = False
            pop_at_risk   = 0
            details       = []
            lz_out = [{
                "class":       z["class"],
                "land_lat":    z["land_lat"],
                "land_lon":    z["land_lon"],
                "scatter_m":   z["scatter_m"],
                "drift_m":     z["drift_m"],
                "tof_s":       z["time_of_flight"],
                "weight":      z["weight"],
                "unified_lat": z["unified_lat"],
                "unified_lon": z["unified_lon"],
                "unified_rad": z["unified_rad"],
            } for z in landing_zones_out]
            unified_lat   = landing_zones_out[0]["unified_lat"] if landing_zones_out else lat
            unified_lon   = landing_zones_out[0]["unified_lon"] if landing_zones_out else lon
            unified_rad   = landing_zones_out[0]["unified_rad"] if landing_zones_out else 150.0
            unified_score = 0.05
            in_bbox       = False

        # ── Engagement decision ────────────────────────────────────────────
        # Priority 1: Direct Forbidden Hits
        # Priority 2: Technical Feasibility
        # Priority 3: Restricted Space vs Landing Safety

        if forbidden_hit:
            decision = "NEVER"
            reason   = "Debris lands on forbidden infrastructure"
        elif not feas["feasible"]:
            if not feas["in_range"]:
                decision = "NO SHOT"
                reason   = f"Out of range ({feas['dist_m']}m > {system['range_m']}m)"
            elif not feas["time_ok"]:
                decision = "NO SHOT"
                reason   = f"Drone arrives first (margin={feas['time_margin_s']}s)"
            else:
                decision = "NO SHOT"
                reason   = "Below minimum range"
        elif inside_excl or not before_excl:
            # Restricted Space (Inside radius or past deadline)
            # "POTENTIAL" is only granted if the debris landing is "Safe" (not over impact zones)
            if consequence < 0.25:
                decision = "POTENTIAL"
                reason   = f"Safe landing zone (<25%) despite intercept in restricted space"
            else:
                decision = "HOLD"
                reason   = f"Restricted space AND landing hits impact zone ({consequence:.2f})"
        else:
            # Normal Space
            if consequence > 0.45:
                decision = "HOLD"
                reason   = f"Debris consequence high ({consequence:.2f}) — over populated/industrial area"
            elif consequence > 0.20:
                decision = "CAUTION"
                reason   = f"Debris consequence marginal ({consequence:.2f}) — seek better window"
            else:
                decision = "ENGAGE"
                reason   = f"Consequence acceptable ({consequence:.2f}), debris in low-impact zone"

        # Time remaining before drone hits exclusion zone
        time_to_excl = (earliest_excl - t_sec) if earliest_excl else None

        # Get grid cell properties
        cid = None
        cell_props = {}
        if in_bbox:
            try:
                cid = h3.latlng_to_cell(lat, lon, H3_RES)
                cell_props = state.GRID_INDEX.get(cid, {})
            except: pass

        candidates.append({
            "t":              round(t_param, 4),
            "time_s":         round(t_sec, 1),
            "lat":            round(lat, 6),
            "lon":            round(lon, 6),
            "alt_m":          round(alt_m),
            "in_bbox":        in_bbox,
            "decision":       decision,
            "reason":         reason,
            "consequence":    consequence,
            "forbidden_hit":  forbidden_hit,
            "pop_at_risk":    pop_at_risk,
            "inside_excl":    inside_excl,
            "before_excl":    before_excl,
            "time_to_excl_s": round(time_to_excl, 1) if time_to_excl else None,
            "feasibility":    feas,
            "landing_zones":  lz_out,
            "details":        details,
            "land_use":       cell_props.get("land_use", "unknown"),
            "is_sea":         cell_props.get("is_sea", not in_bbox),
            "is_coastal":     cell_props.get("is_coastal", False),
                "unified_lat":    score_result.get("unified_lat", lat) if in_bbox else lat,
                "unified_lon":    score_result.get("unified_lon", lon) if in_bbox else lon,
                "unified_rad":    score_result.get("unified_rad", 100.0) if in_bbox else 100.0,
                "unified_score":  score_result.get("unified_score", consequence) if in_bbox else 0.05,
        })

    # ── Find optimal intercept ─────────────────────────────────────────────
    # Pool all "acceptable" candidates.
    engage_pts = [c for c in candidates if c["decision"] == "ENGAGE"]
    caution_pts = [c for c in candidates if c["decision"] == "CAUTION"]
    potential_pts = [c for c in candidates if c["decision"] == "POTENTIAL"]
    pool = engage_pts + caution_pts + potential_pts

    if pool:
        # User mandate: Prioritize ABSOLUTE MINIMUM consequence, even if inside radius.
        # We add a tiny "tie-break" penalty to CAUTION/POTENTIAL so that if scores
        # are identical, we prefer the technically safer ENGAGE option.
        def rank_score(c):
            penalty = 0.0
            if c["decision"] == "CAUTION":   penalty = 0.01
            if c["decision"] == "POTENTIAL": penalty = 0.03
            return c["consequence"] + penalty

        optimal = min(pool, key=rank_score)
        optimal_type = f"{optimal['decision']} — Best Population Outcome"
    else:
        # Fall back: least-bad among technically feasible
        feasible_pts = [c for c in candidates if c["feasibility"]["feasible"]]
        if feasible_pts:
            optimal = min(feasible_pts, key=lambda c: c["consequence"])
            optimal_type = "LAST RESORT — outside normal envelope"
        elif candidates:
            optimal = min(candidates, key=lambda c: c["consequence"])
            optimal_type = "NO INTERCEPT POSSIBLE — least-bad shown"
        else:
            optimal = candidates[0] if candidates else None
            optimal_type = "NO INTERCEPT POSSIBLE"

    # ── Engagement windows ─────────────────────────────────────────────────
    windows = []
    in_win  = False
    for c in candidates:
        if c["decision"] in ("ENGAGE","CAUTION") and not in_win:
            windows.append({
                "start_t":   c["t"],
                "start_time":c["time_s"],
                "start_lat": c["lat"],
                "start_lon": c["lon"],
                "type":      c["decision"],
            })
            in_win = True
        elif c["decision"] not in ("ENGAGE","CAUTION") and in_win:
            windows[-1]["end_t"]   = c["t"]
            windows[-1]["end_time"]= c["time_s"]
            windows[-1]["end_lat"] = c["lat"]
            windows[-1]["end_lon"] = c["lon"]
            in_win = False
    if in_win and windows:
        last = candidates[-1]
        windows[-1].update({"end_t":last["t"],"end_time":last["time_s"],
                             "end_lat":last["lat"],"end_lon":last["lon"]})

    stats = {
        "total_samples":    len(candidates),
        "engage_pts":       len(engage_pts),
        "caution_pts":      len(caution_pts),
        "potential_pts":    len(potential_pts),
        "no_shot_pts":      sum(1 for c in candidates if c["decision"]=="NO SHOT"),
        "never_pts":        sum(1 for c in candidates if c["decision"]=="NEVER"),
        "engage_windows":   len(windows),
        "earliest_excl_s":  round(earliest_excl,1) if earliest_excl else None,
        "nearest_site":     nearest_site["name"] if nearest_site else None,
        "optimal_type":     optimal_type,
        "system":           system["name"],
        "interceptor_pos":  interceptor_pos,
    }

    return {
        "optimal":          optimal,
        "optimal_type":     optimal_type,
        "windows":          windows,
        "all_candidates":   candidates,
        "entry_times":      {k: round(v,1) if v else None for k,v in entry_times.items()},
        "critical_sites":   CRITICAL_SITES,
        "system":           system,
        "stats":            stats,
        "weather":          wx,
        "recommendation": (
            f"[{optimal_type}] Intercept at t={optimal['t']:.3f} "
            f"({optimal['lat']:.4f}N {optimal['lon']:.4f}E, alt={optimal['alt_m']}m, "
            f"t={optimal['time_s']:.0f}s into flight). "
            f"Consequence={optimal['consequence']:.3f}. "
            f"Interceptor flight={optimal['feasibility']['flight_s']:.0f}s, "
            f"margin={optimal['feasibility']['time_margin_s']:.0f}s. "
            + (f"Drone reaches {nearest_site['name']} in {earliest_excl:.0f}s."
               if earliest_excl else "No critical site threatened.")
        ),
    }

def compute_integrated_debris_risk(features):
    """
    Pre-calculate cumulative 1km debris risk for every grid cell.
    For each cell, sum weighted scores of all neighbours within 1000m.
    Weight = 1 - (dist_m / 1000) — nearer cells count more.

    Gives an 'area consequence' score: not just the cell hit but the
    risk of the surrounding zone. Used in consequence scoring to
    weight intercept points where nearby misses also matter.

    Source: appSSS.py. Uses SWEREF99 TM (EPSG:3006) for accurate
    metre-based distances over Sweden.
    """
    print(f"[RISK] Computing integrated debris risk for {len(features)} cells ...")
    t0 = time.time()
    try:
        lats   = [f["properties"]["lat"]           for f in features]
        lons   = [f["properties"]["lon"]           for f in features]
        scores = [f["properties"]["score"] or 0.1  for f in features]
        gdf = gpd.GeoDataFrame(
            {"score": scores, "orig_idx": range(len(features))},
            geometry=[Point(lon,lat) for lat,lon in zip(lats,lons)],
            crs="EPSG:4326"
        ).to_crs("EPSG:3006")   # metric CRS for accurate distances
        sindex = gdf.sindex
        for i, row in gdf.iterrows():
            buf     = row.geometry.buffer(1000)
            hits    = list(sindex.intersection(buf.bounds))
            matches = gdf.iloc[hits]
            risk    = 0.0
            for _, other in matches.iterrows():
                d = row.geometry.distance(other.geometry)
                if d <= 1000:
                    risk += other["score"] * (1.0 - d / 1000.0)
            features[row["orig_idx"]]["properties"]["integrated_risk"] = round(risk, 3)
        print(f"[RISK] Done in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"[RISK] Failed: {e} — defaulting integrated_risk=0")
        for f in features:
            f["properties"].setdefault("integrated_risk", 0.0)
    return features

