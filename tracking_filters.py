from core_imports import *
from intent_inference import infer_target_probabilities, get_candidate_targets

class DroneTracker:
    """
    A Kalman-style state estimator for drone tracking.
    Maintains estimates for:
    - Position (lat, lon)
    - Velocity (v_lat, v_lon)
    - Target Intent Beliefs (categorical distribution)
    """
    def __init__(self, start_lat, start_lon, drone_id="threat_0"):
        self.drone_id = drone_id
        
        # State: [lat, lon, v_lat, v_lon]
        self.state = np.array([start_lat, start_lon, 0.0, 0.0])
        self.P = np.eye(4) * 0.1 # State covariance
        self.Q = np.eye(4) * 0.01 # Process noise
        self.R = np.eye(2) * 0.001 # Measurement noise (radar)
        
        self.last_update = time.time()
        
        # Intent beliefs
        self.targets = get_candidate_targets()
        self.intent_beliefs = {t['key']: 1.0/len(self.targets) for t in self.targets}

    def predict(self, dt):
        """Standard Kalman prediction step."""
        # Transition matrix F
        F = np.array([
            [1, 0, dt, 0],
            [0, 1, 0, dt],
            [0, 0, 1, 0],
            [0, 0, 0, 1]
        ])
        self.state = F @ self.state
        self.P = F @ self.P @ F.T + self.Q

    def update(self, meas_lat, meas_lon, heading_deg):
        """Standard Kalman update step + Intent Belief Update."""
        now = time.time()
        dt = now - self.last_update
        self.last_update = now
        
        self.predict(dt)
        
        # Measurement update
        H = np.array([
            [1, 0, 0, 0],
            [0, 1, 0, 0]
        ])
        z = np.array([meas_lat, meas_lon])
        y = z - H @ self.state # Innovation
        S = H @ self.P @ H.T + self.R
        K = self.P @ H.T @ np.linalg.inv(S) # Kalman gain
        
        self.state = self.state + K @ y
        self.P = (np.eye(4) - K @ H) @ self.P
        
        # --- Intent Belief Update (Bayesian) ---
        # Get instantaneous likelihoods from intent_inference
        likelihoods = infer_target_probabilities(self.state[0], self.state[1], heading_deg, self.targets)
        
        # Update beliefs: posterior = likelihood * prior
        new_beliefs = {}
        total = 0.0
        alpha = 0.2 # Smoothing factor for belief updates
        
        for k in self.intent_beliefs:
            # We blend the old belief with the new likelihood to provide stability
            inst_l = likelihoods.get(k, 0.01)
            new_val = (1 - alpha) * self.intent_beliefs[k] + alpha * inst_l
            new_beliefs[k] = new_val
            total += new_val
            
        # Normalize
        self.intent_beliefs = {k: v/total for k, v in new_beliefs.items()}

    def get_state(self):
        return {
            "lat": self.state[0],
            "lon": self.state[1],
            "v_lat": self.state[2],
            "v_lon": self.state[3],
            "intent": dict(sorted(self.intent_beliefs.items(), key=lambda x: x[1], reverse=True))
        }
