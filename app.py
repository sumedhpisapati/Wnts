from core.core_imports import *
from core.config import *
from core import state
from data_processing.data import load_presence_curves, fetch_weather, fetch_maritime, load_scb, fetch_osm, load_ais
from engine.scoring import build_scored_grid
from engine.physics import compute_integrated_debris_risk
from api.server import Handler

if __name__=="__main__":
    PORT=8000
    print("="*64)
    print("  Karlskrona Impact Risk Engine  v4.0  (clean rewrite)")
    print("="*64)

    state.PRESENCE_CURVES.update(load_presence_curves(PRESENCE_CSV))
    print("\n[WEATHER] Fetching ..."); fetch_weather()
    print("\n[MARITIME] Loading ..."); state.MARITIME_DATA.update(fetch_maritime())

    need_build=True
    if GRID_CACHE.exists():
        print(f"\n[GRID] Loading from {GRID_CACHE} ...")
        try:
            with open(GRID_CACHE) as f: state.GRID_FEATURES.extend(json.load(f))
            s=state.GRID_FEATURES[0]["properties"] if state.GRID_FEATURES else {}
            # Validate v4 required fields
            if not {"land_use","is_coastal","wat_frac","integrated_risk"}.issubset(s.keys()):
                print("[GRID] Cache missing v4 fields — rebuilding ...")
                state.GRID_FEATURES.clear()
            else:
                print(f"[GRID] Loaded {len(state.GRID_FEATURES)} cells."); need_build=False
        except Exception as e:
            print(f"[GRID] Cache error: {e} — rebuilding ..."); state.GRID_FEATURES.clear()

    if need_build:
        print("\n[GRID] Building scored grid ...")
        scb=load_scb(); osm=fetch_osm()
        raw_features = build_scored_grid(scb, osm, state.PRESENCE_CURVES)
        compute_integrated_debris_risk(raw_features)
        state.GRID_FEATURES.extend(raw_features)
        print(f"\n[GRID] Saving → {GRID_CACHE} ...")
        with open(GRID_CACHE,"w") as f: json.dump(state.GRID_FEATURES,f,separators=(",",":"))
        print("[GRID] Saved.")

    for feat in state.GRID_FEATURES:
        state.GRID_INDEX[feat["properties"]["cell_id"]]=feat["properties"]

    print(f"\n[AIS] File: {AIS_FILE} ({'found' if AIS_FILE.exists() else 'not found — place in data/ folder'})")
    print(f"\n[SERVER] http://localhost:{PORT}")
    print("[SERVER] Ctrl+C to stop.\n")
    try: HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
    except KeyboardInterrupt: print("\n[SERVER] Stopped.")
