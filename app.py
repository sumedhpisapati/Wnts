"""
Karlskrona Impact Risk Engine  v3.0
─────────────────────────────────────
Principled Combination Method (PCM)
  - Multi-Criteria Risk Matrix (base weights per land-use type)
  - Total Expected Liability (TEL) scoring
  - Dynamic multipliers: time-of-day, population, infrastructure
  - Forbidden zones (INF weight) for critical infrastructure
  - Real SCB population from data/befolkning_1km_2025.gpkg
  - Live OSM features via single Overpass query

TEL Score → Unsuitability (0.1–0.9):
  1–20    Low       → 0.10–0.29
  21–100  Medium    → 0.30–0.49
  101–500 High      → 0.50–0.69
  >500    Critical  → 0.70–0.89
  INF     Forbidden → 0.90

Run:
    python app.py
"""

import json, math, os, time, sys, warnings, urllib.request, urllib.parse
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

warnings.filterwarnings("ignore")

# ── Dependency check ───────────────────────────────────────────────────────────
MISSING = []
for pkg in ["h3","numpy","geopandas","pandas","shapely"]:
    try: __import__(pkg)
    except ImportError: MISSING.append(pkg)
if MISSING:
    print(f"\n[ERROR] Missing packages:\n  pip install {' '.join(MISSING)}\n")
    sys.exit(1)

import h3
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

# ── Paths ──────────────────────────────────────────────────────────────────────
SCB_FILE   = Path("data")  / "befolkning_1km_2025.gpkg"
OSM_CACHE  = Path("osm_cache.gpkg")
CACHE_FILE = Path("risk_cache.json")
H3_RES     = 9

# Karlskrona bounding box (S, N, W, E)
BBOX = (56.10, 56.25, 15.45, 15.75)

# ─────────────────────────────────────────────────────────────────────────────
# PRINCIPLED COMBINATION METHOD
# ─────────────────────────────────────────────────────────────────────────────

# Base weights (Wb) — Total Expected Liability units
# Maps OSM/SCB land-use to doctrinal impact category
BASE_WEIGHTS = {
    "water":       1,      # Open sea/lake — low human presence
    "forest":      10,     # Skog/Mark — minimal casualty risk
    "road":        50,     # Väg/E22 — secondary accident risk
    "residential": 200,    # Bebyggelse — high population, non-hardened
    "industrial":  500,    # Industri/Port — hazmat leak potential
    "forbidden":   9999,   # Skyddsobjekt — power plants, fuel storage
}

# TEL → unsuitability score mapping
# TEL ranges map to 0.1–0.9 scale
def tel_to_score(tel):
    if tel >= 9999:
        return 0.90                          # Forbidden
    elif tel > 500:
        return round(0.70 + 0.19 * min((tel - 500) / 4500, 1.0), 3)   # Critical
    elif tel > 100:
        return round(0.50 + 0.19 * ((tel - 100) / 400), 3)             # High
    elif tel > 20:
        return round(0.30 + 0.19 * ((tel - 20) / 80), 3)               # Medium
    else:
        return round(0.10 + 0.19 * (tel / 20), 3)                      # Low

def tel_classification(tel):
    if tel >= 9999: return "Forbidden — zero engagement"
    if tel > 500:   return "Critical — catastrophic risk"
    if tel > 100:   return "High — engagement discouraged"
    if tel > 20:    return "Medium — mission necessity required"
    return "Low — acceptable operational loss"

# Dynamic multiplier tags — OSM amenity/tag → forbidden flag
FORBIDDEN_TAGS = {
    "amenity":  ["fuel", "power", "substation"],
    "power":    ["plant", "substation", "transformer"],
    "man_made": ["storage_tank", "fuel_station", "petroleum_well"],
    "landuse":  ["military"],
    "military": ["naval_base", "airfield", "danger_area"],
}

SENSITIVE_SIGMA = {
    "hospital": 450, "clinic": 320, "doctors": 300, "pharmacy": 250,
    "school": 300, "kindergarten": 270, "university": 350, "college": 320,
    "fire_station": 320, "police": 300, "ambulance_station": 320,
}

# ── Time-of-day multiplier ─────────────────────────────────────────────────────
def time_multiplier():
    """
    Dynamic Factor for time of day.
    Rush hours and daytime = more people exposed outdoors.
    Night = people indoors, lower exposure.
    """
    h = datetime.now().hour
    if 7 <= h <= 9 or 15 <= h <= 18:
        return 1.5    # Rush hour — roads and public spaces busy
    elif 9 <= h <= 15:
        return 1.2    # Working day
    elif 18 <= h <= 22:
        return 1.1    # Evening
    else:
        return 0.8    # Night — lower outdoor exposure

# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    f1, f2 = math.radians(lat1), math.radians(lat2)
    df, dl = math.radians(lat2-lat1), math.radians(lon2-lon1)
    a = math.sin(df/2)**2 + math.cos(f1)*math.cos(f2)*math.sin(dl/2)**2
    return R * 2 * math.asin(math.sqrt(max(0.0, a)))

def gaussian(dist_m, sigma_m):
    return math.exp(-0.5 * (dist_m / sigma_m) ** 2)

def h3_shapely(cell_id):
    bd = h3.cell_to_boundary(cell_id)
    return Polygon([(lon, lat) for lat, lon in bd])

def h3_geojson_ring(cell_id):
    bd = h3.cell_to_boundary(cell_id)
    ring = [[lon, lat] for lat, lon in bd]
    ring.append(ring[0])
    return ring

def normalize_col(arr, p_high=98):
    a = np.array(arr, dtype=float)
    pos = a[a > 0]
    if len(pos) == 0:
        return a * 0.0
    ceiling = float(np.percentile(pos, p_high))
    return np.clip(a / ceiling, 0.0, 1.0) if ceiling > 0 else a * 0.0

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — SCB
# ─────────────────────────────────────────────────────────────────────────────

def load_scb():
    if not SCB_FILE.exists():
        print(f"\n[ERROR] SCB file not found: {SCB_FILE}\n")
        sys.exit(1)

    print(f"[SCB] Reading {SCB_FILE.name} ...")
    gdf = gpd.read_file(SCB_FILE)
    print(f"      CRS: {gdf.crs}  |  rows: {len(gdf)}")

    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:3006")
    if str(gdf.crs).upper() != "EPSG:4326":
        gdf = gdf.to_crs("EPSG:4326")

    S, N, W, E = BBOX
    gdf = gdf[gdf.geometry.intersects(box(W, S, E, N))].copy()
    print(f"      After bbox clip: {len(gdf)} cells")

    # Use beftotalt (total population) — all civilians regardless of age
    pop_col = None
    for c in gdf.columns:
        if any(k in c.lower() for k in ["beftotalt","bef","pop","total","antal"]):
            if c in gdf.select_dtypes(include=[float,int]).columns:
                pop_col = c
                break
    if pop_col is None:
        numeric = [c for c in gdf.select_dtypes(include=[float,int]).columns
                   if "geom" not in c.lower()]
        pop_col = max(numeric, key=lambda c: gdf[c].max()) if numeric else None
    if pop_col is None:
        print("[ERROR] Cannot find population column.")
        sys.exit(1)

    print(f"      Population column: '{pop_col}'  "
          f"max={gdf[pop_col].max():.0f}  total={gdf[pop_col].sum():.0f}")
    gdf["pop"] = pd.to_numeric(gdf[pop_col], errors="coerce").fillna(0.0)
    return gdf[["geometry","pop"]]

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — OSM single Overpass query
# ─────────────────────────────────────────────────────────────────────────────

