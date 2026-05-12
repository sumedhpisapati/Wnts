from core_imports import *
from utils import haversine_m
from config import CRITICAL_SITES
import math

def get_candidate_targets():
    """
    Returns the list of potential targets the drone might be aiming for.
    Currently pulls from CRITICAL_SITES in config.
    """
    targets = []
    for site in CRITICAL_SITES:
        targets.append({
            "key": site["key"],
            "name": site["name"],
            "lat": site["lat"],
            "lon": site["lon"],
            "base_value": 1.0 # Can be weighted later based on strategic value
        })
    # Add a generic "Recon Flight" dummy target far inland
    targets.append({
        "key": "recon_flight",
        "name": "General Recon Flight",
        "lat": 56.3000, 
        "lon": 15.6000,
        "base_value": 0.5
    })
    return targets

def infer_target_probabilities(drone_lat, drone_lon, drone_heading_deg, targets):
    """
    Infers the probability of each target based on the drone's current 
    position, heading, and distance to the targets.
    """
    probs = {}
    total_weight = 0.0
    
    for t in targets:
        dist_m = haversine_m(drone_lat, drone_lon, t['lat'], t['lon'])
        
        # Calculate bearing to target
        bearing_rad = math.atan2(
            math.sin(math.radians(t['lon'] - drone_lon)) * math.cos(math.radians(t['lat'])),
            math.cos(math.radians(drone_lat)) * math.sin(math.radians(t['lat'])) -
            math.sin(math.radians(drone_lat)) * math.cos(math.radians(t['lat'])) * math.cos(math.radians(t['lon'] - drone_lon))
        )
        bearing_deg = (math.degrees(bearing_rad) + 360) % 360
        
        # Heading difference (0 to 180 degrees)
        heading_diff = abs(bearing_deg - drone_heading_deg)
        if heading_diff > 180:
            heading_diff = 360 - heading_diff
            
        # Probability formulation
        # 1. Heading Alignment: High if pointing directly at target (sigma=45 deg)
        heading_score = math.exp(-0.5 * (heading_diff / 45.0)**2)
        
        # 2. Distance: Closer targets get slightly higher weighting (sigma=15000m)
        dist_score = math.exp(-0.5 * (dist_m / 15000.0)**2)
        
        # Raw likelihood
        raw_p = heading_score * dist_score * t.get('base_value', 1.0)
        
        # Prevent zero probability
        raw_p = max(0.01, raw_p)
        
        probs[t['key']] = raw_p
        total_weight += raw_p
        
    # Normalize probabilities so they sum to 1.0
    normalized_probs = {}
    if total_weight > 0:
        for k, v in probs.items():
            normalized_probs[k] = round(v / total_weight, 4)
            
    # Sort by highest probability
    return dict(sorted(normalized_probs.items(), key=lambda item: item[1], reverse=True))