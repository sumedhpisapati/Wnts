import re

with open("ui.py", "r", encoding="utf-8") as f:
    content = f.read()

live_track_js = """
// ── Live Tracking ────────────────────────────────────────────────
let lastTrackTime = 0;
async function liveTrackUpdate(lat, lon, heading) {
  const now = Date.now();
  if (now - lastTrackTime < 1000) return; // limit to 1 req per second
  lastTrackTime = now;
  
  try {
    const res = await fetch(`/predict?lat=${lat}&lon=${lon}&heading=${heading}&k=3&analyze_intercept=true&drone_id=live_sim_1`);
    const data = await res.json();
    
    predictLayers.clearLayers();
    
    // Draw uncertainty corridors
    if(data.uncertainty_corridor) {
        data.uncertainty_corridor.forEach(route => {
          L.polyline(route.map(w=>[w.lat, w.lon]), {
            color: '#4444ff', weight: 1, opacity: 0.2, dashArray: '2,2'
          }).addTo(predictLayers);
        });
    }

    // Draw hypotheses
    if(data.hypotheses) {
        data.hypotheses.forEach((h, i) => {
          const col = i === 0 ? '#8888ff' : i === 1 ? '#44ccff' : '#00ffaa';
          const weight = i === 0 ? 3 : 1.5;
          const poly = L.polyline(h.waypoints.map(w=>[w.lat, w.lon]), {
            color: col, weight: weight, opacity: 0.8
          }).addTo(predictLayers);
          poly.bindTooltip(`<div style="font-family:monospace;font-size:10px">
            <b>Hypothesis ${i+1}: ${h.target_name}</b><br>
            Confidence: ${(h.confidence*100).toFixed(1)}%<br>
            Type: ${h.type}</div>`, {sticky: true});
        });
    }
    
    // HUD Update
    if(data.probabilistic_intercept) {
       const pi = data.probabilistic_intercept;
       const ir = document.getElementById('sim-info');
       if (ir) {
           ir.innerHTML = `<b style="color:#00ff88">LIVE TRACKING & PROB. INTERCEPT:</b><br>` +
             `<span style="color:#aaa;font-size:9px">Top Target: ${data.hypotheses[0].target_name} (${(data.hypotheses[0].confidence*100).toFixed(1)}%)</span><br>`+
             `<span style="color:#ffcc00;font-size:9px">Exp. Consequence: ${(pi.expected_consequence*100).toFixed(1)}%</span>`;
       }
    }
  } catch(e) {
    console.error('Live track error:', e);
  }
}
"""

# Insert live_track_js right before runPrediction
content = content.replace("async function runPrediction(){", live_track_js + "\nasync function runPrediction(){")

# Inject call into renderFrame right after droneHeading calculation
inject_call = """
  if(simPlaying && !intercepted) {
     liveTrackUpdate(c.lat, c.lon, droneHeading);
  }
"""
content = content.replace("droneHeading=Math.atan2(nx.lon-c.lon,nx.lat-c.lat)*180/Math.PI;\n  }", 
                          "droneHeading=Math.atan2(nx.lon-c.lon,nx.lat-c.lat)*180/Math.PI;\n  }\n" + inject_call)

with open("ui.py", "w", encoding="utf-8") as f:
    f.write(content)

print("ui.py updated with live tracking integration.")