def fetch_osm():
    if OSM_CACHE.exists():
        print("[OSM] Loading from cache ...")
        return load_osm_cache()

    S, N, W, E = BBOX
    print("[OSM] Fetching all features in one Overpass query ...")
    print("      ~20–60 seconds ...")

    query = f"""
[out:json][timeout:120][bbox:{S},{W},{N},{E}];
(
  way["building"];
  relation["building"];
  way["landuse"~"residential|apartments|commercial|retail|industrial|port|forest|military"];
  node["amenity"~"hospital|clinic|doctors|pharmacy|school|kindergarten|university|college|fire_station|police|ambulance_station|fuel"];
  way["amenity"~"hospital|clinic|doctors|pharmacy|school|kindergarten|university|college|fire_station|police|ambulance_station|fuel"];
  way["highway"~"motorway|trunk|primary|secondary|tertiary|residential|unclassified"];
  way["military"];
  relation["military"];
  way["power"~"plant|substation"];
  node["power"~"plant|substation"];
  way["man_made"~"storage_tank"];
  way["natural"~"water|bay|wood|scrub"];
  relation["natural"~"water|bay"];
  way["waterway"~"river"];
  way["leisure"~"park|garden|recreation_ground"];
);
out body geom qt;
""".strip()

    endpoints = [
        "https://overpass-api.de/api/interpreter",
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]

    data = None
    for ep in endpoints:
        try:
            print(f"      Trying {ep} ...")
            encoded = urllib.parse.urlencode({"data": query}).encode()
            req = urllib.request.Request(
                ep, data=encoded,
                headers={"User-Agent": "KarlskronaRiskEngine/3.0"}
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode("utf-8"))
            print(f"      Got {len(data.get('elements',[]))} elements")
            break
        except Exception as e:
            print(f"      Failed: {e}")

    if data is None:
        print("[ERROR] All Overpass endpoints failed. Check internet connection.")
        sys.exit(1)

    # Buckets
    buildings   = []
    residential = []
    commercial  = []
    industrial  = []
    military    = []
    forbidden   = []   # power plants, fuel storage
    water       = []
    forest      = []
    roads       = []   # (lat,lon) centroids
    sensitive   = []   # (lat,lon,type,name)

    for el in data.get("elements", []):
        geom = element_to_geometry(el)
        if geom is None:
            continue
        tags    = el.get("tags", {})
        amenity = tags.get("amenity", "")
        landuse = tags.get("landuse", "")
        highway = tags.get("highway", "")
        natural = tags.get("natural", "")
        mil     = tags.get("military", "")
        power   = tags.get("power", "")
        man_made= tags.get("man_made", "")
        building= tags.get("building", "")

        # Forbidden first (highest priority)
        if (power in ("plant","substation")
                or man_made in ("storage_tank",)
                or amenity == "fuel"
                or mil in ("naval_base","airfield","danger_area")):
            forbidden.append(geom)

        if amenity in SENSITIVE_SIGMA:
            c = geom.centroid
            sensitive.append((c.y, c.x, amenity, tags.get("name","?")))

        if building:
            buildings.append(geom)
        if landuse in ("residential","apartments"):
            residential.append(geom)
        if landuse in ("commercial","retail"):
            commercial.append(geom)
        if landuse in ("industrial","port") or mil:
            industrial.append(geom)
        if mil:
            military.append(geom)
        if natural in ("water","bay") or tags.get("waterway") == "river":
            water.append(geom)
        if natural in ("wood","scrub") or landuse == "forest":
            forest.append(geom)
        if highway:
            c = geom.centroid
            roads.append((c.y, c.x))

    print(f"      buildings={len(buildings)} residential={len(residential)} "
          f"industrial={len(industrial)}")
    print(f"      forbidden={len(forbidden)} water={len(water)} "
          f"roads={len(roads)} sensitive={len(sensitive)}")

    _save_osm_cache(buildings, residential, commercial, industrial,
                    military, forbidden, water, forest, roads, sensitive)

    return {
        "buildings": buildings, "residential": residential,
        "commercial": commercial, "industrial": industrial,
        "military": military, "forbidden": forbidden,
        "water": water, "forest": forest,
        "roads": roads, "sensitive": sensitive,
    }

def element_to_geometry(el):
    try:
        t = el.get("type")
        if t == "node":
            return Point(el["lon"], el["lat"])
        elif t == "way":
            coords = [(n["lon"],n["lat"]) for n in el.get("geometry",[])]
            if len(coords) < 2:
                return None
            if len(coords) >= 4 and coords[0] == coords[-1]:
                return Polygon(coords)
            from shapely.geometry import LineString
            return LineString(coords)
        elif t == "relation":
            outer = []
            for m in el.get("members",[]):
                if m.get("role") == "outer":
                    outer.extend([(n["lon"],n["lat"]) for n in m.get("geometry",[])])
            if len(outer) >= 4:
                return Polygon(outer)
    except Exception:
        pass
    return None

def _save_osm_cache(buildings, residential, commercial, industrial,
                    military, forbidden, water, forest, roads, sensitive):
    try:
        def _write(geoms, layer):
            if not geoms:
                return
            gpd.GeoDataFrame(
                {"layer": [layer]*len(geoms)},
                geometry=geoms, crs="EPSG:4326"
            ).reset_index(drop=True).to_file(OSM_CACHE, layer=layer, driver="GPKG")

        for name, geoms in [
            ("buildings",residential), ("residential",residential),
            ("commercial",commercial), ("industrial",industrial),
            ("military",military), ("forbidden",forbidden),
            ("water",water), ("forest",forest)
        ]:
            _write(geoms, name)

        if roads:
            gpd.GeoDataFrame(
                {"layer":["roads"]*len(roads)},
                geometry=[Point(lon,lat) for lat,lon in roads],
                crs="EPSG:4326"
            ).reset_index(drop=True).to_file(OSM_CACHE, layer="roads", driver="GPKG")

        if sensitive:
            gpd.GeoDataFrame(
                {"amenity":[s[2] for s in sensitive],
                 "name":   [s[3] for s in sensitive]},
                geometry=[Point(s[1],s[0]) for s in sensitive],
                crs="EPSG:4326"
            ).reset_index(drop=True).to_file(OSM_CACHE, layer="sensitive", driver="GPKG")

        print(f"      OSM cached → {OSM_CACHE}")
    except Exception as e:
        print(f"      [WARN] Cache save failed: {e}")

