import re

with open("route_prediction.py", "r", encoding="utf-8") as f:
    code = f.read()

new_content = """from core_imports import *
from core import state
from core.utils import haversine_m
from core.config import BBOX
import heapq
import math

# Cost function weights (can be parameterized later)
COST_WEIGHTS = {
    "population_density": 1.0,
    "forbidden_zone": 500.0,
    "water_bonus": -2.0,       # Drones prefer flying over water
    "radar_exposure": 5.0,     # Placeholder for line-of-sight exposure
    "distance": 0.001,         # Base distance cost multiplier
    "weather_penalty": 10.0,
    "headwind_penalty": 0.05,
    "sam_coverage": 50.0,
    "maritime_traffic": 0.5
}

def get_h3_neighbors(cell_id):
    \"\"\"Returns valid neighboring H3 cells within the bounding box.\"\"\"
    try:
        neighbors = h3.grid_disk(cell_id, 1)
        valid = []
        S, N, W, E = BBOX
        for n in neighbors:
            if n == cell_id: continue
            lat, lon = h3.cell_to_latlng(n)
            # Clamp to BBOX to prevent infinite searching off-map
            if S <= lat <= N and W <= lon <= E:
                valid.append(n)
        return valid
    except Exception:
        return []

def calculate_cell_cost(cell_id, current_lat, current_lon, neighbor_lat, neighbor_lon, weather, profile, current_heading):
    \"\"\"
    Evaluates the tactical cost of a drone flying through this H3 cell.
    A lower cost represents a 'safer' or more likely route for the drone.
    \"\"\"
    props = state.GRID_INDEX.get(cell_id)
    if not props: return 10.0
        
    cost = 5.0 # Base traversal cost
    
    # 1. Population Density
    pop = props.get("pop_count", 0)
    cost += math.log1p(pop) * COST_WEIGHTS["population_density"]
    
    # 2. Water / Coastline preference (Terrain Masking / Sea Skimming)
    if props.get("is_sea"):
        cost += COST_WEIGHTS["water_bonus"] * 2
    elif props.get("is_coastal"):
        cost += COST_WEIGHTS["water_bonus"]
        
    # 3. Avoid forbidden military zones (Heavy air defense / SAM coverage proxy)
    if props.get("is_forbidden"):
        cost += COST_WEIGHTS["forbidden_zone"]
        
    # 4. Integrated TEL Risk
    tel = props.get("tel", 0)
    if tel > 100:
        cost += COST_WEIGHTS["sam_coverage"] * (tel / 1000.0)
        
    # 5. Weather Severity and Wind Direction Penalty
    if weather:
        wind_from = float(weather.get("wind_direction_deg", 0))
        wind_to = (wind_from + 180) % 360
        wind_speed = float(weather.get("windspeed_kmh", 0)) / 3.6
        
        # Calculate heading towards neighbor
        bearing_rad = math.atan2(
            math.sin(math.radians(neighbor_lon - current_lon)) * math.cos(math.radians(neighbor_lat)),
            math.cos(math.radians(current_lat)) * math.sin(math.radians(neighbor_lat)) -
            math.sin(math.radians(current_lat)) * math.cos(math.radians(neighbor_lat)) * math.cos(math.radians(neighbor_lon - current_lon))
        )
        bearing_deg = (math.degrees(bearing_rad) + 360) % 360
        
        # Headwind penalty
        wind_diff = abs(bearing_deg - wind_from)
        if wind_diff > 180: wind_diff = 360 - wind_diff
        # If wind_diff is small, flying into headwind
        headwind_factor = math.cos(math.radians(wind_diff))
        if headwind_factor > 0:
            cost += headwind_factor * wind_speed * COST_WEIGHTS["headwind_penalty"]

        # General weather penalty (rain, snow)
        if weather.get("precipitation", 0) > 0:
            cost += COST_WEIGHTS["weather_penalty"]

    return max(0.1, cost)

def a_star_route(start_lat, start_lon, target_lat, target_lon, res=9, profile=None, weather=None, initial_heading=0):
    \"\"\"
    Generates a heuristic-based path from start to target using the H3 grid, 
    accounting for drone kinematics and tactical costs.
    \"\"\"
    if profile is None:
        profile = {"speed_ms": 30, "max_turn_rate_deg_s": 10}

    start_cell = h3.latlng_to_cell(start_lat, start_lon, res)
    target_cell = h3.latlng_to_cell(target_lat, target_lon, res)
    
    queue = []
    counter = 0
    # State: (cell_id, current_heading)
    start_state = (start_cell, initial_heading)
    heapq.heappush(queue, (0, counter, start_state))
    
    came_from = {}
    g_score = {start_state: 0.0}
    f_score = {start_state: haversine_m(start_lat, start_lon, target_lat, target_lon) * COST_WEIGHTS["distance"]}
    
    t_lat, t_lon = h3.cell_to_latlng(target_cell)
    
    max_iters = 10000
    iters = 0
    
    best_target_state = None

    while queue:
        iters += 1
        if iters > max_iters:
            print("[ROUTE] Pathfinding timeout.")
            break
            
        _, _, current_state = heapq.heappop(queue)
        current_cell, current_heading = current_state
        
        if current_cell == target_cell:
            best_target_state = current_state
            break
            
        c_lat, c_lon = h3.cell_to_latlng(current_cell)
        
        for neighbor in get_h3_neighbors(current_cell):
            n_lat, n_lon = h3.cell_to_latlng(neighbor)
            step_dist = haversine_m(c_lat, c_lon, n_lat, n_lon)
            
            # Kinematics: calculate required turn
            bearing_rad = math.atan2(
                math.sin(math.radians(n_lon - c_lon)) * math.cos(math.radians(n_lat)),
                math.cos(math.radians(c_lat)) * math.sin(math.radians(n_lat)) -
                math.sin(math.radians(c_lat)) * math.cos(math.radians(n_lat)) * math.cos(math.radians(n_lon - c_lon))
            )
            bearing_deg = (math.degrees(bearing_rad) + 360) % 360
            
            turn_diff = abs(bearing_deg - current_heading)
            if turn_diff > 180: turn_diff = 360 - turn_diff
            
            # Penalize sharp turns based on profile
            # Max turn rate * time to cross cell
            time_to_cross = step_dist / max(1.0, profile["speed_ms"])
            max_turn = profile["max_turn_rate_deg_s"] * time_to_cross
            
            turn_penalty = 0
            if turn_diff > max_turn:
                # Impose a heavy penalty for exceeding turn limits, practically preventing it
                # unless absolutely necessary (e.g. at start)
                turn_penalty = 50.0 * (turn_diff / max_turn)
            else:
                # Small penalty for turning to prefer straight paths
                turn_penalty = 0.5 * turn_diff
            
            cell_penalty = calculate_cell_cost(neighbor, c_lat, c_lon, n_lat, n_lon, weather, profile, current_heading)
            
            tentative_g = g_score[current_state] + (step_dist * COST_WEIGHTS["distance"]) + cell_penalty + turn_penalty
            neighbor_state = (neighbor, bearing_deg)
            
            if neighbor_state not in g_score or tentative_g < g_score[neighbor_state]:
                came_from[neighbor_state] = current_state
                g_score[neighbor_state] = tentative_g
                
                h = haversine_m(n_lat, n_lon, t_lat, t_lon) * COST_WEIGHTS["distance"]
                f = tentative_g + h
                
                counter += 1
                heapq.heappush(queue, (f, counter, neighbor_state))
                
    # Reconstruct path
    path = []
    current_state = best_target_state
    
    if current_state is None or current_state not in came_from:
        # closest state
        if g_score:
            closest_state = min(g_score.keys(), key=lambda s: haversine_m(h3.cell_to_latlng(s[0])[0], h3.cell_to_latlng(s[0])[1], t_lat, t_lon))
            current_state = closest_state

    while current_state in came_from:
        path.append(current_state[0])
        current_state = came_from[current_state]
    path.append(start_state[0])
    path.reverse()
    
    waypoints = []
    for cell in path:
        lat, lon = h3.cell_to_latlng(cell)
        waypoints.append({"lat": round(lat, 6), "lon": round(lon, 6)})
        
    return waypoints
"""

with open("route_prediction.py", "w", encoding="utf-8") as f:
    f.write(new_content)

print("route_prediction.py updated.")
