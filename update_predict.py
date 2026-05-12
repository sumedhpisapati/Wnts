import re

with open("server.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add ACTIVE_TRACKERS at the top of the file
if "ACTIVE_TRACKERS =" not in content:
    # Insert after import tracking_filters
    content = content.replace("import probabilistic_sampling\n", "import probabilistic_sampling\n\nACTIVE_TRACKERS = {}\n")

# 2. Replace the /predict block
new_predict_block = """        elif path=="/predict":
            try:
                lat = float(qs.get("lat", [0])[0])
                lon = float(qs.get("lon", [0])[0])
                heading = float(qs.get("heading", [0])[0])
                k = int(qs.get("k", [3])[0])
            except ValueError:
                self.send_json({"error": "Provide ?lat=&lon=&heading="}, 400)
                return

            drone_id = qs.get("drone_id", [None])[0]
            targets = intent_inference.get_candidate_targets()
            
            # Step 1: Infer intent (initial or updated via Kalman)
            if drone_id:
                if drone_id not in ACTIVE_TRACKERS:
                    ACTIVE_TRACKERS[drone_id] = tracking_filters.DroneTracker(lat, lon, drone_id)
                tracker = ACTIVE_TRACKERS[drone_id]
                tracker.update(lat, lon, heading)
                probabilities = tracker.intent_beliefs
            else:
                probabilities = intent_inference.infer_target_probabilities(lat, lon, heading, targets)
            
            # Sort probabilities dict by value just in case
            probabilities = dict(sorted(probabilities.items(), key=lambda item: item[1], reverse=True))

            # Step 2: Multi-hypothesis generation
            hypotheses = probabilistic_sampling.generate_multi_hypothesis_routes(lat, lon, probabilities, targets, k=k)
            
            # Enrich hypotheses to ensure they look like trajectories for physics
            # This allows us to run full_engagement_analysis on them.
            for hyp in hypotheses:
                # Add t and speed_ms
                speed_ms = 30.0 # default from tactical_isr profile
                total_dist = 0.0
                hyp["speed_ms"] = speed_ms
                hyp["speed_kmh"] = round(speed_ms * 3.6)
                for i, wp in enumerate(hyp["waypoints"]):
                    wp["t"] = 0 # Dummy t, logic uses distances anyway
                    if i > 0:
                        prev_wp = hyp["waypoints"][i-1]
                        h_dist = haversine_m(prev_wp["lat"], prev_wp["lon"], wp["lat"], wp["lon"])
                        v_dist = abs(wp["alt_m"] - prev_wp["alt_m"])
                        total_dist += math.sqrt(h_dist**2 + v_dist**2)
                hyp["total_dist_m"] = total_dist
                hyp["total_time_s"] = total_dist / speed_ms
                hyp["n_waypoints"] = len(hyp["waypoints"])

            # Step 3: Intercept Integration (if requested)
            analyze_intercept = qs.get("analyze_intercept", ["false"])[0].lower() == "true"
            intercept_results = []
            probabilistic_consequence = 0.0
            
            if analyze_intercept and hypotheses:
                system_key = qs.get("system", ["rbs70"])[0]
                system = INTERCEPTOR_SYSTEMS.get(system_key, INTERCEPTOR_SYSTEMS["rbs70"])
                wx = get_weather_with_overrides(qs)
                base_pos = system.get("base", DEFAULT_INTERCEPTOR)
                i_lat = float(qs.get("iLat", [str(base_pos["lat"])])[0])
                i_lon = float(qs.get("iLon", [str(base_pos["lon"])])[0])
                interceptor_pos = {"lat": i_lat, "lon": i_lon}

                for hyp in hypotheses:
                    # Pass hypothesis as if it was a single trajectory
                    # full_engagement_analysis expects a 'traj' dict with 'waypoints', 'speed_ms'
                    try:
                        res = full_engagement_analysis(hyp, system_key, interceptor_pos, wx)
                        # Use confidence to weight consequence
                        conf = hyp["confidence"]
                        hyp_cons = res["optimal"]["consequence"] if res.get("optimal") else 0.0
                        probabilistic_consequence += conf * hyp_cons
                        
                        intercept_results.append({
                            "target_key": hyp["target_key"],
                            "confidence": conf,
                            "optimal_type": res["optimal_type"],
                            "consequence": hyp_cons,
                            "windows": res["windows"]
                        })
                    except Exception as e:
                        print(f"[PREDICT INTERCEPT] Error analyzing hypothesis {hyp['target_key']}: {e}")
            
            # Step 4: Monte Carlo sampling for the top hypothesis to show uncertainty
            corridor = []
            if hypotheses:
                top = hypotheses[0]
                corridor = probabilistic_sampling.monte_carlo_sampling(lat, lon, top["waypoints"][-1]["lat"], top["waypoints"][-1]["lon"], n_samples=5)
            
            response_data = {
                "start": {"lat": lat, "lon": lon, "heading": heading},
                "target_probabilities": probabilities,
                "hypotheses": hypotheses,
                "uncertainty_corridor": corridor
            }
            if analyze_intercept:
                response_data["probabilistic_intercept"] = {
                    "expected_consequence": round(probabilistic_consequence, 4),
                    "hypotheses_analysis": intercept_results
                }
                
            self.send_json(response_data)"""

# Regex to replace the old /predict block
pattern = re.compile(r"        elif path==\"/predict\":.*?self\.send_json\([^)]+\)", re.DOTALL)
new_content = pattern.sub(new_predict_block, content)

with open("server.py", "w", encoding="utf-8") as f:
    f.write(new_content)

print("Updated server.py with Live Tracking and Probabilistic Intercept")