def load_osm_cache():
    try:
        import fiona
        available = fiona.listlayers(str(OSM_CACHE))
    except Exception:
        available = []
    print(f"      Cached layers: {available}")

    def _geoms(layer):
        if layer not in available: return []
        return list(gpd.read_file(OSM_CACHE, layer=layer).geometry)

    def _pts(layer):
        if layer not in available: return []
        gdf = gpd.read_file(OSM_CACHE, layer=layer)
        return [(r.geometry.y, r.geometry.x) for _,r in gdf.iterrows()]

    sensitive = []
    if "sensitive" in available:
        gdf = gpd.read_file(OSM_CACHE, layer="sensitive")
        for _,row in gdf.iterrows():
            sensitive.append((row.geometry.y, row.geometry.x,
                               row.get("amenity","unknown"),
                               row.get("name","?")))

    return {
        "buildings":   _geoms("buildings"),
        "residential": _geoms("residential"),
        "commercial":  _geoms("commercial"),
        "industrial":  _geoms("industrial"),
        "military":    _geoms("military"),
        "forbidden":   _geoms("forbidden"),
        "water":       _geoms("water"),
        "forest":      _geoms("forest"),
        "roads":       _pts("roads"),
        "sensitive":   sensitive,
    }

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Build scored H3 grid using PCM
# ─────────────────────────────────────────────────────────────────────────────

