import urllib.request
import json
import time
from threading import Thread
from server import Handler
from http.server import HTTPServer
import state

# Pre-populate state for testing so we don't need to rebuild the huge grid
# We just need some dummy index so a_star doesn't crash, though the default cost handles missing cells.
state.GRID_INDEX = {} 

def run_server():
    httpd = HTTPServer(("127.0.0.1", 8080), Handler)
    httpd.serve_forever()

server_thread = Thread(target=run_server, daemon=True)
server_thread.start()

time.sleep(1) # wait for server to start

# Let's say a drone is at 56.10, 15.55 heading 45 degrees (North East)
url = "http://127.0.0.1:8080/predict?lat=56.10&lon=15.55&heading=45"
try:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
        print("--- TARGET PROBABILITIES ---")
        for k, v in data["target_probabilities"].items():
            print(f"{k}: {v*100:.1f}%")
        print(f"\nTop Target: {data['top_target']['name']}")
        print(f"Generated {len(data['predicted_route'])} waypoints.")
except Exception as e:
    print(f"Failed: {e}")
