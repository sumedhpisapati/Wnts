import urllib.request
import json
import time
from threading import Thread
from api.server import Handler
from http.server import HTTPServer
from core import state
# Pre-populate state for testing
state.GRID_INDEX = {} 

def run_server():
    httpd = HTTPServer(("127.0.0.1", 8081), Handler)
    httpd.serve_forever()

server_thread = Thread(target=run_server, daemon=True)
server_thread.start()

time.sleep(1) # wait for server to start

# Let's say a drone is at 56.10, 15.55 heading 45 degrees (North East)
url = "http://127.0.0.1:8081/predict?lat=56.10&lon=15.55&heading=45&k=3"
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
        print("--- TARGET PROBABILITIES ---")
        for k, v in data["target_probabilities"].items():
            print(f"{k}: {v*100:.1f}%")
        
        print(f"\n--- HYPOTHESES (Top {len(data['hypotheses'])}) ---")
        for h in data["hypotheses"]:
            print(f"Target: {h['target_name']} | Confidence: {h['confidence']*100:.1f}% | Waypoints: {len(h['waypoints'])}")
            
        print(f"\nGenerated {len(data['uncertainty_corridor'])} Monte Carlo samples for uncertainty corridor.")
except Exception as e:
    print(f"Failed: {e}")
    # Print raw response if it failed
    try:
        with urllib.request.urlopen(url) as r:
            print(r.read().decode())
    except: pass