def build_scored_grid(scb, osm):
    S, N, W, E = BBOX
    cells = list(h3.geo_to_cells(
        {"type":"Polygon","coordinates":[[[W,S],[E,S],[E,N],[W,N],[W,S]]]},
        res=H3_RES
    ))
    print(f"[GRID] {len(cells)} H3 cells at resolution {H3_RES}")

    scb_sindex = scb.sindex if not scb.empty else None

    # Spatial indices for polygon layers
    def make_idx(geoms):
        if not geoms: return None, gpd.GeoDataFrame(crs="EPSG:4326")
        gdf = gpd.GeoDataFrame(geometry=geoms, crs="EPSG:4326")
        return gdf.sindex, gdf

    bld_idx,  bld_gdf  = make_idx(osm["buildings"])
    res_idx,  res_gdf  = make_idx(osm["residential"])
    com_idx,  com_gdf  = make_idx(osm["commercial"])
    ind_idx,  ind_gdf  = make_idx(osm["industrial"])
    for_idx,  for_gdf  = make_idx(osm["forest"])
    wat_idx,  wat_gdf  = make_idx(osm["water"])
    fbd_idx,  fbd_gdf  = make_idx(osm["forbidden"])

    road_pts  = osm["roads"]
    sens_pts  = osm["sensitive"]

    Tf = time_multiplier()
    print(f"[GRID] Time-of-day multiplier: {Tf}x  (hour={datetime.now().hour})")
    print("[GRID] Scoring cells with PCM ...")

    def fast_overlap(sindex, gdf, cell_geom):
        if sindex is None or gdf.empty: return 0.0
        idx = list(sindex.intersection(cell_geom.bounds))
        if not idx: return 0.0
        total = 0.0
        for geom in gdf.iloc[idx][gdf.iloc[idx].geometry.intersects(cell_geom)].geometry:
            try:
                total += geom.buffer(0).intersection(cell_geom).area
            except Exception:
                pass
        return float(min(total / cell_geom.area, 1.0))

    features = []
    t0 = time.time()

    for i, cell in enumerate(cells):
        if i % 300 == 0:
            print(f"       {i}/{len(cells)}  ({100*i//len(cells)}%)  "
                  f"{time.time()-t0:.0f}s")

        cell_geom = h3_shapely(cell)
        clat, clon = h3.cell_to_latlng(cell)

        # ── 1. Forbidden check (INF weight) ───────────────────────────────────
        fbd_frac = fast_overlap(fbd_idx, fbd_gdf, cell_geom)
        is_forbidden = fbd_frac > 0.05   # any meaningful overlap → forbidden

        # ── 2. Determine dominant land-use type ───────────────────────────────
        water_frac = fast_overlap(wat_idx, wat_gdf, cell_geom)
        is_sea     = water_frac > 0.70

        res_frac = fast_overlap(res_idx, res_gdf, cell_geom)
        ind_frac = fast_overlap(ind_idx, ind_gdf, cell_geom)
        for_frac = fast_overlap(for_idx, for_gdf, cell_geom)
        com_frac = fast_overlap(com_idx, com_gdf, cell_geom)

        road_cnt = sum(1 for plat,plon in road_pts
                       if haversine_m(clat,clon,plat,plon) < 300)
        road_frac = min(road_cnt / 20, 1.0)

        # ── 3. SCB population ─────────────────────────────────────────────────
        pop_val = 0.0
        if scb_sindex is not None:
            for _,row in scb.iloc[
                list(scb_sindex.intersection(cell_geom.bounds))
            ].iterrows():
                try:
                    if row.geometry.intersects(cell_geom):
                        frac = row.geometry.intersection(cell_geom).area \
                               / row.geometry.area
                        pop_val += float(row["pop"]) * frac
                except Exception:
                    pass

        # ── 4. Sensitive site influence ───────────────────────────────────────
        sens_score = sum(
            gaussian(haversine_m(clat,clon,s[0],s[1]),
                     SENSITIVE_SIGMA.get(s[2], 320))
            for s in sens_pts
        )

        # ── 5. Compute base TEL (Wb) ──────────────────────────────────────────
        if is_forbidden:
            Wb = BASE_WEIGHTS["forbidden"]
        elif ind_frac > 0.2:
            Wb = BASE_WEIGHTS["industrial"]
        elif res_frac > 0.1:
            Wb = BASE_WEIGHTS["residential"]
        elif road_frac > 0.3:
            Wb = BASE_WEIGHTS["road"]
        elif for_frac > 0.3:
            Wb = BASE_WEIGHTS["forest"]
        elif is_sea:
            Wb = BASE_WEIGHTS["water"]
        else:
            Wb = BASE_WEIGHTS["forest"]   # default: undeveloped land

        # ── 6. Dynamic multipliers ────────────────────────────────────────────
        multipliers = 0.0

        # Population multiplier: every person adds to TEL directly
        # 1 person → +200 TEL,  100 people → +300,  1000+ → maxes out
        if pop_val >= 1:
            pop_mult = 1.0 + math.log1p(pop_val) / math.log1p(10)
            multipliers += pop_mult
        elif pop_val > 0:
            # Even a fraction of a person (overlap calculation) → treat as present
            multipliers += 1.0

        # Sensitive site multiplier
        if sens_score > 0.5:
            multipliers += 1.5
        elif sens_score > 0.1:
            multipliers += 0.8

        # Commercial density multiplier
        if com_frac > 0.2:
            multipliers += 0.5

        # Time-of-day factor applied to outdoor-exposure elements
        # (roads, residential) — not to water/forest
        if Wb in (BASE_WEIGHTS["road"], BASE_WEIGHTS["residential"]):
            multipliers += (Tf - 1.0)

        # ── 7. Final TEL score ────────────────────────────────────────────────
        # Final_Score = Wb × (1 + ΣMultipliers)
        TEL = Wb * (1 + multipliers)

        # Cap forbidden
        if is_forbidden:
            TEL = BASE_WEIGHTS["forbidden"]

        score = tel_to_score(TEL)
        classification = tel_classification(TEL)

        # ── 8. Has any data? ──────────────────────────────────────────────────
        has_data = (
            pop_val > 0 or res_frac > 0 or ind_frac > 0
            or road_cnt > 0 or sens_score > 0 or water_frac > 0.1
            or for_frac > 0.1
        )

        # ── 9. Explanation ────────────────────────────────────────────────────
        parts = []
        if is_forbidden:
            parts.append("FORBIDDEN ZONE — critical infrastructure present")
        if pop_val >= 1:
            parts.append(f"SCB population: {pop_val:.0f} civilians present")
        elif pop_val > 0:
            parts.append("partial SCB population overlap")
        if ind_frac > 0.2:
            parts.append(f"industrial/port land use ({ind_frac*100:.0f}% of cell) — hazmat risk")
        if res_frac > 0.1:
            parts.append(f"residential area ({res_frac*100:.0f}% of cell)")
        if sens_score > 0.1:
            nearest = min(sens_pts, key=lambda s: haversine_m(clat,clon,s[0],s[1]),
                          default=None)
            if nearest:
                parts.append(f"near {nearest[3] if nearest[3]!='?' else nearest[2]}")
        if road_cnt > 5:
            parts.append(f"road network ({road_cnt} segments in range)")
        if not parts:
            parts.append("low exposure — open land or sea")

        explanation = (
            f"TEL={TEL:.0f} | Wb={Wb} | Df={1+multipliers:.2f} | "
            + "; ".join(parts) + "."
        )

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [h3_geojson_ring(cell)]
            },
            "properties": {
                "cell_id":        cell,
                "lat":            round(clat, 6),
                "lon":            round(clon, 6),
                "score":          None if not has_data else score,
                "tel":            round(TEL, 1),
                "wb":             Wb,
                "classification": classification,
                "pop_count":      round(pop_val, 1),
                "res_frac":       round(res_frac, 3),
                "ind_frac":       round(ind_frac, 3),
                "road_count":     road_cnt,
                "sens_score":     round(sens_score, 3),
                "is_forbidden":   is_forbidden,
                "no_data":        not has_data,
                "is_sea":         is_sea,
                "explanation":    explanation,
            }
        })

    nd  = sum(1 for f in features if f["properties"]["no_data"])
    sea = sum(1 for f in features if f["properties"]["is_sea"])
    fbd = sum(1 for f in features if f["properties"]["is_forbidden"])
    print(f"[GRID] Done. {len(features)} cells | "
          f"no-data={nd} | sea={sea} | forbidden={fbd}")
    return features

# ─────────────────────────────────────────────────────────────────────────────
# POINT LOOKUP
# ─────────────────────────────────────────────────────────────────────────────

