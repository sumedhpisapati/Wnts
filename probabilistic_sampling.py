from core_imports import *
from route_prediction import a_star_route
from behavior_models import TacticalState, DRONE_PROFILES
from utils import haversine_m
import state
import random

def generate_multi_hypothesis_routes(drone_lat, drone_lon, intent_beliefs, targets, heading=0, k=3, profile_key="tactical_isr"):
    """
    Generates the top-k most likely future trajectories with tactical behavior modeling.
    """
    sorted_intents = sorted(intent_beliefs.items(), key=lambda x: x[1], reverse=True)
    top_k_intents = sorted_intents[:k]
    
    profile = DRONE_PROFILES.get(profile_key, DRONE_PROFILES["tactical_isr"])
    hypotheses = []
    
    for intent_key, prob in top_k_intents:
        if prob < 0.05: continue 
        
        target = next(t for t in targets if t["key"] == intent_key)
        base_waypoints = a_star_route(drone_lat, drone_lon, target["lat"], target["lon"], initial_heading=heading, profile=profile)
        
        # Enrich waypoints with tactical behaviors and altitude
        enriched = []
        current_behavior = TacticalState.APPROACH
        
        for i, wp in enumerate(base_waypoints):
            dist_to_target = haversine_m(wp["lat"], wp["lon"], target["lat"], target["lon"])
            cid = h3.latlng_to_cell(wp["lat"], wp["lon"], 9)
            props = state.GRID_INDEX.get(cid, {})
            
            current_behavior = TacticalState.transition(current_behavior, props, dist_to_target)
            
            # Altitude profile based on behavior
            alt = profile["min_alt_m"] + 100 # Default
            if current_behavior == TacticalState.TERRAIN_MASK:
                alt = profile["min_alt_m"] + 20 # Low altitude for masking
            elif current_behavior == TacticalState.TERMINAL:
                alt = 30 # Final dive
                
            enriched.append({
                "lat": wp["lat"],
                "lon": wp["lon"],
                "alt_m": alt,
                "behavior": current_behavior
            })
        
        hypotheses.append({
            "target_key": intent_key,
            "target_name": target["name"],
            "confidence": round(prob, 4),
            "waypoints": enriched,
            "type": "tactical_simulation"
        })
        
    return hypotheses

def monte_carlo_sampling(drone_lat, drone_lon, target_lat, target_lon, heading=0, n_samples=5, perturbation=0.002):
    """
    Samples multiple variations of a route to create a probability corridor.
    perturbation: standard deviation of random waypoint jitter in degrees.
    """
    # Get base route
    base_route = a_star_route(drone_lat, drone_lon, target_lat, target_lon, initial_heading=heading)
    
    samples = []
    for i in range(n_samples):
        perturbed_route = []
        for wp in base_route:
            # Don't perturb start and end points
            if wp == base_route[0] or wp == base_route[-1]:
                perturbed_route.append(wp)
            else:
                perturbed_route.append({
                    "lat": wp["lat"] + random.gauss(0, perturbation),
                    "lon": wp["lon"] + random.gauss(0, perturbation)
                })
        samples.append(perturbed_route)
        
    return samples
