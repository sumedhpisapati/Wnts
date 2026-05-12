from core.core_imports import *

# Drone flight kinematics and profiles
DRONE_PROFILES = {
    "fpv_kamikaze": {
        "speed_ms": 40, 
        "max_turn_rate_deg_s": 15, 
        "max_climb_ms": 10, 
        "min_alt_m": 10,
        "max_alt_m": 500,
        "notes": "Highly maneuverable, low altitude, tactical"
    },
    "tactical_isr": {
        "speed_ms": 30, 
        "max_turn_rate_deg_s": 10, 
        "max_climb_ms": 5, 
        "min_alt_m": 50,
        "max_alt_m": 2000,
        "notes": "Medium maneuverability, loitering capability"
    },
    "male_strike": {
        "speed_ms": 80, 
        "max_turn_rate_deg_s": 5, 
        "max_climb_ms": 15, 
        "min_alt_m": 200,
        "max_alt_m": 8000,
        "notes": "Lower maneuverability, high altitude, strategic"
    },
    "cruise_missile": {
        "speed_ms": 250, 
        "max_turn_rate_deg_s": 3, 
        "max_climb_ms": 20, 
        "min_alt_m": 30,
        "max_alt_m": 1000,
        "notes": "Very fast, terrain-following, low turn rate"
    },
}

class TacticalState:
    APPROACH = "APPROACH"          # Moving toward target area
    TERRAIN_MASK = "TERRAIN_MASK"  # Seeking low terrain/water to hide from radar
    ALIGNMENT = "ALIGNMENT"        # Lining up for final run
    TERMINAL = "TERMINAL"          # Final attack run (direct line)
    EVASION = "EVASION"            # Detected interceptor, taking evasive action
    SCOUTING = "SCOUTING"          # Loitering or passing a via-point

    @staticmethod
    def transition(current_state, props, dist_to_target):
        """
        Probabilistically determines the next tactical state based on 
        the drone's environment and distance to target.
        """
        import random
        r = random.random()

        if current_state == TacticalState.APPROACH:
            if dist_to_target < 2000: return TacticalState.ALIGNMENT
            if props.get("is_sea") and r < 0.4: return TacticalState.TERRAIN_MASK
            return TacticalState.APPROACH

        if current_state == TacticalState.TERRAIN_MASK:
            if dist_to_target < 3000: return TacticalState.ALIGNMENT
            if not props.get("is_sea") and r < 0.7: return TacticalState.APPROACH
            return TacticalState.TERRAIN_MASK

        if current_state == TacticalState.ALIGNMENT:
            if dist_to_target < 800: return TacticalState.TERMINAL
            return TacticalState.ALIGNMENT

        return current_state