GRID_INDEX    = {}
GRID_FEATURES = []

def score_point(lat, lon):
    cell_id = h3.latlng_to_cell(lat, lon, H3_RES)
    p = GRID_INDEX.get(cell_id)
    if p is None:
        return None
    return {
        "score":          p["score"],
        "tel":            p["tel"],
        "wb":             p["wb"],
        "classification": p["classification"],
        "cell_id":        cell_id,
        "no_data":        p["no_data"],
        "is_sea":         p["is_sea"],
        "is_forbidden":   p["is_forbidden"],
        "pop_count":      p["pop_count"],
        "contributors": {
            "population":   p["pop_count"],
            "residential":  p["res_frac"],
            "industrial":   p["ind_frac"],
            "roads":        p["road_count"],
            "sensitive":    p["sens_score"],
        },
        "explanation": p["explanation"],
    }

# ─────────────────────────────────────────────────────────────────────────────
# HTTP SERVER
# ─────────────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def send_json(self, data, status=200):
        body = json.dumps(data, separators=(",",":")).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html):
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs     = parse_qs(parsed.query)

        if parsed.path == "/score":
            try:
                lat = float(qs["lat"][0])
                lon = float(qs["lon"][0])
            except (KeyError, ValueError, IndexError):
                self.send_json({"error": "Provide ?lat=...&lon=..."}, 400)
                return
            S, N, W, E = BBOX
            if not (S <= lat <= N and W <= lon <= E):
                self.send_json({"error": "Outside Karlskrona region"}, 404)
                return
            result = score_point(lat, lon)
            if result is None:
                self.send_json({"error": "Cell not found"}, 404)
            else:
                self.send_json(result)

        elif parsed.path == "/cells":
            min_s = float(qs.get("min_score", ["0"])[0])
            filtered = [
                f for f in GRID_FEATURES
                if f["properties"]["no_data"]
                or f["properties"]["is_sea"]
                or (f["properties"]["score"] or 0) >= min_s
            ]
            self.send_json({
                "type":     "FeatureCollection",
                "features": filtered,
                "meta":     {"total": len(filtered)}
            })

        elif parsed.path == "/health":
            self.send_json({
                "status":    "ok",
                "cells":     len(GRID_FEATURES),
                "no_data":   sum(1 for f in GRID_FEATURES if f["properties"].get("no_data")),
                "sea":       sum(1 for f in GRID_FEATURES if f["properties"].get("is_sea")),
                "forbidden": sum(1 for f in GRID_FEATURES if f["properties"].get("is_forbidden")),
            })

        elif parsed.path in ("/", "/index.html"):
            self.send_html(FRONTEND_HTML)

        else:
            self.send_json({"error": "Not found"}, 404)

# ─────────────────────────────────────────────────────────────────────────────
# FRONTEND
# ─────────────────────────────────────────────────────────────────────────────

FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Karlskrona Impact Risk Engine</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#080e14;color:#c8d0d8}
#map{height:100vh;width:100%}
#hud{position:absolute;top:16px;left:16px;z-index:1000;
  background:rgba(6,12,18,0.94);border:1px solid #1a3040;
  border-radius:5px;padding:16px 20px;min-width:300px;max-width:360px}
