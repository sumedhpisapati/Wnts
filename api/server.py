from core.core_imports import *
from core.config import *
from core import state
from core.utils import *
from data_processing.data import *
from engine.scoring import *
from engine.physics import *
from api.ui import *
from prediction import intent_inference
from prediction import route_prediction
from prediction import tracking_filters
from prediction import probabilistic_sampling
ACTIVE_TRACKERS = {}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send_json(self,data,status=200):
        body=json.dumps(data,separators=(",",":")).encode()
        self.send_response(status); self.send_header("Content-Type","application/json")
        self.send_header("Content-Length",str(len(body))); self.send_header("Access-Control-Allow-Origin","*")
        self.end_headers(); self.wfile.write(body)
    def send_html(self,html):
        body=html.encode(); self.send_response(200)
        self.send_header("Content-Type","text/html; charset=utf-8")
        self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed=urlparse(self.path); qs=parse_qs(parsed.query); path=parsed.path
        if path=="/score":
            try: lat=float(qs["lat"][0]); lon=float(qs["lon"][0])
            except: self.send_json({"error":"Provide ?lat=&lon="},400); return
            S,N,W,E=BBOX
            if not(S<=lat<=N and W<=lon<=E): self.send_json({"error":"Outside Karlskrona region"},404); return
            cid=h3.latlng_to_cell(lat,lon,H3_RES); p=state.GRID_INDEX.get(cid)
            if p is None: self.send_json({"error":"Cell not in grid"},404); return
            result={"score":p["score"],"tel":p["tel"],"wb":p["wb"],"land_use":p["land_use"],
                "classification":p["classification"],"cell_id":cid,"lat":p["lat"],"lon":p["lon"],
                "is_forbidden":p["is_forbidden"],"is_sea":p["is_sea"],"is_coastal":p["is_coastal"],
                "no_data":p["no_data"],"pop_count":p["pop_count"],
                "contributors":{"population":p["pop_count"],"residential":p["res_frac"],
                    "industrial":p["ind_frac"],"roads":p["road_count"],
                    "sensitive":p["sens_score"],"water":p["wat_frac"]},
                "explanation":p["explanation"],"integrated_risk":p.get("integrated_risk",0),"temporal":None,"weather":None}
            # Always fetch weather with overrides for temporal/weather fields
            wx=get_weather_with_overrides(qs)
            if "time" in qs or "day" in qs:
                dt=parse_dt(qs)
                result["temporal"]=score_with_time(p,dt,state.PRESENCE_CURVES,wx)
            result["weather"]=wx
            self.send_json(result)
        elif path=="/cells":
            self.send_json({"type":"FeatureCollection","features":state.GRID_FEATURES,"meta":{"total":len(state.GRID_FEATURES)}})
        elif path=="/maritime": self.send_json(state.MARITIME_DATA)
        elif path=="/weather":  self.send_json(fetch_weather())
        elif path=="/mock_ais": self.send_json(load_ais())
        elif path=="/critical_sites":
            # Return critical infrastructure sites + their exclusion zones
            self.send_json({"sites": CRITICAL_SITES, "systems": INTERCEPTOR_SYSTEMS})

        elif path=="/intercept":
            # Full physics-based engagement analysis
            # ?seed=&system=rbs70&iLat=&iLon=&wind_dir=
            try:
                seed = int(qs.get("seed",["1"])[0])
            except:
                self.send_json({"error":"Provide ?seed="},400); return

            system_key = qs.get("system",["rbs70"])[0]
            system = INTERCEPTOR_SYSTEMS.get(system_key, INTERCEPTOR_SYSTEMS["rbs70"])
            wx = get_weather_with_overrides(qs)
            
            # Use system-specific base if available, else default to Naval Base
            base_pos = system.get("base", DEFAULT_INTERCEPTOR)
            i_lat = float(qs.get("iLat",[str(base_pos["lat"])])[0])
            i_lon = float(qs.get("iLon",[str(base_pos["lon"])])[0])
            interceptor_pos = {"lat":i_lat,"lon":i_lon}

            print(f"[INTERCEPT] Site: {i_lat:.4f}N {i_lon:.4f}E | System: {system_key} | Wind: {wx['windspeed_kmh']}km/h")

            # Reconstruct exact curved trajectory from seed
            traj = generate_drone_trajectory(seed)

            # Full engagement analysis
            result = full_engagement_analysis(traj, system_key, interceptor_pos, wx)

            self.send_json({
                "trajectory":    traj,
                "analysis":      result,
                "optimal":       result["optimal"],
                "optimal_type":  result["optimal_type"],
                "windows":       result["windows"],
                "all_candidates":result["all_candidates"],
                "critical_sites":CRITICAL_SITES,
                "system":        system,
                "stats":         result["stats"],
                "weather":       wx,
                "recommendation":result["recommendation"],
            })

        elif path=="/drone":
            import random
            seed = int(qs.get("seed",["0"])[0]) or random.randint(1,9999)
            traj = generate_drone_trajectory(seed)
            self.send_json(traj)


        elif path=="/predict":
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
            hypotheses = probabilistic_sampling.generate_multi_hypothesis_routes(lat, lon, probabilities, targets, heading=heading, k=k)
            
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
                corridor = probabilistic_sampling.monte_carlo_sampling(lat, lon, top["waypoints"][-1]["lat"], top["waypoints"][-1]["lon"], heading=heading, n_samples=5)
            
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
                
            self.send_json(response_data)
        elif path=="/health":
            self.send_json({"status":"ok","version":"4.0","cells":len(state.GRID_FEATURES),
                "sea":sum(1 for f in state.GRID_FEATURES if f["properties"]["is_sea"]),
                "coastal":sum(1 for f in state.GRID_FEATURES if f["properties"]["is_coastal"]),
                "forbidden":sum(1 for f in state.GRID_FEATURES if f["properties"]["is_forbidden"]),
                "maritime":len(state.MARITIME_DATA.get("features",[])),"presence_curves":len(state.PRESENCE_CURVES),
                "weather":fetch_weather()})
        elif path in ("/","/index.html"): self.send_html(FRONTEND_HTML)
        else: self.send_json({"error":"Not found"},404)