.hud-sys{font-size:9px;letter-spacing:3px;color:#2a8060;text-transform:uppercase;margin-bottom:2px}
.hud-sub{font-size:8px;color:#1a5040;letter-spacing:2px;margin-bottom:14px}
.score-val{font-size:36px;font-weight:bold;letter-spacing:-1px;line-height:1.1}
.tel-val{font-size:13px;color:#3a7060;margin-top:2px;letter-spacing:1px}
.c-low{color:#4caf50}.c-med{color:#ffc107}.c-high{color:#ff7043}
.c-crit{color:#f44336}.c-fbd{color:#ff00ff}.c-none{color:#444e55}
.lbl{font-size:8px;color:#2a5060;text-transform:uppercase;
     letter-spacing:2px;margin-top:10px;margin-bottom:3px}
.bar-row{display:flex;align-items:center;gap:7px;margin:2px 0}
.bar-name{width:116px;font-size:10px;color:#4a7a8a;flex-shrink:0}
.bar-bg{flex:1;height:4px;background:#0a1820;border-radius:2px}
.bar-fill{height:4px;border-radius:2px;transition:width .35s ease}
.bar-pct{font-size:10px;width:34px;text-align:right;color:#3a6a78}
#expl{font-size:10px;color:#4a7878;margin-top:9px;
  border-top:1px solid #0a1820;padding-top:7px;line-height:1.5}
#cid{font-size:8px;color:#1a4050;margin-top:6px;word-break:break-all}
.idle{color:#1a6050;font-size:11px;letter-spacing:1px}
#legend{position:absolute;bottom:28px;right:16px;z-index:1000;
  background:rgba(6,12,18,0.94);border:1px solid #1a3040;
  border-radius:5px;padding:12px 16px;font-size:10px}
.leg-ttl{font-size:8px;letter-spacing:2px;color:#2a8060;
  text-transform:uppercase;margin-bottom:8px}
.leg-row{display:flex;align-items:center;gap:8px;margin:4px 0;color:#4a7a8a}
.swatch{width:12px;height:8px;border-radius:2px;flex-shrink:0}
#statusbar{position:absolute;bottom:28px;left:16px;z-index:1000;
  background:rgba(6,12,18,0.94);border:1px solid #1a3040;
  border-radius:5px;padding:7px 14px;font-size:8px;
  color:#1a5040;letter-spacing:2px}
.leaflet-container{background:#080e14}
</style>
</head>
<body>
<div id="map"></div>
<div id="hud">
  <div class="hud-sys">&#9670; Karlskrona Impact Risk Engine  v3</div>
  <div class="hud-sub">Principled Combination Method — TEL Scoring</div>
  <div id="result"><div class="idle">Click a cell or map point to query&hellip;</div></div>
</div>
<div id="legend">
  <div class="leg-ttl">TEL Score / Unsuitability</div>
  <div class="leg-row"><div class="swatch" style="background:#0d3d0d"></div>1–20 &nbsp;&nbsp;Low — acceptable</div>
  <div class="leg-row"><div class="swatch" style="background:#7a6000"></div>21–100 &nbsp;Medium — justify</div>
  <div class="leg-row"><div class="swatch" style="background:#8a3200"></div>101–500 High — discouraged</div>
  <div class="leg-row"><div class="swatch" style="background:#8a0000"></div>>500 &nbsp;&nbsp;Critical — forbidden</div>
  <div class="leg-row"><div class="swatch" style="background:#aa00aa"></div>INF &nbsp;&nbsp;&nbsp;Zero-tolerance zone</div>
  <div class="leg-row"><div class="swatch" style="background:#1a1a1a;border:1px solid #333"></div>No data</div>
  <div class="leg-row"><div class="swatch" style="background:transparent;border:1px solid #1a3040"></div>Sea</div>
</div>
<div id="statusbar">LOADING GRID&hellip;</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map=L.map('map',{zoomControl:false}).setView([56.162,15.585],12);
L.control.zoom({position:'bottomright'}).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{
  attribution:'&copy; CartoDB &copy; OSM',maxZoom:19}).addTo(map);

function cellColor(p){
  if(p.is_forbidden) return '#aa00aa';
  if(p.no_data)      return '#1a1a1a';
  if(p.is_sea)       return 'transparent';
  const t=p.tel||0;
  if(t>500)  return '#8a0000';
  if(t>100)  return '#8a3200';
  if(t>20)   return '#7a6000';
  return '#0d3d0d';
}
function cellOpacity(p){
  if(p.is_sea)  return 0.0;
  if(p.no_data) return 0.70;
  return 0.55;
}
function scoreCls(p){
  if(p.is_forbidden) return 'c-fbd';
  if(!p.score)       return 'c-none';
  const s=p.score;
  if(s>=0.7) return 'c-crit';
  if(s>=0.5) return 'c-high';
  if(s>=0.3) return 'c-med';
  return 'c-low';
}
function bar(label, val, max, unit=''){
  const pct = max>0 ? Math.min(Math.round(val/max*100),100) : 0;
  const col  = pct>65?'#f44336':pct>35?'#ff7043':'#4caf50';
  const disp = unit ? `${val}${unit}` : pct+'%';
  return `<div class="bar-row">
    <div class="bar-name">${label}</div>
    <div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${col}"></div></div>
    <div class="bar-pct">${disp}</div></div>`;
}
function renderResult(p){
  const cls=scoreCls(p);
  const scoreStr = p.score!=null ? p.score.toFixed(2) : 'N/A';
  const telStr   = p.tel!=null   ? p.tel.toFixed(0)   : '—';

  let inner='';
  if(p.is_forbidden){
    inner=`<div class="score-val c-fbd">&#9888; FORBIDDEN</div>
           <div class="tel-val">TEL = &#8734; &nbsp;|&nbsp; Wb=${p.wb||9999}</div>
           <div style="color:#aa00aa;font-size:10px;margin-top:4px;letter-spacing:1px">
             ZERO-TOLERANCE ZONE</div>`;
  } else if(p.no_data){
    inner=`<div class="score-val c-none">N/A</div>
           <div style="color:#334;font-size:10px;margin-top:4px">NO DATA COVERAGE</div>`;
  } else if(p.is_sea && !p.score){
    inner=`<div class="score-val c-none">SEA</div>
           <div style="color:#334;font-size:10px;margin-top:4px">TEL = ${telStr} (Wb=1)</div>`;
  } else {
    const c=p.contributors||{};
    inner=`
      <div class="lbl">Unsuitability score</div>
      <div class="score-val ${cls}">${scoreStr}</div>
      <div class="tel-val">TEL = ${telStr} &nbsp;|&nbsp; Wb=${p.wb||'?'}</div>
      <div style="color:#2a7060;font-size:10px;letter-spacing:1px;margin-top:3px">
        ${(p.classification||'').toUpperCase()}</div>
      ${p.pop_count>=1?`<div style="color:#2a8060;font-size:10px;margin-top:4px">
        &#9632; ${Math.round(p.pop_count)} civilians in cell (SCB)</div>`:''}
      <div class="lbl" style="margin-top:11px">Contributing factors</div>
      ${bar('Population', p.pop_count||0, 500, ' ppl')}
      ${bar('Residential', (c.residential||0)*100, 100, '%')}
      ${bar('Industrial', (c.industrial||0)*100, 100, '%')}
      ${bar('Road density', c.roads||0, 30, ' seg')}
      ${bar('Sensitive sites', Math.min((c.sensitive||0)*100,100), 100, '%')}`;
  }
  document.getElementById('result').innerHTML = inner +
    `<div id="expl">${p.explanation||''}</div>
     <div id="cid">&#9632; ${typeof p.lat==='number'?p.lat.toFixed(5):'?'}N
       ${typeof p.lon==='number'?p.lon.toFixed(5):'?'}E &nbsp;|&nbsp; ${p.cell_id||''}</div>`;
}

let selected=null;
async function loadGrid(){
  try{
    const res=await fetch('/cells');
    const data=await res.json();
    L.geoJSON(data,{
      style:f=>{
        const p=f.properties;
        return{fillColor:cellColor(p),fillOpacity:cellOpacity(p),
               color:p.is_forbidden?'#cc00cc':p.no_data?'#2a2a2a':'#000',
               weight:p.is_forbidden?1.5:p.no_data?0.5:0.25,
               opacity:p.is_sea?0:0.4};
      },
      onEachFeature:(feat,lyr)=>{
        lyr.on('click',e=>{
          L.DomEvent.stopPropagation(e);
          if(selected) selected.setStyle({weight:0.25,color:'#000'});
          lyr.setStyle({weight:2,color:'#00ffaa'});
          selected=lyr;
          renderResult(feat.properties);
        });
      }
    }).addTo(map);
    const n=data.features.length;
    const nd=data.features.filter(f=>f.properties.no_data).length;
    const sea=data.features.filter(f=>f.properties.is_sea).length;
    const fbd=data.features.filter(f=>f.properties.is_forbidden).length;
    document.getElementById('statusbar').textContent=
      `PCM GRID \u2022 ${n} CELLS \u2022 FORBIDDEN:${fbd} \u2022 NO-DATA:${nd} \u2022 SEA:${sea} \u2022 H3 RES-9`;
  }catch(e){
    document.getElementById('statusbar').textContent='GRID LOAD FAILED';
    console.error(e);
  }
}
map.on('click',async e=>{
  const{lat,lng}=e.latlng;
  document.getElementById('result').innerHTML=
    `<div class="idle">Querying (${lat.toFixed(4)}, ${lng.toFixed(4)})&hellip;</div>`;
  try{
    const res=await fetch(`/score?lat=${lat}&lon=${lng}`);
    if(!res.ok){
      document.getElementById('result').innerHTML=
        `<div style="color:#f44">Outside scored region</div>`;return;
    }
    renderResult(await res.json());
  }catch{
    document.getElementById('result').innerHTML=
      `<div style="color:#f44">API unreachable</div>`;
  }
});
loadGrid();
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    PORT = 8000
    print("="*60)
    print("  Karlskrona Impact Risk Engine  v3.0")
    print("  Principled Combination Method (PCM)")
    print("="*60)

    if CACHE_FILE.exists():
        print(f"\nLoading cached grid from {CACHE_FILE} ...")
        with open(CACHE_FILE) as f:
            GRID_FEATURES.extend(json.load(f))
        print(f"Loaded {len(GRID_FEATURES)} cells.\n")
    else:
        print("\n[PIPELINE] Starting ...")
        scb      = load_scb()
        osm      = fetch_osm()
        features = build_scored_grid(scb, osm)
        GRID_FEATURES.extend(features)
        print(f"\nSaving cache → {CACHE_FILE} ...")
        with open(CACHE_FILE, "w") as f:
            json.dump(GRID_FEATURES, f, separators=(",",":"))
        print("Cache saved.")

    for feat in GRID_FEATURES:
        p = feat["properties"]
        GRID_INDEX[p["cell_id"]] = p

    print(f"\nStarting server → http://localhost:{PORT}")
    print("Open your browser:  http://localhost:8000")
    print("Press Ctrl+C to stop.\n")
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")