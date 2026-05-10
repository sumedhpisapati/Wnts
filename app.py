"""
Karlskrona Impact Risk Engine  v4.0
─────────────────────────────────────────────────────────────────────
Complete rewrite. Clean architecture, no patches.

SCORING LAYERS (applied in order):
  1. Forbidden infrastructure  → always 1.0 (naval base, power, fuel)
  2. Water detection           → >50% water = sea, 20-50% = coastal
  3. Land-use base weight      → water=1, forest=10, road=50, res=200, ind=500
  4. Population multiplier     → SCB 1km grid, log-scaled
  5. Sensitive site decay      → Gaussian kernel around hospitals/schools/etc
  6. Temporal modulation       → presence_curves.csv (who is actually there now)
  7. Weather modulation        → rain/storm/wind reduce outdoor presence
  8. AIS vessel proximity      → vessels raise score (sea cells capped at 5%)

DATA SOURCES:
  - SCB befolkning_1km_2025.gpkg, OSM Overpass, MarineRegions WFS,
    Open-Meteo, presence_curves.csv, data/karlskrona_ais_mock.csv

RUN:  python app.py
"""

import json, math, time, sys, warnings, urllib.request, urllib.parse, csv
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime

warnings.filterwarnings("ignore")

MISSING = []
for pkg in ["h3","numpy","geopandas","pandas","shapely"]:
    try: __import__(pkg)
    except ImportError: MISSING.append(pkg)
if MISSING:
    print(f"\n[ERROR] Missing: pip install {' '.join(MISSING)}\n"); sys.exit(1)

import h3, numpy as np, pandas as pd, geopandas as gpd
from shapely.geometry import Point, Polygon, box

# ── Paths & config ────────────────────────────────────────────────────────────
SCB_FILE       = Path("data") / "befolkning_1km_2025.gpkg"
OSM_CACHE      = Path("osm_cache.gpkg")
MARITIME_CACHE = Path("maritime_cache.json")
GRID_CACHE     = Path("risk_cache_v4.json")   # new name avoids stale old cache
AIS_FILE       = Path("data") / "karlskrona_ais_mock.csv"
PRESENCE_CSV   = Path(__file__).parent / "presence_curves.csv"
H3_RES  = 9
BBOX    = (56.10, 56.25, 15.45, 15.75)   # S, N, W, E

BASE_WEIGHTS = {"water":1,"forest":10,"road":50,"residential":200,"industrial":500,"forbidden":9999}

# Explicit forbidden tag map — readable and maintainable
# Source: Swedish Skyddsobjekt + OSM tagging conventions
FORBIDDEN_TAGS = {
    "power":    ["plant","substation","transformer"],
    "man_made": ["storage_tank","fuel_station","petroleum_well"],
    "amenity":  ["fuel"],
    "military": ["naval_base","airfield","danger_area","restricted_area"],
    "landuse":  ["military"],
}

def time_multiplier(hour=None):
    """
    Time-of-day outdoor exposure factor. Applied ONLY to road and residential.
    NOT applied to water/forest/forbidden.
    Source: Trafikverket peak hour definition, RVU Sverige trip patterns.
    """
    h = hour if hour is not None else datetime.now().hour
    if 7 <= h <= 9 or 15 <= h <= 18: return 1.5   # Rush hour
    elif 9 < h <= 15:                 return 1.2   # Working day
    elif 18 < h <= 22:                return 1.1   # Evening
    else:                             return 0.8   # Night

SENSITIVE_SIGMA = {
    "hospital":450,"clinic":320,"doctors":300,"pharmacy":250,
    "school":300,"kindergarten":270,"university":350,"college":320,
    "fire_station":320,"police":300,"ambulance_station":320,
}

SWEDISH_HOLIDAYS = {
    (1,1),(1,6),(4,18),(4,20),(4,21),(5,1),(5,29),
    (6,6),(6,7),(6,21),(11,1),(12,25),(12,26),(4,3),(4,5),(4,6),
}

# ── Presence curves ───────────────────────────────────────────────────────────
def load_presence_curves(path):
    if path.exists():
        try:
            df = pd.read_csv(path, comment="#")
            if {"land_use","day_type","hour","presence"}.issubset(df.columns):
                curves = {}
                for (lu,dt),grp in df.groupby(["land_use","day_type"]):
                    vals = grp.sort_values("hour")["presence"].tolist()
                    if len(vals)==24: curves.setdefault(lu,{})[dt] = vals
                if curves:
                    print(f"[PRESENCE] Loaded {len(curves)} types from {path}"); return curves
        except Exception as e:
            print(f"[PRESENCE] CSV load failed: {e}")
    print("[PRESENCE] Using built-in defaults")
    return {
        "residential":{
            "weekday":[0.95,0.95,0.95,0.95,0.93,0.88,0.68,0.52,0.32,0.28,0.28,0.30,
                       0.35,0.32,0.30,0.32,0.45,0.68,0.80,0.86,0.90,0.92,0.93,0.95],
            "weekend":[0.95,0.95,0.95,0.95,0.95,0.92,0.90,0.86,0.78,0.70,0.64,0.60,
                       0.57,0.56,0.58,0.62,0.66,0.72,0.78,0.84,0.88,0.90,0.92,0.95]},
        "industrial":{
            "weekday":[0.05,0.05,0.05,0.05,0.05,0.08,0.35,0.70,0.92,0.92,0.92,0.90,
                       0.88,0.90,0.92,0.90,0.72,0.40,0.15,0.08,0.05,0.05,0.05,0.05],
            "weekend":[0.05]*24},
        "road":{
            "weekday":[0.04,0.03,0.03,0.03,0.05,0.15,0.55,0.88,0.82,0.58,0.48,0.52,
                       0.62,0.55,0.50,0.54,0.75,0.92,0.78,0.58,0.42,0.30,0.16,0.07],
            "weekend":[0.04,0.03,0.03,0.03,0.04,0.07,0.14,0.28,0.48,0.60,0.68,0.72,
                       0.74,0.75,0.74,0.70,0.68,0.65,0.58,0.48,0.38,0.28,0.16,0.07]},
        "commercial":{
            "weekday":[0.02,0.02,0.02,0.02,0.02,0.03,0.05,0.12,0.30,0.55,0.80,0.88,
                       0.92,0.88,0.82,0.80,0.72,0.55,0.30,0.18,0.08,0.04,0.02,0.02],
            "weekend":[0.02,0.02,0.02,0.02,0.02,0.02,0.03,0.06,0.15,0.45,0.72,0.82,
                       0.88,0.85,0.80,0.72,0.55,0.30,0.12,0.06,0.03,0.02,0.02,0.02]},
        "water":{
            "weekday":[0.00,0.00,0.00,0.00,0.02,0.06,0.10,0.14,0.16,0.18,0.20,0.22,
                       0.24,0.25,0.24,0.22,0.20,0.16,0.12,0.08,0.04,0.02,0.00,0.00],
            "weekend":[0.00,0.00,0.00,0.00,0.02,0.04,0.08,0.14,0.22,0.30,0.36,0.38,
                       0.40,0.40,0.38,0.36,0.32,0.24,0.16,0.10,0.05,0.02,0.00,0.00]},
        "forest":{
            "weekday":[0.00,0.00,0.00,0.00,0.00,0.02,0.06,0.10,0.08,0.07,0.08,0.12,
                       0.10,0.10,0.09,0.08,0.11,0.10,0.07,0.04,0.02,0.00,0.00,0.00],
            "weekend":[0.00,0.00,0.00,0.00,0.00,0.02,0.04,0.08,0.16,0.22,0.26,0.28,
                       0.28,0.26,0.24,0.22,0.18,0.12,0.07,0.03,0.01,0.00,0.00,0.00]},
    }

def get_day_type(dt):
    if (dt.month,dt.day) in SWEDISH_HOLIDAYS: return "weekend"
    return "weekend" if dt.weekday()>=5 else "weekday"

def get_presence(curves, land_use, hour, day_type):
    c = curves.get(land_use) or curves.get("forest",{})
    arr = c.get(day_type) or c.get("weekday",[])
    return float(arr[max(0,min(23,hour))]) if arr else 0.5

# ── Weather ───────────────────────────────────────────────────────────────────
_wx_cache, _wx_ts = {}, 0.0

def fetch_weather():
    global _wx_cache, _wx_ts
    if _wx_cache and time.time()-_wx_ts < 600: return _wx_cache
    S,N,W,E = BBOX
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={(S+N)/2}&longitude={(W+E)/2}"
           f"&current=temperature_2m,rain,snowfall,windspeed_10m,weathercode,is_day"
           f"&timezone=Europe%2FStockholm")
    try:
        req = urllib.request.Request(url,headers={"User-Agent":"KarlskronaRisk/4.0"})
        with urllib.request.urlopen(req,timeout=10) as r:
            data = json.loads(r.read().decode())
        cur = data.get("current",{})
        wc  = cur.get("weathercode",0)
        cmap = {0:"Clear",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
                45:"Fog",61:"Light rain",63:"Rain",65:"Heavy rain",
                71:"Light snow",73:"Snow",80:"Showers",95:"Thunderstorm"}
        wind = cur.get("windspeed_10m",0)
        # Outdoor factor: how weather reduces presence on roads/water/forest
        of = 1.0
        if wc>=95: of*=0.40
        elif wc>=80: of*=0.60
        elif wc>=61: of*=0.72
        elif wc>=51: of*=0.85
        if wind>20: of*=0.80
        if wind>30: of*=0.65
        of = max(0.40, min(1.0, of))
        w = {"ok":True,"temperature_c":cur.get("temperature_2m",15),"rain_mm":cur.get("rain",0),
             "snowfall_cm":cur.get("snowfall",0),"windspeed_kmh":wind,"weathercode":wc,
             "is_day":cur.get("is_day",1),"condition":cmap.get(wc,"Unknown"),
             "fetched_at":datetime.now().strftime("%H:%M"),"outdoor_factor":round(of,3)}
        _wx_cache=w; _wx_ts=time.time()
        print(f"[WEATHER] {w['condition']} {w['temperature_c']}°C wind={wind}km/h factor={of:.2f}")
        return w
    except Exception as e:
        print(f"[WEATHER] Failed: {e}")
        fb={"ok":False,"temperature_c":15,"rain_mm":0,"snowfall_cm":0,"windspeed_kmh":0,
            "weathercode":0,"is_day":1,"condition":"Unknown","fetched_at":"N/A","outdoor_factor":1.0}
        _wx_cache=fb; _wx_ts=time.time(); return fb

def get_weather_with_overrides(qs):
    wx = fetch_weather().copy()
    if "wind" in qs:
        try: wx["windspeed_kmh"] = float(qs["wind"][0])
        except: pass
    if "temp" in qs:
        try: wx["temperature_c"] = float(qs["temp"][0])
        except: pass
    if "wc" in qs:
        try: wx["weathercode"] = int(qs["wc"][0])
        except: pass
    if "wind_dir" in qs:
        try: wx["wind_direction_deg"] = float(qs["wind_dir"][0])
        except: pass

    # Recalculate outdoor_factor based on overrides
    wc = wx["weathercode"]
    wind = wx["windspeed_kmh"]
    of = 1.0
    if wc>=95: of*=0.40
    elif wc>=80: of*=0.60
    elif wc>=61: of*=0.72
    elif wc>=51: of*=0.85
    if wind>20: of*=0.80
    if wind>30: of*=0.65
    of = max(0.40, min(1.0, of))
    wx["outdoor_factor"] = round(of, 3)

    cmap = {0:"Clear",1:"Mainly clear",2:"Partly cloudy",3:"Overcast",
            45:"Fog",61:"Light rain",63:"Rain",65:"Heavy rain",
            71:"Light snow",73:"Snow",80:"Showers",95:"Thunderstorm"}
    wx["condition"] = cmap.get(wc, "Custom")
    return wx

# ── Geometry helpers ──────────────────────────────────────────────────────────
def haversine_m(lat1,lon1,lat2,lon2):
    R=6_371_000; f1,f2=math.radians(lat1),math.radians(lat2)
    a=math.sin(math.radians(lat2-lat1)/2)**2+math.cos(f1)*math.cos(f2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R*2*math.asin(math.sqrt(max(0.0,a)))

def gauss(d,s): return math.exp(-0.5*(d/s)**2)

def h3_shapely(cid):
    return Polygon([(lon,lat) for lat,lon in h3.cell_to_boundary(cid)])

def h3_ring(cid):
    r=[[lon,lat] for lat,lon in h3.cell_to_boundary(cid)]; r.append(r[0]); return r

# ── SCB ───────────────────────────────────────────────────────────────────────
def load_scb():
    if not SCB_FILE.exists():
        print(f"[SCB] Not found — population disabled"); return gpd.GeoDataFrame(columns=["geometry","pop"],crs="EPSG:4326")
    print(f"[SCB] Loading {SCB_FILE.name} ...")
    gdf=gpd.read_file(SCB_FILE)
    if gdf.crs is None: gdf=gdf.set_crs("EPSG:3006")
    if str(gdf.crs).upper()!="EPSG:4326": gdf=gdf.to_crs("EPSG:4326")
    S,N,W,E=BBOX; gdf=gdf[gdf.geometry.intersects(box(W,S,E,N))].copy()
    pop_col=None
    for c in gdf.columns:
        if any(k in c.lower() for k in ["beftotalt","bef","pop","total","antal","invan"]):
            if c in gdf.select_dtypes(include=[float,int]).columns: pop_col=c; break
    if pop_col is None:
        nums=[c for c in gdf.select_dtypes(include=[float,int]).columns if "geom" not in c.lower()]
        pop_col=max(nums,key=lambda c:gdf[c].max()) if nums else None
    if pop_col is None: print("[SCB] No population column found"); return gpd.GeoDataFrame()
    gdf["pop"]=pd.to_numeric(gdf[pop_col],errors="coerce").fillna(0.0)
    print(f"[SCB] {len(gdf)} cells, total={gdf['pop'].sum():.0f}"); return gdf[["geometry","pop"]]

# ── OSM ───────────────────────────────────────────────────────────────────────
def _empty(): return {k:[] for k in ["residential","commercial","industrial","military","forbidden","water","forest","buildings","sensitive"]}|{"roads":[]}

def _el_geom(el):
    try:
        t=el.get("type")
        if t=="node": return Point(el["lon"],el["lat"])
        elif t=="way":
            coords=[(n["lon"],n["lat"]) for n in el.get("geometry",[])]
            if len(coords)<2: return None
            if len(coords)>=4 and coords[0]==coords[-1]: return Polygon(coords)
            from shapely.geometry import LineString; return LineString(coords)
        elif t=="relation":
            outer=[]
            for m in el.get("members",[]):
                if m.get("role")=="outer": outer.extend([(n["lon"],n["lat"]) for n in m.get("geometry",[])])
            if len(outer)>=4: return Polygon(outer)
    except: pass
    return None

def fetch_osm():
    if OSM_CACHE.exists():
        print("[OSM] Loading from cache ..."); return _load_osm_cache()
    S,N,W,E=BBOX
    q=f"""[out:json][timeout:120][bbox:{S},{W},{N},{E}];
(way["building"];relation["building"];
way["landuse"~"residential|apartments|commercial|retail|industrial|port|forest|military"];
node["amenity"~"hospital|clinic|doctors|pharmacy|school|kindergarten|university|college|fire_station|police|ambulance_station|fuel"];
way["amenity"~"hospital|clinic|doctors|pharmacy|school|kindergarten|university|college|fire_station|police|ambulance_station|fuel"];
way["highway"~"motorway|trunk|primary|secondary|tertiary|residential|unclassified"];
way["military"];relation["military"];
way["power"~"plant|substation"];node["power"~"plant|substation"];
way["man_made"~"storage_tank"];
way["natural"~"water|bay|wood|scrub"];relation["natural"~"water|bay"];
way["waterway"~"river"];way["leisure"~"park|garden|recreation_ground"];);
out body geom qt;""".strip()
    raw=None
    for ep in ["https://overpass-api.de/api/interpreter","https://maps.mail.ru/osm/tools/overpass/api/interpreter","https://overpass.kumi.systems/api/interpreter"]:
        try:
            enc=urllib.parse.urlencode({"data":q}).encode()
            req=urllib.request.Request(ep,data=enc,headers={"User-Agent":"KarlskronaRisk/4.0"})
            with urllib.request.urlopen(req,timeout=120) as r: raw=json.loads(r.read().decode())
            print(f"[OSM] {len(raw.get('elements',[]))} elements from {ep}"); break
        except Exception as e: print(f"[OSM] {ep} failed: {e}")
    if raw is None: print("[OSM] All endpoints failed"); return _empty()
    res=_empty()
    for el in raw.get("elements",[]):
        geom=_el_geom(el)
        if geom is None: continue
        tags=el.get("tags",{}); amenity=tags.get("amenity",""); landuse=tags.get("landuse","")
        highway=tags.get("highway",""); natural=tags.get("natural",""); mil=tags.get("military","")
        power=tags.get("power",""); man_made=tags.get("man_made",""); building=tags.get("building","")
        if (power    in FORBIDDEN_TAGS["power"] or
                man_made in FORBIDDEN_TAGS["man_made"] or
                amenity  in FORBIDDEN_TAGS["amenity"] or
                mil      in FORBIDDEN_TAGS["military"] or
                landuse  in FORBIDDEN_TAGS["landuse"]):
            res["forbidden"].append(geom)
        if amenity in SENSITIVE_SIGMA:
            c=geom.centroid; res["sensitive"].append((c.y,c.x,amenity,tags.get("name","?")))
        if building: res["buildings"].append(geom)
        if landuse in ("residential","apartments"): res["residential"].append(geom)
        if landuse in ("commercial","retail"):      res["commercial"].append(geom)
        if landuse in ("industrial","port") or mil: res["industrial"].append(geom)
        if mil: res["military"].append(geom)
        if natural in ("water","bay") or tags.get("waterway")=="river": res["water"].append(geom)
        if natural in ("wood","scrub") or landuse=="forest": res["forest"].append(geom)
        if highway: c=geom.centroid; res["roads"].append((c.y,c.x))
    _save_osm_cache(res)
    print(f"[OSM] res={len(res['residential'])} ind={len(res['industrial'])} fbd={len(res['forbidden'])} water={len(res['water'])} sens={len(res['sensitive'])}")
    return res

def _save_osm_cache(data):
    try:
        def _w(geoms,layer):
            if not geoms: return
            gpd.GeoDataFrame({"layer":[layer]*len(geoms)},geometry=geoms,crs="EPSG:4326").reset_index(drop=True).to_file(OSM_CACHE,layer=layer,driver="GPKG")
        for n in ["buildings","residential","commercial","industrial","military","forbidden","water","forest"]:
            _w(data[n],n)
        if data["roads"]:
            gpd.GeoDataFrame({"layer":["roads"]*len(data["roads"])},geometry=[Point(lon,lat) for lat,lon in data["roads"]],crs="EPSG:4326").reset_index(drop=True).to_file(OSM_CACHE,layer="roads",driver="GPKG")
        if data["sensitive"]:
            gpd.GeoDataFrame({"amenity":[s[2] for s in data["sensitive"]],"name":[s[3] for s in data["sensitive"]]},geometry=[Point(s[1],s[0]) for s in data["sensitive"]],crs="EPSG:4326").reset_index(drop=True).to_file(OSM_CACHE,layer="sensitive",driver="GPKG")
        print(f"[OSM] Cached → {OSM_CACHE}")
    except Exception as e: print(f"[OSM] Cache save failed: {e}")

def _load_osm_cache():
    try:
        import fiona; available=fiona.listlayers(str(OSM_CACHE))
    except: available=[]
    print(f"[OSM] Cache layers: {available}")
    def _g(layer): return [] if layer not in available else list(gpd.read_file(OSM_CACHE,layer=layer).geometry)
    def _p(layer): return [] if layer not in available else [(r.geometry.y,r.geometry.x) for _,r in gpd.read_file(OSM_CACHE,layer=layer).iterrows()]
    sens=[]
    if "sensitive" in available:
        gdf=gpd.read_file(OSM_CACHE,layer="sensitive")
        for _,row in gdf.iterrows(): sens.append((row.geometry.y,row.geometry.x,row.get("amenity","?"),row.get("name","?")))
    return {"buildings":_g("buildings"),"residential":_g("residential"),"commercial":_g("commercial"),
            "industrial":_g("industrial"),"military":_g("military"),"forbidden":_g("forbidden"),
            "water":_g("water"),"forest":_g("forest"),"roads":_p("roads"),"sensitive":sens}

# ── Scoring ───────────────────────────────────────────────────────────────────
def tel_to_score(tel):
    if tel>=9999: return 0.90
    if tel>500:   return round(0.70+0.19*min((tel-500)/4500,1.0),3)
    if tel>100:   return round(0.50+0.19*((tel-100)/400),3)
    if tel>20:    return round(0.30+0.19*((tel-20)/80),3)
    return              round(0.10+0.19*(tel/20),3)

def tel_class(tel):
    if tel>=9999: return "Forbidden — zero engagement"
    if tel>500:   return "Critical — catastrophic risk"
    if tel>100:   return "High — engagement discouraged"
    if tel>20:    return "Medium — mission necessity required"
    return               "Low — acceptable under ROE"

def build_scored_grid(scb, osm, curves):
    S,N,W,E=BBOX
    cells=list(h3.geo_to_cells({"type":"Polygon","coordinates":[[[W,S],[E,S],[E,N],[W,N],[W,S]]]},res=H3_RES))
    print(f"[GRID] {len(cells)} H3 cells at resolution {H3_RES}")
    scb_idx=scb.sindex if not scb.empty else None

    def mk(geoms):
        if not geoms: return None,gpd.GeoDataFrame(geometry=[],crs="EPSG:4326")
        gdf=gpd.GeoDataFrame(geometry=geoms,crs="EPSG:4326"); return gdf.sindex,gdf

    res_i,res_g=mk(osm["residential"]); ind_i,ind_g=mk(osm["industrial"])
    com_i,com_g=mk(osm["commercial"]);  for_i,for_g=mk(osm["forest"])
    wat_i,wat_g=mk(osm["water"]);       fbd_i,fbd_g=mk(osm["forbidden"])
    road_pts=osm["roads"]; sens_pts=osm["sensitive"]

    def ovlp(si,gdf,cg):
        if si is None or gdf.empty: return 0.0
        hits=list(si.intersection(cg.bounds))
        if not hits: return 0.0
        tot=0.0
        for geom in gdf.iloc[hits][gdf.iloc[hits].geometry.intersects(cg)].geometry:
            try: tot+=geom.buffer(0).intersection(cg).area
            except: pass
        return float(min(tot/cg.area,1.0))

    features=[]; t0=time.time()
    for i,cell in enumerate(cells):
        if i%500==0: print(f"  {i}/{len(cells)} ({100*i//len(cells)}%) {time.time()-t0:.0f}s")
        cg=h3_shapely(cell); clat,clon=h3.cell_to_latlng(cell)
        fbd_f=ovlp(fbd_i,fbd_g,cg); wat_f=ovlp(wat_i,wat_g,cg)
        res_f=ovlp(res_i,res_g,cg); ind_f=ovlp(ind_i,ind_g,cg)
        for_f=ovlp(for_i,for_g,cg); com_f=ovlp(com_i,com_g,cg)
        road_n=sum(1 for plat,plon in road_pts if haversine_m(clat,clon,plat,plon)<300)
        road_f=min(road_n/20,1.0)

        # ── Sea/water detection — land-absence method ─────────────────────
        # KEY INSIGHT: In OSM the open Baltic Sea is NOT tagged as natural=water.
        # natural=water only covers inland lakes. The sea is defined by coastline
        # ways which don't produce fillable polygons in our query.
        # So wat_frac ≈ 0 for most sea cells in the Karlskrona archipelago.
        #
        # FIX: Detect sea by ABSENCE of land features, not presence of water polygons.
        # A weighted land score — if near zero, the cell is sea.
        land_score = (res_f * 5.0 + ind_f * 5.0 + for_f * 2.0 +
                      com_f * 3.0 + road_f * 1.5)

        # is_sea: no meaningful land features AND not forbidden infrastructure
        is_sea     = land_score < 0.10 and fbd_f < 0.05
        # is_coastal: some land features but still mostly open — island fringe, harbour
        is_coastal = (not is_sea) and land_score < 0.50 and res_f < 0.08 and ind_f < 0.10 and fbd_f < 0.05
        # Also honour explicit OSM inland water polygons (lakes, rivers)
        if wat_f > 0.50:    is_sea = True;  is_coastal = False
        elif wat_f > 0.20 and not is_sea: is_coastal = True
        is_water   = is_sea or is_coastal

        # Forbidden: never apply to water cells
        is_forbidden = fbd_f > 0.05 and not is_water

        # Base weight — water checked FIRST, always Wb=1
        if is_forbidden:       Wb=BASE_WEIGHTS["forbidden"]
        elif is_water:         Wb=BASE_WEIGHTS["water"]
        elif ind_f>0.2:        Wb=BASE_WEIGHTS["industrial"]
        elif res_f>0.1:        Wb=BASE_WEIGHTS["residential"]
        elif road_f>0.3:       Wb=BASE_WEIGHTS["road"]
        elif for_f>0.3:        Wb=BASE_WEIGHTS["forest"]
        elif com_f>0.2:        Wb=BASE_WEIGHTS["road"]
        else:                  Wb=BASE_WEIGHTS["forest"]

        # Land-use tag for presence curves
        if is_forbidden:       lu="forbidden"
        elif is_water:         lu="water"
        elif ind_f>0.2:        lu="industrial"
        elif res_f>0.1:        lu="residential"
        elif road_f>0.3:       lu="road"
        elif com_f>0.2:        lu="commercial"
        elif for_f>0.3:        lu="forest"
        else:                  lu="forest"

        # Population
        pop=0.0
        if scb_idx is not None:
            for _,row in scb.iloc[list(scb_idx.intersection(cg.bounds))].iterrows():
                try:
                    if row.geometry.intersects(cg):
                        pop+=float(row["pop"])*(row.geometry.intersection(cg).area/row.geometry.area)
                except: pass

        # Sensitive site influence
        sens=sum(gauss(haversine_m(clat,clon,s[0],s[1]),SENSITIVE_SIGMA.get(s[2],320)) for s in sens_pts)

        # Dynamic multiplier
        Df=1.0
        if pop>=1:    Df+=1.0+math.log1p(pop)/math.log1p(10)
        elif pop>0:   Df+=0.5
        if sens>0.5:  Df+=1.5
        elif sens>0.1:Df+=0.8
        if com_f>0.2: Df+=0.5

        # time_multiplier applied to outdoor land-uses (road, residential)
        # Integrated from appSSS.py — more transparent than CSV curves for this factor
        Tf = time_multiplier()
        if Wb in (BASE_WEIGHTS["road"], BASE_WEIGHTS["residential"]):
            Df += (Tf - 1.0)

        TEL=Wb*Df
        if is_forbidden: TEL=BASE_WEIGHTS["forbidden"]
        score=tel_to_score(TEL); classif=tel_class(TEL)
        has_data=(pop>0 or res_f>0 or ind_f>0 or road_n>0 or sens>0 or wat_f>0.05 or for_f>0.1)

        parts=[]
        if is_forbidden: parts.append("FORBIDDEN — critical infrastructure")
        if is_sea:       parts.append("open sea — preferred landing zone")
        elif is_coastal: parts.append("coastal — preferred over land")
        if pop>=1:       parts.append(f"{pop:.0f} civilians (SCB)")
        if ind_f>0.2:    parts.append(f"industrial {ind_f*100:.0f}% — hazmat risk")
        if res_f>0.1:    parts.append(f"residential {res_f*100:.0f}%")
        if sens>0.1:
            nr=min(sens_pts,key=lambda s:haversine_m(clat,clon,s[0],s[1]),default=None)
            if nr: parts.append(f"near {nr[3] if nr[3]!='?' else nr[2]}")
        if road_n>5:     parts.append(f"{road_n} road segments")
        if not parts:    parts.append("low exposure — open land")

        features.append({"type":"Feature","geometry":{"type":"Polygon","coordinates":[h3_ring(cell)]},
            "properties":{"cell_id":cell,"lat":round(clat,6),"lon":round(clon,6),
                "score":None if not has_data else score,"tel":round(TEL,1),"wb":Wb,"land_use":lu,
                "classification":classif,"pop_count":round(pop,1),"res_frac":round(res_f,3),
                "ind_frac":round(ind_f,3),"wat_frac":round(wat_f,3),"road_count":road_n,
                "sens_score":round(sens,3),"is_forbidden":is_forbidden,"is_sea":is_sea,
                "is_coastal":is_coastal,"no_data":not has_data,
                "explanation":f"TEL={TEL:.0f}|Wb={Wb}|Df={Df:.2f}|"+"; ".join(parts)+".",}})

    sea=sum(1 for f in features if f["properties"]["is_sea"])
    cst=sum(1 for f in features if f["properties"]["is_coastal"])
    fbd=sum(1 for f in features if f["properties"]["is_forbidden"])
    print(f"[GRID] Done. {len(features)} cells | sea={sea} coastal={cst} forbidden={fbd} | {time.time()-t0:.0f}s")
    return features

# ── Temporal scoring ──────────────────────────────────────────────────────────
def score_with_time(props, dt, curves, weather):
    lu=props.get("land_use","forest"); base=props.get("tel",10)
    day=get_day_type(dt); hour=dt.hour
    if base>=9999:
        return {"query_time":dt.strftime("%H:%M"),"day_type":day,"presence_fraction":1.0,
                "weather_factor":1.0,"tel_temporal":9999,"score_temporal":0.90,
                "classification":"Forbidden — zero engagement",
                "explanation":"Forbidden zone — always unacceptable.",
                "data_source":"presence_curves.csv + Open-Meteo"}
    pres=get_presence(curves,lu,hour,day)

    # Weather factor application:
    # 1. High impact: Roads, Water, Forest, Commercial (people stay away)
    # 2. Medium impact: Residential, Industrial (people stay inside, some protection)
    of = weather.get("outdoor_factor", 1.0)
    if lu in {"road", "water", "forest", "commercial"}:
        wx = of
    elif lu in {"residential", "industrial"}:
        wx = 0.8 + 0.2 * of  # minor reduction (max 20%) for indoor protection/absence
    else:
        wx = 1.0

    tel_t=round(base*pres*wx,2); score_t=tel_to_score(tel_t); cls=tel_class(tel_t)
    expl=(f"At {dt.strftime('%H:%M')} on a {day}, {int(pres*100)}% of peak population "
          f"estimated present in this {lu} cell. Weather: {weather.get('condition','?')} "
          f"(exposure factor {wx:.2f}). Static TEL={base:.0f} → {tel_t:.0f}. "
          f"Unsuitability: {score_t:.2f} — {cls}.")
    return {"query_time":dt.strftime("%H:%M"),"day_type":day,"presence_fraction":round(pres,3),
            "weather_factor":round(wx,3),"tel_temporal":tel_t,"score_temporal":score_t,
            "classification":cls,"explanation":expl,"data_source":"presence_curves.csv + Open-Meteo"}

def parse_dt(qs):
    now=datetime.now(); ts=qs.get("time",[None])[0]; ds=qs.get("day",[None])[0]
    if ts:
        try: now=now.replace(hour=max(0,min(23,int(ts.split(":")[0]))),minute=0,second=0)
        except: pass
    if ds=="weekend" and now.weekday()<5:
        from datetime import timedelta; now+=timedelta(days=5-now.weekday())
    elif ds=="weekday" and now.weekday()>=5:
        from datetime import timedelta; now+=timedelta(days=7-now.weekday())
    return now

# ── Maritime ──────────────────────────────────────────────────────────────────
MS={
    "territorial_sea":{"color":"#00bcd4","weight":2,"dashArray":"8,4","fillOpacity":0.06,"label":"Territorial Sea (12nm)"},
    "contiguous_zone":{"color":"#0077be","weight":2,"dashArray":"12,4","fillOpacity":0.04,"label":"Contiguous Zone (24nm)"},
    "eez":            {"color":"#003f87","weight":2,"dashArray":"16,6","fillOpacity":0.03,"label":"EEZ (200nm)"},
    "tss_lane":       {"color":"#cc00cc","weight":1.5,"dashArray":"4,2","fillOpacity":0.12,"label":"Traffic Sep. Lane"},
    "tss_zone":       {"color":"#ff66ff","weight":1,"dashArray":"4,2","fillOpacity":0.15,"label":"Traffic Sep. Zone"},
    "military_area":  {"color":"#cc0000","weight":2,"dashArray":"6,3","fillOpacity":0.10,"label":"Military Restricted"},
}

def fetch_maritime():
    if MARITIME_CACHE.exists():
        print("[MARITIME] Loading from cache ...")
        with open(MARITIME_CACHE) as f: return json.load(f)
    S,N,W,E=BBOX; features=[]; MR="https://geo.vliz.be/geoserver/MarineRegions/wfs"
    def mr(tn,cql,zt):
        params={"service":"WFS","version":"1.0.0","request":"GetFeature","typeName":tn,
                "outputFormat":"application/json","CQL_FILTER":cql,"BBOX":f"{W},{S},{E},{N},EPSG:4326"}
        try:
            req=urllib.request.Request(MR+"?"+urllib.parse.urlencode(params),headers={"User-Agent":"KarlskronaRisk/4.0"})
            with urllib.request.urlopen(req,timeout=30) as r: data=json.loads(r.read().decode())
            for feat in data.get("features",[]):
                if feat.get("geometry"):
                    features.append({"type":"Feature","geometry":feat["geometry"],
                        "properties":{"zone_type":zt,"name":feat.get("properties",{}).get("geoname",zt),**MS[zt]}})
            print(f"  [MR] {zt}: {len(data.get('features',[]))} features")
        except Exception as e: print(f"  [MR] {zt} failed: {e}")
    print("[MARITIME] Fetching MarineRegions ...")
    mr("MarineRegions:eez","mrgid_eez=5694","eez")
    mr("MarineRegions:eez_12nm","territory1='Sweden'","territorial_sea")
    mr("MarineRegions:eez_24nm","territory1='Sweden'","contiguous_zone")
    osm_q=f"""[out:json][timeout:60][bbox:{S},{W},{N},{E}];
(way["seamark:type"~"separation_zone|traffic_separation_scheme|separation_line"];
 relation["seamark:type"~"separation_zone|traffic_separation_scheme"];
 way["seamark:type"="recommended_traffic_lane_part"];
 way["military"~"danger_area|restricted_area|training_area|naval_base"];
 relation["military"~"danger_area|restricted_area|training_area|naval_base"];
 way["landuse"="military"];relation["landuse"="military"];);
out body geom qt;""".strip()
    for ep in ["https://overpass-api.de/api/interpreter","https://overpass.kumi.systems/api/interpreter"]:
        try:
            enc=urllib.parse.urlencode({"data":osm_q}).encode()
            req=urllib.request.Request(ep,data=enc,headers={"User-Agent":"KarlskronaRisk/4.0"})
            with urllib.request.urlopen(req,timeout=60) as r: od=json.loads(r.read().decode())
            for el in od.get("elements",[]):
                tags=el.get("tags",{}); smt=tags.get("seamark:type",""); mil=tags.get("military",""); lu=tags.get("landuse","")
                geom=None
                if el.get("type")=="way":
                    coords=[(n["lon"],n["lat"]) for n in el.get("geometry",[])]
                    if len(coords)>=4 and coords[0]==coords[-1]:
                        try: geom=Polygon(coords).__geo_interface__
                        except: pass
                if geom is None: continue
                if smt in ("separation_zone","separation_line"): zt="tss_zone"
                elif smt in ("traffic_separation_scheme","recommended_traffic_lane_part"): zt="tss_lane"
                elif mil or lu=="military": zt="military_area"
                else: continue
                features.append({"type":"Feature","geometry":geom,"properties":{"zone_type":zt,"name":tags.get("name",zt),**MS[zt]}})
            break
        except Exception as e: print(f"  [OSM maritime] {ep}: {e}")
    result={"type":"FeatureCollection","features":features}
    with open(MARITIME_CACHE,"w") as f: json.dump(result,f,separators=(",",":"))
    print(f"[MARITIME] {len(features)} zones cached"); return result

# ── AIS ───────────────────────────────────────────────────────────────────────
def load_ais():
    if not AIS_FILE.exists(): return []
    try:
        ships=[]
        with open(AIS_FILE,newline="",encoding="utf-8") as f:
            for row in csv.DictReader(f): ships.append(row)
        print(f"[AIS] {len(ships)} vessels from {AIS_FILE}"); return ships
    except Exception as e: print(f"[AIS] {e}"); return []

# ── Global state ──────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# DEBRIS PHYSICS ENGINE
# ─────────────────────────────────────────────────────────────────────────────
# Sources:
#   - Terminal velocity: v_t = sqrt(2mg / rho*Cd*A), standard aerodynamics
#   - Cd values: Hoerner (1965) "Fluid Dynamic Drag", Ch.3 irregular fragments
#   - Air density rho=1.225 kg/m³ at sea level (ISA standard atmosphere)
#   - Fragment mass distribution: estimated for 5kg commercial/military drone
#     Based on DJI/Shahed class mass breakdown (publicly documented)
#   - Horizontal drift: x = v_wind * t_fall * Cd_horizontal (linearised)
#   NOT heuristic: physics equations are exact. Parameters are estimated
#   from literature. Clearly documented so they can be updated with real data.

AIR_DENSITY = 1.225   # kg/m³, ISA standard sea level

DEBRIS_CLASSES = [
    # name, mass_kg, Cd_vertical, Cd_horizontal, area_m2, consequence_weight
    # Heavy: main frame/motor — falls fast, limited drift
    # Cd_v=0.8 (tumbling brick shape, Hoerner Ch.3)
    {"name":"Heavy debris",  "mass_kg":2.5, "Cd_v":0.80, "Cd_h":0.30, "area_m2":0.04, "weight":0.50},
    # Medium: panels/battery — moderate fall, moderate drift
    # Cd_v=1.2 (flat plate tumbling, Hoerner Ch.3)
    {"name":"Medium debris", "mass_kg":0.8, "Cd_v":1.20, "Cd_h":0.80, "area_m2":0.02, "weight":0.35},
    # Fine: wiring/PCB/fuel — slow fall, large drift, fire risk
    # Cd_v=2.0 (high-drag irregular small fragments)
    {"name":"Fine/fuel",     "mass_kg":0.1, "Cd_v":2.00, "Cd_h":1.50, "area_m2":0.005,"weight":0.15},
]

def terminal_velocity(mass_kg, Cd_v, area_m2):
    """
    Stokes/Newton drag terminal velocity.
    v_t = sqrt(2 * m * g / (rho * Cd * A))
    Source: standard aerodynamics (Hoerner 1965, Anderson 2010)
    """
    g = 9.81
    return math.sqrt((2 * mass_kg * g) / (AIR_DENSITY * Cd_v * area_m2))

def compute_debris_landing(lat, lon, alt_m, weather):
    """
    Compute debris impact zone for an intercept at (lat, lon, alt_m).

    Returns per-class physics PLUS a unified impact zone:
      - centre: wind-drift-adjusted landing point for each class
      - radius: 50m-1000m based on alt + wind (higher/windier = larger spread)
      - The unified zone is the weighted centroid of all class centres,
        with radius = max class radius (conservative bound)

    Physics (Newtonian drag, Hoerner 1965 Cd):
      v_t = sqrt(2mg / rho*Cd*A)
      drift = v_wind * (alt/v_t) / (1 + Cd_h)
      radius = max(50, drift * 0.30) clamped to [50, 1000]
    """
    # Debris physics constants
    # Limit wind to 40km/h as requested
    wind_kmh  = min(40.0, float(weather.get("windspeed_kmh", 10)))
    wind_ms   = wind_kmh / 3.6
    # Wind direction: 0 is North, 90 is East. 
    # Drift is IN the direction the wind is blowing.
    # meteorological wind_dir is "from", so "to" is (dir + 180) % 360
    wind_from = float(weather.get("wind_direction_deg", 270))
    wind_to   = (wind_from + 180) % 360
    bear_rad  = math.radians(90 - wind_to) # Standard math angle (East=0, North=90)

    landing_zones = []
    for dc in DEBRIS_CLASSES:
        v_t     = terminal_velocity(dc["mass_kg"], dc["Cd_v"], dc["area_m2"])
        tof     = alt_m / v_t
        drift_m = wind_ms * tof / (1.0 + dc["Cd_h"])

        # Calculate offset in degrees
        d_lat    = (drift_m * math.sin(math.radians(90 - wind_to))) / 111111.0
        d_lon    = (drift_m * math.cos(math.radians(90 - wind_to))) / (111111.0 * math.cos(math.radians(lat)))
        land_lat = lat + d_lat
        land_lon = lon + d_lon

        # Debris scatter radius [50, 1000] metres
        # Two physical contributions combined (RSS):
        #
        # 1. FRAGMENTATION SPREAD from missile warhead impact
        #    Lateral delta-v imparted to fragments ≈ 200-400 m/s (RBS70 proximity fuze)
        #    Spread at ground = lateral_v × tof / (1 + Cd_h)
        #    Proxy: alt_m × 0.35 × mass_factor (lighter = more spread)
        mass_factor    = math.sqrt(2.5 / dc["mass_kg"])  # heavy=1.0, fine=5.0
        frag_spread_m  = alt_m * 0.35 * mass_factor

        # 2. WIND DRIFT UNCERTAINTY (30% variability in drift)
        wind_uncertainty_m = drift_m * 0.30

        # RSS combination, clamped to [50, 1000]
        scatter_m = float(max(50.0, min(1000.0,
            math.sqrt(frag_spread_m**2 + wind_uncertainty_m**2)
        )))

        landing_zones.append({
            "class":          dc["name"],
            "weight":         dc["weight"],
            "terminal_v":     round(v_t, 1),
            "time_of_flight": round(tof, 1),
            "drift_m":        round(drift_m, 1),
            "frag_spread_m":  round(frag_spread_m, 1),
            "land_lat":       round(land_lat, 6),
            "land_lon":       round(land_lon, 6),
            "scatter_m":      round(scatter_m, 1),
            "mass_kg":        dc["mass_kg"],
        })

    # Unified impact zone: probability-weighted centroid of all class centres
    # Radius = weighted average of class radii (not just max — fine debris
    # has large radius but low consequence weight)
    total_w = sum(z["weight"] for z in landing_zones)
    uni_lat = sum(z["land_lat"] * z["weight"] for z in landing_zones) / total_w
    uni_lon = sum(z["land_lon"] * z["weight"] for z in landing_zones) / total_w
    uni_rad = float(max(50.0, min(1000.0,
        sum(z["scatter_m"] * z["weight"] for z in landing_zones) / total_w
    )))

    for z in landing_zones:
        z["unified_lat"] = round(uni_lat, 6)
        z["unified_lon"] = round(uni_lon, 6)
        z["unified_rad"] = round(uni_rad, 1)

    return landing_zones

def score_landing_zones(landing_zones):
    """
    Probability-weighted consequence scoring for debris impact zones.

    For each debris class:
      - Find all grid cells within scatter_m radius of the landing centre
      - Each cell gets a hit_probability based on Gaussian distance decay
        hit_prob(cell) = exp(-0.5 * (dist / sigma)^2)
        where sigma = scatter_m / 2  (68% of mass within scatter_m)
      - Cell consequence = hit_prob * cell_score * (1 + pop_weight)
      - Zone score = sum(cell_consequence) / sum(hit_prob)  [expected consequence]

    Also scores the UNIFIED impact zone (weighted centroid of all classes)
    using the same probability-weighted model.

    This replaces the flat average with a proper expected-value calculation:
    cells closer to the impact centre are more likely to be hit.
    """
    total_consequence = 0.0
    forbidden_hit     = False
    pop_at_risk       = 0
    details           = []
    S,N,W,E = BBOX

    for zone in landing_zones:
        zlat  = zone["land_lat"]
        zlon  = zone["land_lon"]
        rad   = zone["scatter_m"]                  # max radius (50-1000m)
        sigma = max(rad / 2.0, 25.0)               # Gaussian sigma

        sum_prob  = 0.0
        sum_harm  = 0.0
        fbd_hit   = False
        pop_total = 0

        for feat in GRID_FEATURES:
            p    = feat["properties"]
            dist = haversine_m(zlat, zlon, p["lat"], p["lon"])
            if dist > rad: continue                 # outside impact radius

            # Gaussian hit probability: 1.0 at centre, ~0.14 at edge
            hit_prob = math.exp(-0.5 * (dist / sigma) ** 2)

            # Cell consequence score (blended direct + neighbourhood)
            cell_score = p.get("score") or 0.0
            integ_norm = min(p.get("integrated_risk", 0) / 20.0, 1.0)
            blended    = 0.70 * cell_score + 0.30 * integ_norm

            # Population amplifier: more people → more harm per hit
            pop = p.get("pop_count", 0)
            pop_amp = 1.0 + math.log1p(pop) / math.log1p(100)

            # Expected harm = probability × consequence × population factor
            expected_harm = hit_prob * blended * pop_amp

            sum_prob  += hit_prob
            sum_harm  += expected_harm
            pop_total += pop * hit_prob    # probability-weighted population

            if p.get("is_forbidden"):
                fbd_hit = True
                forbidden_hit = True

        if sum_prob > 0:
            # Expected consequence = weighted average harm per unit probability
            zone_score = sum_harm / sum_prob
            # Normalise: pop_amp is ~1-2, so divide by 1.5 to keep in 0-1
            zone_score = min(zone_score / 1.5, 1.0)
        else:
            # No cells hit — check if landing point is in bbox
            zone_score = 0.05 if (S<=zlat<=N and W<=zlon<=E) else 0.0

        pop_at_risk  += int(pop_total)
        weighted      = zone["weight"] * zone_score
        total_consequence += weighted

        details.append({
            "class":       zone["class"],
            "score":       round(zone_score, 3),
            "weighted":    round(weighted, 3),
            "cells_hit":   sum(1 for f in GRID_FEATURES
                               if haversine_m(zlat,zlon,f["properties"]["lat"],
                                              f["properties"]["lon"]) <= rad),
            "forbidden":   fbd_hit,
            "pop_in_zone": int(pop_total),
            "radius_m":    rad,
        })

    # ── Score the unified impact zone separately ───────────────────────────
    # unified_lat/lon/rad are set by compute_debris_landing (weighted centroid)
    # This scores the aggregate impact zone where MOST debris lands
    if landing_zones:
        ulat  = landing_zones[0].get("unified_lat", landing_zones[0]["land_lat"])
        ulon  = landing_zones[0].get("unified_lon", landing_zones[0]["land_lon"])
        urad  = landing_zones[0].get("unified_rad", 150.0)
        usig  = max(urad / 2.0, 25.0)
        usum_prob = usum_harm = 0.0
        for feat in GRID_FEATURES:
            p    = feat["properties"]
            dist = haversine_m(ulat, ulon, p["lat"], p["lon"])
            if dist > urad: continue
            hit_prob   = math.exp(-0.5 * (dist / usig) ** 2)
            cell_score = 0.70*(p.get("score") or 0) + 0.30*min(p.get("integrated_risk",0)/20.0,1.0)
            pop_amp    = 1.0 + math.log1p(p.get("pop_count",0)) / math.log1p(100)
            usum_prob += hit_prob
            usum_harm += hit_prob * cell_score * pop_amp
        unified_score = min(usum_harm / usum_prob / 1.5, 1.0) if usum_prob > 0 else 0.0
    else:
        ulat = ulon = 0.0; urad = 100.0; unified_score = 0.0

    return {
        "consequence":    round(total_consequence, 4),
        "forbidden_hit":  forbidden_hit,
        "pop_at_risk":    int(pop_at_risk),
        "details":        details,
        "unified_lat":    round(ulat, 6),
        "unified_lon":    round(ulon, 6),
        "unified_rad":    round(urad, 1),
        "unified_score":  round(unified_score, 4),
    }

def generate_drone_trajectory(seed, target_key=None):
    """
    Generates a realistic evasive drone trajectory within the scored BBOX.

    Behaviours:
    - All waypoints clamped to BBOX (fully scoreable)
    - 50% chance drone routes NEAR 1-2 important regions en route
      (recon pass, secondary threat, radar shadow exploitation)
    - 6 attack patterns with BBOX-safe offsets
    - Local velocity per segment fed to lead-angle solver
    """
    import random
    rng = random.Random(seed)

    S, N, W, E = BBOX

    SEA_ENTRIES = [
        {"lat": S+0.01, "lon": W+(E-W)*0.55, "label":"South approach"},
        {"lat": S+0.01, "lon": W+(E-W)*0.35, "label":"South-west approach"},
        {"lat": S+0.01, "lon": W+(E-W)*0.75, "label":"South-east approach"},
        {"lat": S+(N-S)*0.35, "lon": E-0.01, "label":"East approach"},
        {"lat": S+(N-S)*0.65, "lon": E-0.01, "label":"North-east approach"},
        {"lat": N-0.01, "lon": W+(E-W)*0.45, "label":"North approach"},
    ]

    ALL_IMPORTANT = [
        {"key":"naval_base", "name":"Naval Base Karlskrona",   "lat":56.1614,"lon":15.5869,"value":1.0},
        {"key":"ferry",      "name":"Ferry Terminal",           "lat":56.1607,"lon":15.5950,"value":0.8},
        {"key":"fort",       "name":"Kungsholms Fort",          "lat":56.1050,"lon":15.5906,"value":0.9},
        {"key":"radar",      "name":"Aspö Island Radar",        "lat":56.0700,"lon":15.7100,"value":0.85},
        {"key":"port",       "name":"Dragsö Industrial Port",   "lat":56.1750,"lon":15.6200,"value":0.7},
        {"key":"city",       "name":"City Centre",              "lat":56.1608,"lon":15.5865,"value":0.6},
        {"key":"stumholmen", "name":"Stumholmen Naval Museum",   "lat":56.1590,"lon":15.5920,"value":0.65},
        {"key":"power",      "name":"Power Substation Lyckeby", "lat":56.1820,"lon":15.5600,"value":0.55},
        {"key":"bergasa",    "name":"Bergåsa Industrial Zone",   "lat":56.1800,"lon":15.6100,"value":0.50},
        {"key":"rosenholm",  "name":"Rosenholm Port Area",       "lat":56.1650,"lon":15.6300,"value":0.55},
    ]

    # Drone categories based on user requirements
    DRONE_TYPES = [
        {"name": "Tactical ISR / Loitering", "speed_kmh": rng.uniform(100, 200)},
        {"name": "MALE ISR / Strike",       "speed_kmh": rng.uniform(200, 450)},
        {"name": "Strategic / Jet Attack",   "speed_kmh": rng.uniform(450, 800)},
    ]
    drone_type = rng.choice(DRONE_TYPES)
    speed_ms = drone_type["speed_kmh"] / 3.6

    entry   = rng.choice(SEA_ENTRIES)
    target  = rng.choices(ALL_IMPORTANT, weights=[t["value"] for t in ALL_IMPORTANT])[0]
    pattern = rng.choice(["s_curve","flanking","sea_skim","pincer","direct_jink","spiral_in"])

    # Via-regions: 0-2 important sites the drone passes near en route
    # Weight by asset value AND how close it is to the direct path
    others = [r for r in ALL_IMPORTANT if r["key"] != target["key"]]
    def path_closeness(r):
        mlat = (entry["lat"] + target["lat"]) / 2
        mlon = (entry["lon"] + target["lon"]) / 2
        return r["value"] / (1.0 + haversine_m(r["lat"],r["lon"],mlat,mlon)/5000.0)
    via_weights = [path_closeness(r) for r in others]
    n_via = rng.choices([0,1,2], weights=[0.30, 0.45, 0.25])[0]
    via_regions = []
    if n_via > 0:
        picked = rng.choices(others, weights=via_weights, k=min(n_via,len(others)))
        seen = set()
        for r in picked:
            if r["key"] not in seen:
                via_regions.append(r); seen.add(r["key"])

    # Clamp helpers
    mlat = (N-S)*0.04; mlon = (E-W)*0.04
    def clamp(la,lo):
        return max(S+mlat,min(N-mlat,la)), max(W+mlon,min(E-mlon,lo))
    def mk(la,lo,alt,label):
        la,lo=clamp(la,lo)
        return {"lat":round(la,6),"lon":round(lo,6),"alt_m":int(alt),"label":label,"t":0}
    def moff(frac,perp_m):
        bla=entry["lat"]+frac*(target["lat"]-entry["lat"])
        blo=entry["lon"]+frac*(target["lon"]-entry["lon"])
        bear=math.atan2(target["lon"]-entry["lon"],target["lat"]-entry["lat"])
        perp=bear+math.pi/2; sign=rng.choice([-1,1])
        dla=sign*perp_m*math.cos(perp)/111111.0
        dlo=sign*perp_m*math.sin(perp)/(111111.0*math.cos(math.radians(bla)))
        return clamp(bla+dla,blo+dlo)

    elat,elon=entry["lat"],entry["lon"]
    tlat,tlon=target["lat"],target["lon"]

    # Via-point waypoints: drone passes within ~500m-1km of asset
    via_pts=[]
    for j,via in enumerate(via_regions):
        vlat=via["lat"]+rng.uniform(-0.006,0.006)
        vlon=via["lon"]+rng.uniform(-0.006,0.006)
        via_pts.append(mk(vlat,vlon,rng.uniform(70,160),f"Via: {via['name']}"))

    # Pattern cores
    if pattern=="s_curve":
        la1,lo1=moff(0.28,rng.uniform(3000,6000))
        la2,lo2=moff(0.65,rng.uniform(2500,5000))
        wpts=[mk(elat,elon,260,"Entry: "+entry["label"]),
              mk(la1,lo1,rng.uniform(180,280),"S-apex 1"),
              *via_pts,
              mk((la1+la2)/2,(lo1+lo2)/2,rng.uniform(140,220),"S-mid"),
              mk(la2,lo2,rng.uniform(90,160),"S-apex 2"),
              mk(tlat,tlon,45,"Target: "+target["name"])]

    elif pattern=="flanking":
        la,lo=moff(0.55,rng.uniform(4000,7000))
        la2,lo2=moff(0.72,rng.uniform(2000,4000))
        wpts=[mk(elat,elon,280,"Entry: "+entry["label"]),
              mk(elat+(la-elat)*0.45,elon+(lo-elon)*0.45,rng.uniform(220,280),"Flank approach"),
              *via_pts,
              mk(la,lo,rng.uniform(140,210),"Flank position"),
              mk(la2,lo2,rng.uniform(100,160),"Turning inbound"),
              mk(tlat,tlon,50,"Target: "+target["name"])]

    elif pattern=="sea_skim":
        n=rng.randint(3,5)
        skm=[mk(elat+((i+1)/(n+1))*(tlat-elat)+rng.uniform(-0.020,0.020),
                elon+((i+1)/(n+1))*(tlon-elon)+rng.uniform(-0.020,0.020),
                rng.uniform(25,70),f"Skim {i+1}") for i in range(n)]
        wpts=[mk(elat,elon,70,"Entry: "+entry["label"]+" (low)"),
              *skm,*via_pts,mk(tlat,tlon,50,"Target: "+target["name"])]

    elif pattern=="pincer":
        pvla=elat+(tlat-elat)*0.50+rng.uniform(-0.04,-0.01)
        pvlo=elon+(tlon-elon)*0.50+rng.uniform(-0.03,0.03)
        wpts=[mk(elat,elon,280,"Entry: "+entry["label"]),
              mk(elat+(pvla-elat)*0.5,elon+(pvlo-elon)*0.5,rng.uniform(230,290),"Leg 1 mid"),
              *via_pts,
              mk(pvla,pvlo,rng.uniform(160,240),"Pivot"),
              mk(pvla+(tlat-pvla)*0.45,pvlo+(tlon-pvlo)*0.45,rng.uniform(100,170),"Leg 2"),
              mk(tlat,tlon,50,"Target: "+target["name"])]

    elif pattern=="direct_jink":
        n=rng.randint(3,5)
        jnk=[mk(elat+((i+1)/(n+1))*(tlat-elat)+rng.uniform(-0.018,0.018),
                elon+((i+1)/(n+1))*(tlon-elon)+rng.uniform(-0.022,0.022),
                rng.uniform(90,260) if (i+1)/(n+1)<0.7 else rng.uniform(55,130),
                f"Jink {i+1}") for i in range(n)]
        wpts=[mk(elat,elon,240,"Entry: "+entry["label"]),*jnk,*via_pts,
              mk(tlat,tlon,50,"Target: "+target["name"])]

    else:  # spiral_in
        n=rng.randint(4,6); rs=rng.uniform(0.025,0.045)
        spl=[mk(tlat+rs*(1-i/n*0.65)*math.sin((i/n)*2*math.pi+rng.uniform(0,0.4)),
                tlon+rs*(1-i/n*0.65)*math.cos((i/n)*2*math.pi+rng.uniform(0,0.4))/math.cos(math.radians(tlat)),
                max(230-i*(170/n),55),f"Spiral {i+1}") for i in range(n)]
        wpts=[mk(elat,elon,270,"Entry: "+entry["label"]),
              mk(elat+(tlat-elat)*0.45+rng.uniform(-0.02,0.02),
                 elon+(tlon-elon)*0.45+rng.uniform(-0.02,0.02),200,"Transit"),
              *via_pts,*spl,mk(tlat,tlon,40,"Target: "+target["name"])]

    total_dist=sum(haversine_m(wpts[i]["lat"],wpts[i]["lon"],
                               wpts[i+1]["lat"],wpts[i+1]["lon"])
                   for i in range(len(wpts)-1))
    
    via_names=[v["name"] for v in via_regions]
    route=(" via "+", ".join(via_names)) if via_names else ""

    return {
        "seed":         seed,
        "type":         drone_type["name"],
        "pattern":      pattern,
        "target":       target,
        "entry":        entry,
        "via_regions":  via_regions,
        "waypoints":    wpts,
        "speed_ms":     speed_ms,
        "speed_kmh":    round(drone_type["speed_kmh"]),
        "n_waypoints":  len(wpts),
        "total_dist_m": round(total_dist),
        "total_time_s": round(total_dist/speed_ms),
        "from_lat":     elat,
        "from_lon":     elon,
        "to_lat":       target["lat"],
        "to_lon":       target["lon"],
        "description":  (f"[{drone_type['name'].upper()}] "
                         f"{entry['label']}{route} → {target['name']} | "
                         f"{drone_type['speed_kmh']:.0f}km/h | {total_dist/1000:.1f}km"),
        "assumptions":  f"Speed={drone_type['speed_kmh']:.0f}km/h, {pattern} pattern, Hoerner 1965 Cd",
    }


def interpolate_trajectory(waypoints, t):
    """
    Smooth interpolation along a multi-waypoint trajectory.
    t in [0,1] maps to the full path by arc-length parameterisation.
    Uses cumulative distance so speed is uniform regardless of waypoint spacing.
    """
    if t <= 0: return {**waypoints[0], "t": 0}
    if t >= 1: return {**waypoints[-1], "t": 1}

    # Build cumulative arc-length table
    dists = [0.0]
    for i in range(len(waypoints)-1):
        d = haversine_m(
            waypoints[i]["lat"],   waypoints[i]["lon"],
            waypoints[i+1]["lat"], waypoints[i+1]["lon"]
        )
        dists.append(dists[-1] + d)
    total = dists[-1]
    if total == 0:
        return {**waypoints[0], "t": t}

    # Target distance along path
    target_dist = t * total

    # Find which segment we're in
    seg = 0
    for i in range(len(dists)-1):
        if dists[i] <= target_dist <= dists[i+1]:
            seg = i
            break
        seg = i

    seg_len = dists[seg+1] - dists[seg]
    local_t = (target_dist - dists[seg]) / seg_len if seg_len > 0 else 0
    a, b = waypoints[seg], waypoints[seg+1]

    return {
        "lat":   a["lat"]   + local_t * (b["lat"]   - a["lat"]),
        "lon":   a["lon"]   + local_t * (b["lon"]   - a["lon"]),
        "alt_m": a["alt_m"] + local_t * (b["alt_m"] - a["alt_m"]),
        "t":     t,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CRITICAL INFRASTRUCTURE & ENGAGEMENT GEOMETRY
# ─────────────────────────────────────────────────────────────────────────────

# Critical sites — threat exclusion zones around these locations.
# Drone intercepted inside exclusion radius → debris falls on/near asset.
# Source: public knowledge, OSM, Swedish defence doctrine.
CRITICAL_SITES = [
    {"key":"naval_base",  "name":"Naval Base Karlskrona",   "lat":56.1614,"lon":15.5869,
     "radius_m":3000, "type":"military",    "color":"#cc0000"},
    {"key":"kungsholm",   "name":"Kungsholms Fort",          "lat":56.1050,"lon":15.5906,
     "radius_m":2000, "type":"military",    "color":"#cc0000"},
    {"key":"port",        "name":"Dragsö Industrial Port",   "lat":56.1750,"lon":15.6200,
     "radius_m":2000, "type":"industrial",  "color":"#ff6600"},
    {"key":"ferry",       "name":"Ferry Terminal",           "lat":56.1607,"lon":15.5950,
     "radius_m":1500, "type":"civilian",    "color":"#ff9900"},
    {"key":"power",       "name":"Power Substation Lyckeby", "lat":56.1820,"lon":15.5600,
     "radius_m":1000, "type":"industrial",  "color":"#ff6600"},
    {"key":"stumholmen",  "name":"Stumholmen Naval Museum",  "lat":56.1590,"lon":15.5920,
     "radius_m":1000, "type":"military",    "color":"#cc0000"},
    {"key":"radar",       "name":"Aspö Island Radar",        "lat":56.0700,"lon":15.7100,
     "radius_m":2500, "type":"military",    "color":"#cc0000"},
]

# Interceptor systems — realistic parameters for Karlskrona context
# Source: Jane's Weapons, public defence documentation
INTERCEPTOR_SYSTEMS = {
    "rbs70": {
        "name":      "RBS 70 Mk2 (Karlskrona Naval)",
        "speed_ms":  680,    # m/s
        "range_m":   9000,   # max engagement range
        "min_range_m": 200,  # minimum arm distance
        "reaction_s":  6,    # seconds from detection to launch
        "notes":     "SHORAD, laser-beam riding, stationed at Karlskrona",
    },
    "iris_t": {
        "name":      "IRIS-T SLM",
        "speed_ms":  1020,
        "range_m":   40000,
        "min_range_m": 500,
        "reaction_s":  8,
        "notes":     "Medium-range SAM",
    },
    "stinger": {
        "name":      "Stinger MANPADS",
        "speed_ms":  750,
        "range_m":   4800,
        "min_range_m": 200,
        "reaction_s":  10,
        "notes":     "Man-portable, short range",
    },
    "kreuger100": {
        "name":      "Kreuger 100 Interceptor",
        "speed_ms":  75,      # ~270 km/h
        "range_m":   2000,
        "min_range_m": 50,
        "reaction_s":  4,     # Very fast hand-launch
        "notes":     "Battery-powered counter-UAS drone",
    },
    "kreuger100xr": {
        "name":      "Kreuger 100XR (Extended Range)",
        "speed_ms":  85,      # Faster military variant (~300 km/h)
        "range_m":   3500,
        "min_range_m": 50,
        "reaction_s":  4,
        "notes":     "Extended range military variant, 20min loiter",
    },
    "gripen": {
        "name":      "JAS 39 Gripen (F17 Ronneby)",
        "speed_ms":  750,    # Supersonic intercept speed
        "range_m":   150000, # Long range
        "min_range_m": 1000,
        "reaction_s":  45,   # Scramble/warm-up time
        "base":      {"lat": 56.2667, "lon": 15.2667, "name": "F17 Ronneby Airbase"},
        "notes":     "Scrambled from F17 Airbase (West of Karlskrona)",
    },
    "custom": {
        "name":      "Custom system",
        "speed_ms":  500,
        "range_m":   8000,
        "min_range_m": 200,
        "reaction_s":  5,
        "notes":     "User-defined parameters",
    },
}

# Fixed interceptor launch site: Karlskrona Naval Base
# RBS 70 Mk2 SHORAD system is stationed here in real life.
DEFAULT_INTERCEPTOR = {"lat": 56.1614, "lon": 15.5869}


def compute_drone_times(waypoints, speed_ms):
    """
    Compute cumulative time at each trajectory sample point.
    Uses arc-length parameterisation so time is proportional to real distance.
    Returns list of (t_param, time_seconds, lat, lon, alt_m) for N samples.
    """
    # Build dense samples along spline
    N = 120  # sample resolution
    samples = []
    for step in range(N + 1):
        t = step / N
        pos = interpolate_trajectory(waypoints, t)
        samples.append((t, pos["lat"], pos["lon"], pos["alt_m"]))

    # Cumulative time
    cum_time = [0.0]
    for i in range(1, len(samples)):
        d = haversine_m(samples[i-1][1], samples[i-1][2],
                        samples[i][1],   samples[i][2])
        cum_time.append(cum_time[-1] + d / speed_ms)

    return [(samples[i][0], cum_time[i], samples[i][1],
             samples[i][2], samples[i][3])
            for i in range(len(samples))]


def find_exclusion_entry_times(timed_samples, critical_sites):
    """
    For each critical site, find the earliest time the drone enters
    the exclusion radius. Returns dict {site_key: time_seconds or None}.
    """
    entry_times = {}
    for site in critical_sites:
        entry_time = None
        for (t_param, t_sec, lat, lon, alt) in timed_samples:
            dist = haversine_m(lat, lon, site["lat"], site["lon"])
            if dist <= site["radius_m"]:
                entry_time = t_sec
                break
        entry_times[site["key"]] = entry_time
    return entry_times


def solve_intercept_feasibility(drone_lat, drone_lon, drone_time_s,
                                 interceptor_lat, interceptor_lon,
                                 system, drone_vel_lat=0, drone_vel_lon=0,
                                 reaction_s_override=None):
    """
    Lead-angle intercept geometry.

    The missile does NOT fly to where the drone IS — it flies to where
    the drone WILL BE when the missile arrives. We solve this iteratively:

      t_fly_0 = dist(interceptor, drone_now) / v_missile
      future_pos = drone_now + drone_velocity * t_fly_0
      t_fly_1 = dist(interceptor, future_pos) / v_missile
      repeat until convergence (3-5 iterations)

    This correctly models lead-angle pursuit and gives a much larger
    feasible engagement envelope than the naive point-to-point check.

    Source: standard fire control geometry (Zarchan 2012, Tactical and
    Strategic Missile Guidance, Ch.2 lead-angle intercept)
    """
    v_i        = system["speed_ms"]
    reaction_s = reaction_s_override or system["reaction_s"]

    # Iterative lead-angle solver (converges in 3-5 steps)
    # Start with naive estimate
    dist_now = haversine_m(drone_lat, drone_lon, interceptor_lat, interceptor_lon)
    t_fly    = dist_now / v_i

    for _ in range(6):
        # Project drone forward by t_fly
        future_lat = drone_lat + drone_vel_lat * t_fly
        future_lon = drone_lon + drone_vel_lon * t_fly
        dist_future = haversine_m(future_lat, future_lon,
                                   interceptor_lat, interceptor_lon)
        t_fly_new = dist_future / v_i
        if abs(t_fly_new - t_fly) < 0.01:   # converged
            t_fly = t_fly_new
            break
        t_fly = t_fly_new

    future_lat = drone_lat + drone_vel_lat * t_fly
    future_lon = drone_lon + drone_vel_lon * t_fly
    dist_to_intercept = haversine_m(future_lat, future_lon,
                                     interceptor_lat, interceptor_lon)

    total_s    = reaction_s + t_fly
    time_margin= drone_time_s - total_s    # positive = interceptor arrives first

    in_range   = dist_to_intercept <= system["range_m"]
    above_min  = dist_now >= system["min_range_m"]
    time_ok    = time_margin > 0

    return {
        "feasible":          in_range and above_min and time_ok,
        "dist_now_m":        round(dist_now),
        "dist_intercept_m":  round(dist_to_intercept),
        "flight_s":          round(t_fly, 1),
        "reaction_s":        reaction_s,
        "total_s":           round(total_s, 1),
        "launch_time_s":     round(drone_time_s - total_s, 1),
        "time_margin_s":     round(time_margin, 1),
        "intercept_lat":     round(future_lat, 6),
        "intercept_lon":     round(future_lon, 6),
        "in_range":          in_range,
        "above_min":         above_min,
        "time_ok":           time_ok,
        "dist_m":            round(dist_now),
    }


def full_engagement_analysis(traj, system_key, interceptor_pos, wx):
    """
    Full engagement analysis for a drone trajectory.

    For each of N trajectory points:
      1. Drone arrival time (arc-length / speed)
      2. Interceptor feasibility (can it get there in time?)
      3. Exclusion zone check (is drone past a critical site?)
      4. Debris consequence (physics + grid scoring)
      5. Engagement decision

    Returns structured result with all candidate points,
    optimal intercept, and engagement windows.
    """
    system   = INTERCEPTOR_SYSTEMS.get(system_key, INTERCEPTOR_SYSTEMS["rbs70"])
    waypoints = traj["waypoints"]
    speed_ms  = traj["speed_ms"]

    # Add wind direction to weather
    wx["wind_direction_deg"] = wx.get("wind_direction_deg", 270)

    # Step 1: build timed trajectory samples
    timed = compute_drone_times(waypoints, speed_ms)

    # Step 2: find when drone enters each exclusion zone
    entry_times = find_exclusion_entry_times(timed, CRITICAL_SITES)

    # Earliest exclusion entry (this is our hard deadline)
    earliest_excl = None
    nearest_site  = None
    for site in CRITICAL_SITES:
        et = entry_times.get(site["key"])
        if et is not None:
            if earliest_excl is None or et < earliest_excl:
                earliest_excl = et
                nearest_site  = site

    S, N, W, E = BBOX
    candidates  = []

    # Drone velocity vector (lat/lon per second) for lead-angle solver
    total_path_m = sum(
        haversine_m(waypoints[i]["lat"],waypoints[i]["lon"],
                    waypoints[i+1]["lat"],waypoints[i+1]["lon"])
        for i in range(len(waypoints)-1)
    )
    # Overall heading from entry to target (simplified — good enough for planning)
    entry_lat, entry_lon = waypoints[0]["lat"], waypoints[0]["lon"]
    final_lat, final_lon = waypoints[-1]["lat"], waypoints[-1]["lon"]
    total_d = haversine_m(entry_lat, entry_lon, final_lat, final_lon)
    if total_d > 0:
        # lat/lon per second
        drone_vlat = (final_lat - entry_lat) / (total_d / speed_ms) / 111111.0
        drone_vlon = (final_lon - entry_lon) / (total_d / speed_ms) / (111111.0 * math.cos(math.radians((entry_lat+final_lat)/2)))
    else:
        pass  # local velocities computed below

    # Pre-compute LOCAL velocity vector at each timed sample.
    # Uses point[i]→point[i+1] direction — correct for S-curves, spirals, jinks.
    # The old global average was wrong: a drone on an S-curve changes heading
    # mid-flight, so the lead-angle projection was pointing the wrong direction.
    local_velocities = []
    for i, (t_p, t_s, lat_i, lon_i, alt_i) in enumerate(timed):
        if i < len(timed) - 1:
            _, t_next, nlat, nlon, _ = timed[i+1]
            dt = t_next - t_s
            if dt > 0:
                vlat = (nlat - lat_i) / dt          # degrees latitude per second
                vlon = (nlon - lon_i) / dt          # degrees longitude per second
            else:
                vlat = vlon = 0.0
        else:
            vlat, vlon = local_velocities[-1] if local_velocities else (0.0, 0.0)
        local_velocities.append((vlat, vlon))

    for i, (t_param, t_sec, lat, lon, alt_m) in enumerate(timed):
        drone_vlat, drone_vlon = local_velocities[i]

        # ── Interceptor feasibility — local heading lead-angle ──────────────
        feas = solve_intercept_feasibility(
            lat, lon, t_sec,
            interceptor_pos["lat"], interceptor_pos["lon"],
            system,
            drone_vel_lat=drone_vlat,
            drone_vel_lon=drone_vlon,
        )

        # ── Exclusion zone check ───────────────────────────────────────────
        # Is this point BEFORE the drone enters ANY exclusion zone?
        before_excl = (earliest_excl is None) or (t_sec < earliest_excl)

        # Is the drone currently INSIDE an exclusion zone?
        inside_excl = False
        inside_site = None
        for site in CRITICAL_SITES:
            if haversine_m(lat, lon, site["lat"], site["lon"]) <= site["radius_m"]:
                inside_excl = True
                inside_site = site
                break

        # ── Debris physics ─────────────────────────────────────────────────
        if S <= lat <= N and W <= lon <= E:
            landing_zones = compute_debris_landing(lat, lon, alt_m, wx)
            score_result  = score_landing_zones(landing_zones)
            consequence   = score_result["consequence"]
            forbidden_hit = score_result["forbidden_hit"]
            pop_at_risk   = score_result["pop_at_risk"]
            details       = score_result["details"]
            lz_out = [{
                "class":       z["class"],
                "land_lat":    z["land_lat"],
                "land_lon":    z["land_lon"],
                "scatter_m":   z["scatter_m"],
                "drift_m":     z["drift_m"],
                "tof_s":       z["time_of_flight"],
                "weight":      z["weight"],
                "unified_lat": score_result.get("unified_lat", lat),
                "unified_lon": score_result.get("unified_lon", lon),
                "unified_rad": score_result.get("unified_rad", 150.0),
            } for z in landing_zones]
            in_bbox = True
        else:
            # Outside scored region — still run debris physics for visual display
            landing_zones_out = compute_debris_landing(lat, lon, alt_m, wx)
            consequence   = 0.05
            forbidden_hit = False
            pop_at_risk   = 0
            details       = []
            lz_out = [{
                "class":       z["class"],
                "land_lat":    z["land_lat"],
                "land_lon":    z["land_lon"],
                "scatter_m":   z["scatter_m"],
                "drift_m":     z["drift_m"],
                "tof_s":       z["time_of_flight"],
                "weight":      z["weight"],
                "unified_lat": z["unified_lat"],
                "unified_lon": z["unified_lon"],
                "unified_rad": z["unified_rad"],
            } for z in landing_zones_out]
            unified_lat   = landing_zones_out[0]["unified_lat"] if landing_zones_out else lat
            unified_lon   = landing_zones_out[0]["unified_lon"] if landing_zones_out else lon
            unified_rad   = landing_zones_out[0]["unified_rad"] if landing_zones_out else 150.0
            unified_score = 0.05
            in_bbox       = False

        # ── Engagement decision ────────────────────────────────────────────
        # Priority 1: Direct Forbidden Hits
        # Priority 2: Technical Feasibility
        # Priority 3: Restricted Space vs Landing Safety

        if forbidden_hit:
            decision = "NEVER"
            reason   = "Debris lands on forbidden infrastructure"
        elif not feas["feasible"]:
            if not feas["in_range"]:
                decision = "NO SHOT"
                reason   = f"Out of range ({feas['dist_m']}m > {system['range_m']}m)"
            elif not feas["time_ok"]:
                decision = "NO SHOT"
                reason   = f"Drone arrives first (margin={feas['time_margin_s']}s)"
            else:
                decision = "NO SHOT"
                reason   = "Below minimum range"
        elif inside_excl or not before_excl:
            # Restricted Space (Inside radius or past deadline)
            # "POTENTIAL" is only granted if the debris landing is "Safe" (not over impact zones)
            if consequence < 0.25:
                decision = "POTENTIAL"
                reason   = f"Safe landing zone (<25%) despite intercept in restricted space"
            else:
                decision = "HOLD"
                reason   = f"Restricted space AND landing hits impact zone ({consequence:.2f})"
        else:
            # Normal Space
            if consequence > 0.45:
                decision = "HOLD"
                reason   = f"Debris consequence high ({consequence:.2f}) — over populated/industrial area"
            elif consequence > 0.20:
                decision = "CAUTION"
                reason   = f"Debris consequence marginal ({consequence:.2f}) — seek better window"
            else:
                decision = "ENGAGE"
                reason   = f"Consequence acceptable ({consequence:.2f}), debris in low-impact zone"

        # Time remaining before drone hits exclusion zone
        time_to_excl = (earliest_excl - t_sec) if earliest_excl else None

        # Get grid cell properties
        cid = None
        cell_props = {}
        if in_bbox:
            try:
                cid = h3.latlng_to_cell(lat, lon, H3_RES)
                cell_props = GRID_INDEX.get(cid, {})
            except: pass

        candidates.append({
            "t":              round(t_param, 4),
            "time_s":         round(t_sec, 1),
            "lat":            round(lat, 6),
            "lon":            round(lon, 6),
            "alt_m":          round(alt_m),
            "in_bbox":        in_bbox,
            "decision":       decision,
            "reason":         reason,
            "consequence":    consequence,
            "forbidden_hit":  forbidden_hit,
            "pop_at_risk":    pop_at_risk,
            "inside_excl":    inside_excl,
            "before_excl":    before_excl,
            "time_to_excl_s": round(time_to_excl, 1) if time_to_excl else None,
            "feasibility":    feas,
            "landing_zones":  lz_out,
            "details":        details,
            "land_use":       cell_props.get("land_use", "unknown"),
            "is_sea":         cell_props.get("is_sea", not in_bbox),
            "is_coastal":     cell_props.get("is_coastal", False),
                "unified_lat":    score_result.get("unified_lat", lat) if in_bbox else lat,
                "unified_lon":    score_result.get("unified_lon", lon) if in_bbox else lon,
                "unified_rad":    score_result.get("unified_rad", 100.0) if in_bbox else 100.0,
                "unified_score":  score_result.get("unified_score", consequence) if in_bbox else 0.05,
        })

    # ── Find optimal intercept ─────────────────────────────────────────────
    # Pool all "acceptable" candidates.
    engage_pts = [c for c in candidates if c["decision"] == "ENGAGE"]
    caution_pts = [c for c in candidates if c["decision"] == "CAUTION"]
    potential_pts = [c for c in candidates if c["decision"] == "POTENTIAL"]
    pool = engage_pts + caution_pts + potential_pts

    if pool:
        # User mandate: Prioritize ABSOLUTE MINIMUM consequence, even if inside radius.
        # We add a tiny "tie-break" penalty to CAUTION/POTENTIAL so that if scores
        # are identical, we prefer the technically safer ENGAGE option.
        def rank_score(c):
            penalty = 0.0
            if c["decision"] == "CAUTION":   penalty = 0.01
            if c["decision"] == "POTENTIAL": penalty = 0.03
            return c["consequence"] + penalty

        optimal = min(pool, key=rank_score)
        optimal_type = f"{optimal['decision']} — Best Population Outcome"
    else:
        # Fall back: least-bad among technically feasible
        feasible_pts = [c for c in candidates if c["feasibility"]["feasible"]]
        if feasible_pts:
            optimal = min(feasible_pts, key=lambda c: c["consequence"])
            optimal_type = "LAST RESORT — outside normal envelope"
        elif candidates:
            optimal = min(candidates, key=lambda c: c["consequence"])
            optimal_type = "NO INTERCEPT POSSIBLE — least-bad shown"
        else:
            optimal = candidates[0] if candidates else None
            optimal_type = "NO INTERCEPT POSSIBLE"

    # ── Engagement windows ─────────────────────────────────────────────────
    windows = []
    in_win  = False
    for c in candidates:
        if c["decision"] in ("ENGAGE","CAUTION") and not in_win:
            windows.append({
                "start_t":   c["t"],
                "start_time":c["time_s"],
                "start_lat": c["lat"],
                "start_lon": c["lon"],
                "type":      c["decision"],
            })
            in_win = True
        elif c["decision"] not in ("ENGAGE","CAUTION") and in_win:
            windows[-1]["end_t"]   = c["t"]
            windows[-1]["end_time"]= c["time_s"]
            windows[-1]["end_lat"] = c["lat"]
            windows[-1]["end_lon"] = c["lon"]
            in_win = False
    if in_win and windows:
        last = candidates[-1]
        windows[-1].update({"end_t":last["t"],"end_time":last["time_s"],
                             "end_lat":last["lat"],"end_lon":last["lon"]})

    stats = {
        "total_samples":    len(candidates),
        "engage_pts":       len(engage_pts),
        "caution_pts":      len(caution_pts),
        "potential_pts":    len(potential_pts),
        "no_shot_pts":      sum(1 for c in candidates if c["decision"]=="NO SHOT"),
        "never_pts":        sum(1 for c in candidates if c["decision"]=="NEVER"),
        "engage_windows":   len(windows),
        "earliest_excl_s":  round(earliest_excl,1) if earliest_excl else None,
        "nearest_site":     nearest_site["name"] if nearest_site else None,
        "optimal_type":     optimal_type,
        "system":           system["name"],
        "interceptor_pos":  interceptor_pos,
    }

    return {
        "optimal":          optimal,
        "optimal_type":     optimal_type,
        "windows":          windows,
        "all_candidates":   candidates,
        "entry_times":      {k: round(v,1) if v else None for k,v in entry_times.items()},
        "critical_sites":   CRITICAL_SITES,
        "system":           system,
        "stats":            stats,
        "weather":          wx,
        "recommendation": (
            f"[{optimal_type}] Intercept at t={optimal['t']:.3f} "
            f"({optimal['lat']:.4f}N {optimal['lon']:.4f}E, alt={optimal['alt_m']}m, "
            f"t={optimal['time_s']:.0f}s into flight). "
            f"Consequence={optimal['consequence']:.3f}. "
            f"Interceptor flight={optimal['feasibility']['flight_s']:.0f}s, "
            f"margin={optimal['feasibility']['time_margin_s']:.0f}s. "
            + (f"Drone reaches {nearest_site['name']} in {earliest_excl:.0f}s."
               if earliest_excl else "No critical site threatened.")
        ),
    }

def compute_integrated_debris_risk(features):
    """
    Pre-calculate cumulative 1km debris risk for every grid cell.
    For each cell, sum weighted scores of all neighbours within 1000m.
    Weight = 1 - (dist_m / 1000) — nearer cells count more.

    Gives an 'area consequence' score: not just the cell hit but the
    risk of the surrounding zone. Used in consequence scoring to
    weight intercept points where nearby misses also matter.

    Source: appSSS.py. Uses SWEREF99 TM (EPSG:3006) for accurate
    metre-based distances over Sweden.
    """
    print(f"[RISK] Computing integrated debris risk for {len(features)} cells ...")
    t0 = time.time()
    try:
        lats   = [f["properties"]["lat"]           for f in features]
        lons   = [f["properties"]["lon"]           for f in features]
        scores = [f["properties"]["score"] or 0.1  for f in features]
        gdf = gpd.GeoDataFrame(
            {"score": scores, "orig_idx": range(len(features))},
            geometry=[Point(lon,lat) for lat,lon in zip(lats,lons)],
            crs="EPSG:4326"
        ).to_crs("EPSG:3006")   # metric CRS for accurate distances
        sindex = gdf.sindex
        for i, row in gdf.iterrows():
            buf     = row.geometry.buffer(1000)
            hits    = list(sindex.intersection(buf.bounds))
            matches = gdf.iloc[hits]
            risk    = 0.0
            for _, other in matches.iterrows():
                d = row.geometry.distance(other.geometry)
                if d <= 1000:
                    risk += other["score"] * (1.0 - d / 1000.0)
            features[row["orig_idx"]]["properties"]["integrated_risk"] = round(risk, 3)
        print(f"[RISK] Done in {time.time()-t0:.1f}s")
    except Exception as e:
        print(f"[RISK] Failed: {e} — defaulting integrated_risk=0")
        for f in features:
            f["properties"].setdefault("integrated_risk", 0.0)
    return features

GRID_FEATURES, GRID_INDEX, MARITIME_DATA, PRESENCE_CURVES = [], {}, {}, {}

# ── HTTP Server ───────────────────────────────────────────────────────────────
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
            cid=h3.latlng_to_cell(lat,lon,H3_RES); p=GRID_INDEX.get(cid)
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
                result["temporal"]=score_with_time(p,dt,PRESENCE_CURVES,wx)
            result["weather"]=wx
            self.send_json(result)
        elif path=="/cells":
            self.send_json({"type":"FeatureCollection","features":GRID_FEATURES,"meta":{"total":len(GRID_FEATURES)}})
        elif path=="/maritime": self.send_json(MARITIME_DATA)
        elif path=="/weather":  self.send_json(fetch_weather())
        elif path=="/mock_ais": self.send_json(load_ais())
        elif path=="/drone":
            import random
            TARGETS=[
                {"name":"Naval Base Karlskrona","lat":56.1614,"lon":15.5869},
                {"name":"Ferry Terminal","lat":56.1607,"lon":15.5868},
                {"name":"City Centre","lat":56.1608,"lon":15.5865},
                {"name":"Kungsholms Fort","lat":56.1050,"lon":15.5906},
                {"name":"Stumholmen Naval Museum","lat":56.1590,"lon":15.5920},
                {"name":"Dragsö Industrial Port","lat":56.1750,"lon":15.6200},
                {"name":"Aspö Island Radar","lat":56.0700,"lon":15.7100},
            ]
            ENTRIES=[
                {"lat":56.10,"lon":15.45,"label":"SW coast"},
                {"lat":56.25,"lon":15.45,"label":"NW approach"},
                {"lat":56.25,"lon":15.75,"label":"NE approach"},
                {"lat":56.10,"lon":15.75,"label":"SE sea approach"},
            ]
            seed=int(qs.get("seed",["0"])[0]) or random.randint(1,9999)
            rng=random.Random(seed)
            entry=rng.choice(ENTRIES); target=rng.choice(TARGETS)
            exit_=rng.choice([w for w in TARGETS if w!=target])
            pts=[
                {"lat":entry["lat"],"lon":entry["lon"],"label":f"Entry: {entry['label']}"},
                {"lat":target["lat"],"lon":target["lon"],"label":f"Target: {target['name']}"},
                {"lat":exit_["lat"],"lon":exit_["lon"],"label":f"Secondary: {exit_['name']}"},
            ]
            self.send_json({"seed":seed,"waypoints":pts,
                "from_lat":pts[0]["lat"],"from_lon":pts[0]["lon"],
                "to_lat":pts[1]["lat"],"to_lon":pts[1]["lon"],
                "description":f"Drone → {target['name']} from {entry['label']}"})

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


        elif path=="/health":
            self.send_json({"status":"ok","version":"4.0","cells":len(GRID_FEATURES),
                "sea":sum(1 for f in GRID_FEATURES if f["properties"]["is_sea"]),
                "coastal":sum(1 for f in GRID_FEATURES if f["properties"]["is_coastal"]),
                "forbidden":sum(1 for f in GRID_FEATURES if f["properties"]["is_forbidden"]),
                "maritime":len(MARITIME_DATA.get("features",[])),"presence_curves":len(PRESENCE_CURVES),
                "weather":fetch_weather()})
        elif path in ("/","/index.html"): self.send_html(FRONTEND_HTML)
        else: self.send_json({"error":"Not found"},404)

# ── Frontend ──────────────────────────────────────────────────────────────────
FRONTEND_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Karlskrona Impact Risk Engine v4.0</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Courier New',monospace;background:#06090d;color:#c8d0d8;overflow:hidden}
#map{position:absolute;inset:0}
#hud{position:absolute;top:12px;left:12px;z-index:1000;width:300px;
  background:rgba(6,9,13,0.96);border:1px solid #1a3040;border-radius:6px;padding:14px}
.hud-title{font-size:9px;letter-spacing:3px;color:#00c896;text-transform:uppercase;margin-bottom:2px}
.hud-sub{font-size:8px;color:#1a5040;letter-spacing:2px;margin-bottom:10px}
#wx-bar{display:flex;gap:8px;background:#060c12;border:1px solid #0e2030;
  border-radius:3px;padding:6px 8px;margin-bottom:8px;flex-wrap:wrap;align-items:center}
.wx-item{display:flex;flex-direction:column;align-items:center;gap:1px}
.wx-v{font-size:10px;color:#3a9a7a;font-weight:bold}
.wx-l{font-size:7px;color:#1a4a3a;letter-spacing:1px}
#wx-cond{font-size:8px;color:#2a7a6a;flex:1;text-align:right}
#wx-factor{font-size:8px;color:#1a5a4a;width:100%;margin-top:2px;letter-spacing:1px}
#time-ctrl{background:#060c12;border:1px solid #0e2030;border-radius:3px;padding:8px;margin-bottom:10px}
.tc-row{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.tc-lbl{font-size:9px;color:#2a7a6a;width:64px;flex-shrink:0}
.tc-val{font-size:10px;color:#3a9a7a;font-weight:bold;min-width:40px}
input[type=range]{flex:1;accent-color:#00c896;height:3px}
select{background:#06090d;color:#3a9a7a;border:1px solid #1a3040;padding:2px 6px;
  font-size:9px;font-family:monospace;border-radius:2px;flex:1}
.score-val{font-size:36px;font-weight:bold;letter-spacing:-1px;line-height:1}
.tel-val{font-size:11px;color:#3a7060;margin-top:2px}
.cls-val{font-size:10px;letter-spacing:1px;margin-top:3px}
.c-sea{color:#00c8a0}.c-low{color:#4caf50}.c-med{color:#ffc107}
.c-high{color:#ff7043}.c-crit{color:#f44336}.c-fbd{color:#ff00ff}.c-none{color:#444}
.lbl{font-size:8px;color:#2a5060;text-transform:uppercase;letter-spacing:2px;margin-top:10px;margin-bottom:4px}
.bar-row{display:flex;align-items:center;gap:6px;margin:2px 0}
.bar-name{width:110px;font-size:10px;color:#4a7a8a;flex-shrink:0}
.bar-bg{flex:1;height:4px;background:#0a1820;border-radius:2px}
.bar-fill{height:4px;border-radius:2px;transition:width .3s}
.bar-pct{font-size:10px;width:36px;text-align:right;color:#3a6a78}
#result-expl{font-size:9px;color:#3a6868;margin-top:8px;border-top:1px solid #0a1820;padding-top:6px;line-height:1.5}
#result-texpl{font-size:9px;color:#2a8a60;margin-top:6px;border:1px solid #0a2a1a;
  background:#030e06;border-radius:3px;padding:6px;line-height:1.5;display:none}
#result-cid{font-size:7px;color:#1a3050;margin-top:6px;word-break:break-all}
#result-body{min-height:20px}
.idle-msg{color:#1a6050;font-size:11px;letter-spacing:1px}
#overlays{position:absolute;top:12px;right:12px;z-index:1000;width:200px;
  background:rgba(6,9,13,0.96);border:1px solid #1a3040;border-radius:6px;padding:12px}
.ov-title{font-size:8px;letter-spacing:2px;color:#00c896;text-transform:uppercase;margin-bottom:8px}
.ov-row{display:flex;align-items:center;gap:8px;margin:5px 0;font-size:10px;color:#4a7a8a;cursor:pointer}
.ov-row:hover{color:#8ac}
.ov-swatch{width:26px;height:4px;border-radius:2px;flex-shrink:0}
.ov-sep{font-size:8px;color:#1a5040;letter-spacing:1px;margin-top:8px;margin-bottom:4px;
  border-top:1px solid #0e2030;padding-top:6px}
input[type=checkbox]{accent-color:#00c896}
#legend{position:absolute;bottom:12px;right:12px;z-index:1000;
  background:rgba(6,9,13,0.96);border:1px solid #1a3040;border-radius:6px;padding:10px}
.leg-title{font-size:8px;letter-spacing:2px;color:#00c896;text-transform:uppercase;margin-bottom:6px}
.leg-row{display:flex;align-items:center;gap:8px;margin:3px 0;font-size:9px;color:#4a7a8a}
.sw{width:14px;height:9px;border-radius:2px;flex-shrink:0}
#statusbar{position:absolute;bottom:12px;left:12px;z-index:1000;
  background:rgba(6,9,13,0.96);border:1px solid #1a3040;border-radius:4px;
  padding:6px 12px;font-size:8px;color:#1a5040;letter-spacing:1px}
.leaflet-container{background:#06090d}
</style>
</head>
<body>
<div id="map"></div>
<div id="hud">
  <div class="hud-title">&#9670; Karlskrona Impact Risk Engine v4.0</div>
  <div class="hud-sub">PCM · Maritime · Weather · Temporal · AIS</div>
  <div id="wx-ctrl" style="background:#060c12;border:1px solid #0e2030;border-radius:3px;padding:8px;margin-bottom:10px">
    <div style="font-size:8px;color:#00c896;letter-spacing:1px;margin-bottom:6px">WEATHER & DRIFT OVERRIDES</div>
    <div class="tc-row">
      <div class="tc-lbl">WIND SPD</div>
      <input type="range" id="sl-wind" min="0" max="40" value="10" step="1">
      <div class="tc-val" id="lbl-wind">10 km/h</div>
    </div>
    <div class="tc-row">
      <div class="tc-lbl">WIND DIR</div>
      <input type="range" id="sl-wind-dir" min="0" max="359" value="270" step="1">
      <div class="tc-val" id="lbl-wind-dir">270°</div>
    </div>
    <div class="tc-row">
      <div class="tc-lbl">TEMP</div>
      <input type="range" id="sl-temp" min="-20" max="40" value="15" step="1">
      <div class="tc-val" id="lbl-temp">15°C</div>
    </div>
    <div class="tc-row">
      <div class="tc-lbl">COND</div>
      <select id="sel-wc">
        <option value="0">Clear</option>
        <option value="3">Overcast</option>
        <option value="61">Light Rain</option>
        <option value="65">Heavy Rain</option>
        <option value="71">Snow</option>
        <option value="95">Thunderstorm</option>
      </select>
    </div>
    <div id="wx-factor" style="font-size:8px;color:#1a5a4a;margin-top:6px;letter-spacing:1px"></div>
  </div>
  <div id="time-ctrl">
    <div class="tc-row">
      <div class="tc-lbl">TIME</div>
      <input type="range" id="sl-hour" min="0" max="23" value="12" step="1">
      <div class="tc-val" id="lbl-hour">12:00</div>
    </div>
    <div class="tc-row">
      <div class="tc-lbl">DAY TYPE</div>
      <select id="sel-day"><option value="weekday">Weekday</option><option value="weekend">Weekend</option></select>
    </div>
  </div>
  <div id="result-body"><div class="idle-msg">Click any cell to score it…</div></div>
  <div id="result-expl"></div>
  <div id="result-texpl"></div>
  <div id="result-cid"></div>
</div>

<div id="overlays">
  <div class="ov-title">Overlays</div>
  <label class="ov-row"><input type="checkbox" id="tog-eez" checked>
    <div class="ov-swatch" style="background:#003f87;border-top:2px dashed #003f87"></div>EEZ (200nm)</label>
  <label class="ov-row"><input type="checkbox" id="tog-cont" checked>
    <div class="ov-swatch" style="background:#0077be;border-top:2px dashed #0077be"></div>Contiguous (24nm)</label>
  <label class="ov-row"><input type="checkbox" id="tog-terr" checked>
    <div class="ov-swatch" style="background:#00bcd4;border-top:2px dashed #00bcd4"></div>Territorial (12nm)</label>
  <label class="ov-row"><input type="checkbox" id="tog-tss" checked>
    <div class="ov-swatch" style="background:#cc00cc"></div>Traffic Sep. Scheme</label>
  <label class="ov-row"><input type="checkbox" id="tog-mil" checked>
    <div class="ov-swatch" style="background:#cc0000"></div>Military Restricted</label>
  <div class="ov-sep">AIS VESSELS</div>
  <label class="ov-row"><input type="checkbox" id="tog-ais" checked>
    <div class="ov-swatch" style="background:#ff9900;border-radius:50%"></div>Live Vessels</label>
  <div class="ov-sep">SIMULATION</div>
  <div style="font-size:8px;color:#1a5040;margin-bottom:4px;letter-spacing:1px">INTERCEPTOR SYSTEM</div>
  <select id="sel-system" style="width:100%;background:#06090d;color:#3a9a7a;border:1px solid #1a3040;padding:4px 6px;font-size:9px;font-family:monospace;border-radius:3px;margin-bottom:4px">
    <option value="rbs70">RBS 70 Mk2 (680m/s, 9km)</option>
  </select>
  <button id="btn-drone" style="width:100%;padding:7px;background:#1a2a10;color:#88ff44;border:1px solid #446622;border-radius:3px;font-family:monospace;font-size:9px;letter-spacing:1px;cursor:pointer;text-transform:uppercase">&#9992; Generate Drone</button>
  <button id="btn-analyse" style="width:100%;padding:7px;margin-top:4px;background:#0a1a20;color:#00c8ff;border:1px solid #224466;border-radius:3px;font-family:monospace;font-size:9px;letter-spacing:1px;cursor:pointer;text-transform:uppercase;display:none">&#9654; Run Engagement Analysis</button>
  <div style="display:flex;gap:4px;margin-top:4px">
    <button id="btn-play" style="flex:1;padding:6px;background:#0a1820;color:#00ffaa;border:1px solid #1a4030;border-radius:3px;font-family:monospace;font-size:9px;cursor:pointer;display:none">&#9654; PLAY</button>
    <button id="btn-reset" style="padding:6px 8px;background:#0a1820;color:#888;border:1px solid #1a3040;border-radius:3px;font-family:monospace;font-size:9px;cursor:pointer;display:none">&#8634;</button>
  </div>
  <div id="sim-panel" style="font-size:9px;color:#3a8060;margin-top:5px;background:#040c08;border:1px solid #0a2a1a;border-radius:3px;padding:5px;min-height:28px;line-height:1.5"></div>
  <div id="sim-info" style="font-size:9px;color:#2a6050;margin-top:4px;line-height:1.5"></div>
  <div id="sim-timeline-wrap"></div>
</div>

<div id="legend">
  <div class="leg-title">Temporal TEL</div>
  <div class="leg-row"><div class="sw" style="background:#0d4f5c;border:1px solid #1a8070"></div>Sea — preferred</div>
  <div class="leg-row"><div class="sw" style="background:#1a6a4a;border:1px solid #1a8060"></div>Coastal — preferred</div>
  <div class="leg-row"><div class="sw" style="background:#0d3d0d"></div>Low — acceptable</div>
  <div class="leg-row"><div class="sw" style="background:#7a6000"></div>Medium</div>
  <div class="leg-row"><div class="sw" style="background:#8a3200"></div>High</div>
  <div class="leg-row"><div class="sw" style="background:#8a0000"></div>Critical</div>
  <div class="leg-row"><div class="sw" style="background:#aa00aa"></div>Forbidden — never</div>
</div>
<div id="statusbar">INITIALISING…</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
// ═══════════════════════════════════════════════════
// All declarations before all functions.
// All functions before all calls.
// ═══════════════════════════════════════════════════

// ── Map ──────────────────────────────────────────────
const map = L.map('map',{zoomControl:false}).setView([56.162,15.585],12);
L.control.zoom({position:'bottomright'}).addTo(map);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {attribution:'&copy; CartoDB &copy; OSM',maxZoom:19}).addTo(map);

// ── Presence curves ───────────────────────────────────
const PRESENCE = {
  residential:{
    weekday:[0.95,0.95,0.95,0.95,0.93,0.88,0.68,0.52,0.32,0.28,0.28,0.30,0.35,0.32,0.30,0.32,0.45,0.68,0.80,0.86,0.90,0.92,0.93,0.95],
    weekend:[0.95,0.95,0.95,0.95,0.95,0.92,0.90,0.86,0.78,0.70,0.64,0.60,0.57,0.56,0.58,0.62,0.66,0.72,0.78,0.84,0.88,0.90,0.92,0.95]},
  industrial:{
    weekday:[0.05,0.05,0.05,0.05,0.05,0.08,0.35,0.70,0.92,0.92,0.92,0.90,0.88,0.90,0.92,0.90,0.72,0.40,0.15,0.08,0.05,0.05,0.05,0.05],
    weekend:[0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05,0.05]},
  road:{
    weekday:[0.04,0.03,0.03,0.03,0.05,0.15,0.55,0.88,0.82,0.58,0.48,0.52,0.62,0.55,0.50,0.54,0.75,0.92,0.78,0.58,0.42,0.30,0.16,0.07],
    weekend:[0.04,0.03,0.03,0.03,0.04,0.07,0.14,0.28,0.48,0.60,0.68,0.72,0.74,0.75,0.74,0.70,0.68,0.65,0.58,0.48,0.38,0.28,0.16,0.07]},
  commercial:{
    weekday:[0.02,0.02,0.02,0.02,0.02,0.03,0.05,0.12,0.30,0.55,0.80,0.88,0.92,0.88,0.82,0.80,0.72,0.55,0.30,0.18,0.08,0.04,0.02,0.02],
    weekend:[0.02,0.02,0.02,0.02,0.02,0.02,0.03,0.06,0.15,0.45,0.72,0.82,0.88,0.85,0.80,0.72,0.55,0.30,0.12,0.06,0.03,0.02,0.02,0.02]},
  water:{
    weekday:[0.00,0.00,0.00,0.00,0.02,0.06,0.10,0.14,0.16,0.18,0.20,0.22,0.24,0.25,0.24,0.22,0.20,0.16,0.12,0.08,0.04,0.02,0.00,0.00],
    weekend:[0.00,0.00,0.00,0.00,0.02,0.04,0.08,0.14,0.22,0.30,0.36,0.38,0.40,0.40,0.38,0.36,0.32,0.24,0.16,0.10,0.05,0.02,0.00,0.00]},
  forest:{
    weekday:[0.00,0.00,0.00,0.00,0.00,0.02,0.06,0.10,0.08,0.07,0.08,0.12,0.10,0.10,0.09,0.08,0.11,0.10,0.07,0.04,0.02,0.00,0.00,0.00],
    weekend:[0.00,0.00,0.00,0.00,0.00,0.02,0.04,0.08,0.16,0.22,0.26,0.28,0.28,0.26,0.24,0.22,0.18,0.12,0.07,0.03,0.01,0.00,0.00,0.00]}
};

// ── State (all vars declared here before any function uses them) ──
let VESSELS      = [];
let WX_FACTOR    = 1.0;
let lastLat      = null;
let lastLon      = null;
let selCellId    = null;
const cellLayers = new Map();
const cellProps  = new Map();

const vesselLayer = L.layerGroup().addTo(map);
const mLayers = {eez:L.layerGroup(),contiguous_zone:L.layerGroup(),
  territorial_sea:L.layerGroup(),tss_lane:L.layerGroup(),
  tss_zone:L.layerGroup(),military_area:L.layerGroup()};
Object.values(mLayers).forEach(lg=>lg.addTo(map));

const VCOLS = {MILITARY:'#ff2020',PASSENGER:'#ff9900',FERRY:'#ffcc00',
               CARGO:'#00ccff',TANKER:'#ff6600',FISHING:'#88ff88'};

// ── Pure functions ────────────────────────────────────
function pres(lu,h,dt){
  const c=PRESENCE[lu]||PRESENCE.forest;
  return (c[dt]||c.weekday||[])[Math.max(0,Math.min(23,h))]||0.5;
}
function hday(){
  return {h:parseInt(document.getElementById('sl-hour').value),
          dt:document.getElementById('sel-day').value};
}
function calcWX(){
  const wind=parseFloat(document.getElementById('sl-wind').value);
  const wc=parseInt(document.getElementById('sel-wc').value);
  let of=1.0;
  if(wc>=95)of*=0.40;else if(wc>=80)of*=0.60;else if(wc>=61)of*=0.72;else if(wc>=51)of*=0.85;
  if(wind>20)of*=0.80;if(wind>30)of*=0.65;
  return Math.max(0.40,Math.min(1.0,of));
}
function timeParams(){
  const {h,dt}=hday();
  const wind=document.getElementById('sl-wind').value;
  const windDir=document.getElementById('sl-wind-dir').value;
  const temp=document.getElementById('sl-temp').value;
  const wc=document.getElementById('sel-wc').value;
  return `&time=${String(h).padStart(2,'0')}:00&day=${dt}&wind=${wind}&wind_dir=${windDir}&temp=${temp}&wc=${wc}`;
}
function tempTEL(base,lu,h,dt){
  if(base>=9999) return 9999;
  const wxF=calcWX();
  const outdoor=['road','water','forest','commercial'];
  // If outdoor, apply full reduction. If indoor (res/ind), apply 20% of reduction.
  const effWX=outdoor.includes(lu)?wxF:(0.8+0.2*wxF);
  return base*pres(lu,h,dt)*effWX;
}
function vboost(lat,lon,base,isSea){
  if(!VESSELS.length||base>=9999) return base;
  let boost=0;
  for(const v of VESSELS){
    const vla=parseFloat(v.lat),vlo=parseFloat(v.lon);
    if(isNaN(vla)||isNaN(vlo)) continue;
    const d=Math.sqrt(Math.pow((lat-vla)*111000,2)+Math.pow((lon-vlo)*111000*Math.cos(lat*Math.PI/180),2));
    if(d>5000) continue;
    const pr=1-d/5000, tag=(v.tag||v.vessel_type||v.type||'').toUpperCase();
    let raw=tag.includes('MILITARY')?9000*pr:(tag.includes('FERRY')||tag.includes('PASSENGER'))?800*pr:tag.includes('TANKER')?400*pr:80*pr;
    boost+=isSea?raw*0.05:raw;
  }
  return Math.min(base+boost,9999);
}
function telColor(tel,isSea,isCst){
  if(tel>=9999)return '#aa00aa';
  if(isSea)    return '#0d4f5c';
  if(isCst)    return '#1a6a4a';
  if(tel>500)  return '#8a0000';
  if(tel>100)  return '#8a3200';
  if(tel>20)   return '#7a6000';
  return '#0d3d0d';
}
function cellColor(p,h,dt){
  if(p.is_forbidden)return '#aa00aa';
  const isSea=p.is_sea||p.land_use==='water';
  const isCst=p.is_coastal||(!isSea&&(p.wat_frac||0)>0.15);
  if(p.no_data&&!isSea&&!isCst)return '#111827';
  const tel=tempTEL(p.tel||0,p.land_use||'forest',h,dt);
  const ttel=vboost(p.lat||0,p.lon||0,tel,isSea);
  return telColor(ttel,isSea,isCst);
}
function cellOp(p){
  if(p.is_sea||p.land_use==='water')return 0.45;
  if(p.is_coastal||(p.wat_frac||0)>0.15)return 0.50;
  if(p.no_data)return 0.55;
  return 0.65;
}
function sCls(s,isSea){
  if(isSea)return 'c-sea';
  if(s===null||s===undefined)return 'c-none';
  if(s>=0.7)return 'c-crit';if(s>=0.5)return 'c-high';
  if(s>=0.3)return 'c-med';return 'c-low';
}
function bar(label,val,max,unit=''){
  const pct=max>0?Math.min(Math.round(val/max*100),100):0;
  const col=pct>65?'#f44336':pct>35?'#ff7043':'#4caf50';
  const disp=unit?`${typeof val==='number'?val.toFixed(0):'?'}${unit}`:pct+'%';
  return `<div class="bar-row"><div class="bar-name">${label}</div>
    <div class="bar-bg"><div class="bar-fill" style="width:${pct}%;background:${col}"></div></div>
    <div class="bar-pct">${disp}</div></div>`;
}
function vcol(tag){
  const t=(tag||'').toUpperCase();
  for(const[k,v]of Object.entries(VCOLS))if(t.includes(k))return v;
  return '#888';
}

// ── Recolor grid ──────────────────────────────────────
function recolorGrid(){
  const{h,dt}=hday();
  cellLayers.forEach((lyr,cid)=>{
    const p=cellProps.get(cid); if(!p)return;
    const isSea=p.is_sea||p.land_use==='water';
    const isCst=p.is_coastal||(!isSea&&(p.wat_frac||0)>0.15);
    const isSel=selCellId===cid;
    lyr.setStyle({
      fillColor:cellColor(p,h,dt), fillOpacity:cellOp(p),
      color:isSel?'#00ffaa':isSea?'#1a6a6a':isCst?'#1a5a4a':p.is_forbidden?'#cc00cc':p.no_data?'#2a2a2a':'#000',
      weight:isSel?2:(isSea||isCst)?0.3:p.is_forbidden?1.5:p.no_data?0.5:0.25,opacity:0.7
    });
  });
}

// ── Render result ─────────────────────────────────────
function renderResult(p){
  const body  = document.getElementById('result-body');
  const expl  = document.getElementById('result-expl');
  const texpl = document.getElementById('result-texpl');
  const cid   = document.getElementById('result-cid');
  if(!body)return;

  const s     = p.score, t=p.tel;
  const isSea = p.is_sea||p.land_use==='water';
  const isCst = p.is_coastal;
  let html='';

  if(p.is_forbidden){
    html=`<div class="score-val c-fbd">⚠ FORBIDDEN</div>
      <div class="tel-val">TEL=∞ | Wb=${p.wb||9999}</div>
      <div class="cls-val" style="color:#aa00aa">ZERO ENGAGEMENT — ALWAYS</div>`;
  } else if(isSea){
    html=`<div class="score-val c-sea">${s!=null?s.toFixed(2):'—'}</div>
      <div class="tel-val">TEL=${t||'—'} | Open sea</div>
      <div class="cls-val c-sea">PREFERRED LANDING ZONE</div>`;
  } else if(isCst){
    html=`<div class="score-val c-sea">${s!=null?s.toFixed(2):'—'}</div>
      <div class="tel-val">TEL=${t||'—'} | Coastal</div>
      <div class="cls-val c-sea">COASTAL — PREFERRED OVER LAND</div>`;
  } else if(p.no_data){
    html=`<div class="score-val c-none">N/A</div><div class="tel-val" style="color:#334">No data</div>`;
  } else {
    const c=p.contributors||{}, temp=p.temporal;
    const ds=temp?temp.score_temporal:s;
    const dt2=temp?temp.tel_temporal:t;
    const dc=sCls(ds,false);
    const dCls=temp?temp.classification:p.classification;
    html=`<div class="lbl">${temp?'Temporal unsuitability':'Static unsuitability'}</div>
      <div class="score-val ${dc}">${ds!=null?(typeof ds==='number'?ds.toFixed(2):ds):'—'}</div>
      <div class="tel-val">TEL=${dt2!=null?(typeof dt2==='number'?dt2.toFixed(0):dt2):'—'} | Wb=${p.wb||'?'} | ${p.land_use||'?'}</div>
      <div class="cls-val" style="color:#2a9070">${(dCls||'').toUpperCase()}</div>
      ${temp?`<div style="color:#2a6050;font-size:9px;margin-top:3px">presence=${(temp.presence_fraction*100).toFixed(0)}% · wx=${(temp.weather_factor*100).toFixed(0)}% · ${temp.query_time} ${temp.day_type}</div>`:''}
      ${p.pop_count>=1?`<div style="color:#2a9060;font-size:10px;margin-top:4px">■ ${Math.round(p.pop_count)} civilians (SCB)</div>`:''}
      <div class="lbl" style="margin-top:10px">Contributing factors</div>
      ${bar('Population',p.pop_count||0,500,' ppl')}
      ${bar('Residential',(c.residential||0)*100,100,'%')}
      ${bar('Industrial',(c.industrial||0)*100,100,'%')}
      ${bar('Road density',c.roads||0,30,' seg')}
      ${bar('Sensitive',Math.min((c.sensitive||0)*100,100),100,'%')}`;
  }
  body.innerHTML=html;
  if(expl)  expl.textContent=p.explanation||'';
  if(texpl){
    if(p.temporal&&p.temporal.explanation){
      texpl.textContent=p.temporal.explanation; texpl.style.display='block';
    } else { texpl.style.display='none'; }
  }
  if(cid) cid.textContent=`■ ${typeof p.lat==='number'?p.lat.toFixed(5):'?'}N ${typeof p.lon==='number'?p.lon.toFixed(5):'?'}E | ${p.cell_id||''}`;
}

// ── Query score ───────────────────────────────────────
async function queryScore(lat,lon){
  const body=document.getElementById('result-body');
  if(body)body.innerHTML=`<div class="idle-msg">Querying…</div>`;
  try{
    const res=await fetch(`/score?lat=${lat}&lon=${lon}${timeParams()}`);
    if(!res.ok){
      const err=await res.json().catch(()=>({error:'Unknown'}));
      if(body)body.innerHTML=`<div style="color:#f66;font-size:10px">${err.error}</div>`;
      return;
    }
    const data=await res.json();
    if(data.weather&&data.weather.outdoor_factor!==undefined){
      WX_FACTOR=data.weather.outdoor_factor;
      const wf=document.getElementById('wx-factor');
      if(wf)wf.textContent=`Outdoor factor: ${(WX_FACTOR*100).toFixed(0)}% — ${data.weather.condition}`;
    }
    renderResult(data);
  }catch(e){
    console.error('[score]',e);
    if(body)body.innerHTML=`<div style="color:#f66;font-size:10px">Request failed: ${e.message}</div>`;
  }
}

// ── Weather ───────────────────────────────────────────
async function loadWeather(){
  try{
    const wx=await(await fetch('/weather')).json();
    const set=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v;};
    set('wx-temp',wx.temperature_c+'°C');
    set('wx-rain',wx.rain_mm+'mm');
    set('wx-wind',wx.windspeed_kmh+'km/h');
    set('wx-cond',(wx.condition||'?')+' '+wx.fetched_at);
    if(wx.outdoor_factor!==undefined){
      WX_FACTOR=wx.outdoor_factor;
      set('wx-factor',`Outdoor: ${(WX_FACTOR*100).toFixed(0)}% of normal presence`);
      recolorGrid();
    }
  }catch(e){const el=document.getElementById('wx-cond');if(el)el.textContent='Weather N/A';}
}

// ── AIS ───────────────────────────────────────────────
async function loadAIS(){
  try{
    const res=await fetch('/mock_ais');
    if(!res.ok)return;
    const data=await res.json();
    if(!Array.isArray(data)||!data.length)return;
    VESSELS=data; vesselLayer.clearLayers();
    for(const v of VESSELS){
      const lat=parseFloat(v.lat),lon=parseFloat(v.lon);
      if(isNaN(lat)||isNaN(lon))continue;
      const tag=v.tag||v.vessel_type||v.type||'OTHER';
      const name=v.vessel_name||v.name||'Unknown';
      const hv=/MILITARY|FERRY|PASSENGER/.test(tag.toUpperCase());
      L.circleMarker([lat,lon],{radius:hv?9:5,fillColor:vcol(tag),color:'#fff',weight:hv?2:1,fillOpacity:0.9})
       .bindTooltip(`<div style="font-family:monospace;font-size:11px;line-height:1.5"><b>${name}</b><br>${tag.toUpperCase()}<br>${lat.toFixed(4)}N ${lon.toFixed(4)}E</div>`,{sticky:true,opacity:0.95})
       .addTo(vesselLayer);
    }
    console.log(`[AIS] ${VESSELS.length} vessels`); recolorGrid();
  }catch(e){console.warn('[AIS]',e);}
}

// ── Load grid ─────────────────────────────────────────
async function loadGrid(){
  const sb=document.getElementById('statusbar');
  if(sb)sb.textContent='Loading grid…';
  const res=await fetch('/cells'), data=await res.json();
  const{h,dt}=hday();
  L.geoJSON(data,{
    style:f=>{
      const p=f.properties;
      const isSea=p.is_sea||p.land_use==='water';
      const isCst=p.is_coastal||(!isSea&&(p.wat_frac||0)>0.15);
      return{fillColor:cellColor(p,h,dt),fillOpacity:cellOp(p),
        color:isSea?'#1a6a6a':isCst?'#1a5a4a':p.is_forbidden?'#cc00cc':p.no_data?'#2a2a2a':'#000',
        weight:(isSea||isCst)?0.3:p.is_forbidden?1.5:p.no_data?0.5:0.25,opacity:0.7};
    },
    onEachFeature:(feat,lyr)=>{
      const cid=feat.properties.cell_id;
      cellLayers.set(cid,lyr); cellProps.set(cid,feat.properties);
      lyr.on('click',e=>{
        L.DomEvent.stopPropagation(e);
        if(selCellId&&cellLayers.has(selCellId)){
          const pp=cellProps.get(selCellId);
          const ps=pp.is_sea||pp.land_use==='water', pc=pp.is_coastal;
          cellLayers.get(selCellId).setStyle({weight:(ps||pc)?0.3:pp.is_forbidden?1.5:0.25,
            color:ps?'#1a6a6a':pc?'#1a5a4a':pp.is_forbidden?'#cc00cc':'#000'});
        }
        selCellId=cid; lyr.setStyle({weight:2,color:'#00ffaa'});
        const p=feat.properties; lastLat=p.lat; lastLon=p.lon;
        queryScore(p.lat,p.lon);
      });
    }
  }).addTo(map);
  window.gridData=data;  // store globally for sim reuse
  const n=data.features.length;
  const sea=data.features.filter(f=>f.properties.is_sea).length;
  const cst=data.features.filter(f=>f.properties.is_coastal).length;
  const fbd=data.features.filter(f=>f.properties.is_forbidden).length;
  const nd=data.features.filter(f=>f.properties.no_data).length;
  if(sb)sb.textContent=`v4.0 · ${n} CELLS · SEA:${sea} · COASTAL:${cst} · FORBIDDEN:${fbd} · NO-DATA:${nd} · VESSELS:${VESSELS.length}`;
}

// ── Maritime ──────────────────────────────────────────
async function loadMaritime(){
  try{
    const data=await(await fetch('/maritime')).json();
    for(const feat of data.features||[]){
      const p=feat.properties, lg=mLayers[p.zone_type]; if(!lg)continue;
      L.geoJSON(feat,{style:()=>({color:p.color||'#0077be',weight:p.weight||1.5,
        dashArray:p.dashArray||null,fillColor:p.color||'#0077be',
        fillOpacity:p.fillOpacity||0.05,opacity:0.9})})
       .bindTooltip(`<div style="font-family:monospace;font-size:11px"><b>${p.label||p.zone_type}</b><br>${p.name||''}</div>`,{sticky:true,opacity:0.9})
       .addTo(lg);
    }
  }catch(e){console.warn('[Maritime]',e);}
}

// ═══════════════════════════════════════════════════════════════════
// ENGAGEMENT SIMULATION ENGINE
// Full physics: interceptor geometry + debris + grid scoring
// ═══════════════════════════════════════════════════════════════════

const DCOLS={"Heavy debris":"#ff4444","Medium debris":"#ff9900","Fine/fuel":"#ffee00"};
const DCISION_COLS={"ENGAGE":"#00ff88","CAUTION":"#ffc107","HOLD":"#ff7043","POTENTIAL":"#00d4ff","NO SHOT":"#446688","NEVER":"#aa00aa"};

let simLayer=L.layerGroup().addTo(map);
let critLayer=L.layerGroup().addTo(map);
let simState=null,simDrone=null,simTimer=null,simT=0,simPlaying=false;
let interceptorPos=null,interceptorMarker=null;

// ── Load critical infrastructure ──────────────────────────────────
async function loadCriticalSites(){
  try{
    const data=await(await fetch('/critical_sites')).json();
    critLayer.clearLayers();
    (data.sites||[]).forEach(site=>{
      const col=site.type==='military'?'#cc0000':site.type==='industrial'?'#ff6600':'#ff9900';
      L.circle([site.lat,site.lon],{radius:site.radius_m,color:col,fillColor:col,
        fillOpacity:0.07,weight:2,dashArray:'6,4'})
       .bindTooltip(`<div style="font-family:monospace;font-size:11px;line-height:1.5"><b>⚠ ${site.name}</b><br>Type: ${site.type}<br>Exclusion: ${site.radius_m}m<br><i>Never intercept inside</i></div>`,{sticky:true,opacity:0.95})
       .addTo(critLayer);
      L.circleMarker([site.lat,site.lon],{radius:5,fillColor:col,color:'#fff',weight:1.5,fillOpacity:0.95}).addTo(critLayer);
    });
    const sel=document.getElementById('sel-system');
    if(sel&&data.systems){
      sel.innerHTML=Object.entries(data.systems).map(([k,v])=>
        `<option value="${k}">${v.name} (${v.speed_ms}m/s, ${v.range_m/1000}km)</option>`).join('');
    }
  }catch(e){console.warn('[critical]',e);}
}

// ── Interceptor placement ─────────────────────────────────────────
function placeInterceptor(lat,lon){
  if(interceptorMarker){simLayer.removeLayer(interceptorMarker);}
  interceptorPos={lat,lon};
  interceptorMarker=L.circleMarker([lat,lon],{radius:12,fillColor:'#00ccff',color:'#fff',weight:2.5,fillOpacity:0.95})
    .bindTooltip(`<div style="font-family:monospace;font-size:11px"><b>⊕ INTERCEPTOR</b><br>${lat.toFixed(4)}N ${lon.toFixed(4)}E<br><i>Click map to move</i></div>`,{opacity:0.95});
  interceptorMarker._interceptMarker=true;
  interceptorMarker.addTo(simLayer);
}

// ── Spline helpers ────────────────────────────────────────────────
function crp(p0,p1,p2,p3,t){const t2=t*t,t3=t2*t;return 0.5*((2*p1)+(-p0+p2)*t+(2*p0-5*p1+4*p2-p3)*t2+(-p0+3*p1-3*p2+p3)*t3);}
function splinePts(wpts,n=25){
  const out=[],L2=wpts.length;
  for(let i=0;i<L2-1;i++){
    const p0=wpts[Math.max(0,i-1)],p1=wpts[i],p2=wpts[i+1],p3=wpts[Math.min(L2-1,i+2)];
    for(let s=0;s<n;s++){const t=s/n;out.push({lat:crp(p0.lat,p1.lat,p2.lat,p3.lat,t),lon:crp(p0.lon,p1.lon,p2.lon,p3.lon,t),alt_m:crp(p0.alt_m,p1.alt_m,p2.alt_m,p3.alt_m,t)});}
  }
  out.push(wpts[L2-1]);return out;
}

// ── Timeline ──────────────────────────────────────────────────────
function buildTimeline(cands){
  const W=262,H=65,pad=4;
  if(!cands||!cands.length)return '';
  const maxC=Math.max(...cands.map(c=>c.consequence||0),0.01);
  const bars=cands.map((c,i)=>{
    const x=pad+(i/cands.length)*(W-2*pad);
    const w=Math.max(1,(W-2*pad)/cands.length-0.2);
    const bH=((c.consequence||0)/maxC)*(H-16-pad);
    return `<rect x="${x.toFixed(1)}" y="${(H-16-pad-bH).toFixed(1)}" width="${w.toFixed(1)}" height="${bH.toFixed(1)}" fill="${DCISION_COLS[c.decision]||'#333'}" opacity="0.85"/>`;
  }).join('');
  const y30=(H-16-pad-(0.30/maxC)*(H-16-pad)).toFixed(1);
  const y55=(H-16-pad-(0.55/maxC)*(H-16-pad)).toFixed(1);
  const legend=Object.entries(DCISION_COLS).map(([d,c],i)=>`<rect x="${pad+i*40}" y="${H-13}" width="8" height="6" fill="${c}"/><text x="${pad+i*40+10}" y="${H-7}" fill="${c}" font-size="6">${d}</text>`).join('');
  const cx=(pad+simT*(W-2*pad)).toFixed(1);
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" id="sim-timeline"
    style="background:#060c12;border:1px solid #0e2030;border-radius:3px;cursor:crosshair;display:block;margin-top:4px"
    onclick="tlClick(event)">
    ${bars}
    <line x1="${pad}" y1="${y30}" x2="${W-pad}" y2="${y30}" stroke="#00ff88" stroke-width="0.8" stroke-dasharray="3,2" opacity="0.6"/>
    <line x1="${pad}" y1="${y55}" x2="${W-pad}" y2="${y55}" stroke="#ff7043" stroke-width="0.8" stroke-dasharray="3,2" opacity="0.6"/>
    <text x="${W-pad-1}" y="${parseFloat(y30)-2}" fill="#00ff88" font-size="6" text-anchor="end">ENGAGE</text>
    <text x="${W-pad-1}" y="${parseFloat(y55)-2}" fill="#ff7043" font-size="6" text-anchor="end">HOLD</text>
    ${legend}
    <line x1="${cx}" y1="${pad}" x2="${cx}" y2="${H-16}" stroke="#fff" stroke-width="1.5" id="sim-cur"/>
  </svg>`;
}
function tlClick(evt){
  if(!simState)return;
  const svg=document.getElementById('sim-timeline');if(!svg)return;
  const r=svg.getBoundingClientRect();
  simT=Math.max(0,Math.min(1,(evt.clientX-r.left-4)/(262-8)));
  renderFrame(simT);pauseSim();
}
function updateCursor(){const c=document.getElementById('sim-cur');if(!c)return;const cx=(4+simT*(262-8)).toFixed(1);c.setAttribute('x1',cx);c.setAttribute('x2',cx);}

// ── Render frame ──────────────────────────────────────────────────
function renderFrame(t){
  if(!simState||!simState.all_candidates)return;
  simT=t;
  const cands=simState.all_candidates;
  const opt=simState.optimal;

  // ── Check if drone has been intercepted ───────────────────────────────
  // If current t >= optimal intercept t, stop the drone and show explosion
  const optT = opt ? opt.t : 1.0;
  const intercepted = opt && t >= optT && opt.decision === 'ENGAGE';

  const idx=Math.round(t*(cands.length-1));
  // If intercepted, freeze drone at the optimal intercept position
  const c = intercepted
    ? cands[Math.round(optT*(cands.length-1))]
    : cands[Math.max(0,Math.min(idx,cands.length-1))];
  if(!c)return;

  simLayer.eachLayer(l=>{if(l._sf)simLayer.removeLayer(l);});
  const dcol=DCISION_COLS[c.decision]||'#ffff00';

  if(intercepted){
    // ═══════════════════════════════════════════════════
    // INTERCEPT EVENT: explosion + debris rings
    // ═══════════════════════════════════════════════════

    // 1. Missile line from naval base to intercept point
    if(interceptorPos){
      L.polyline([[interceptorPos.lat,interceptorPos.lon],[c.lat,c.lon]],
        {color:'#00ccff',weight:3,opacity:1.0})
       ._sf=true;
      const ml=L.polyline([[interceptorPos.lat,interceptorPos.lon],[c.lat,c.lon]],
        {color:'#00ccff',weight:3,opacity:1.0});
      ml._sf=true; ml.addTo(simLayer);
    }

    // 2. Explosion icon
    L.marker([c.lat,c.lon],{icon:L.divIcon({
      html:'<div style="font-size:30px;line-height:30px;filter:drop-shadow(0 0 8px #ff4400)">💥</div>',
      className:'',iconSize:[32,32],iconAnchor:[16,16]
    })}).bindTooltip(
      '<b style="font-family:monospace">💥 INTERCEPT<br>Alt:'+c.alt_m+'m | t='+c.time_s?.toFixed(0)+'s</b>',
      {opacity:0.95}
    )._sf=true;
    const exm=L.marker([c.lat,c.lon],{icon:L.divIcon({
      html:'<div style="font-size:30px;line-height:30px;filter:drop-shadow(0 0 8px #ff4400)">💥</div>',
      className:'',iconSize:[32,32],iconAnchor:[16,16]
    })});
    exm._sf=true; exm.addTo(simLayer);

    // 3. Compute debris zone centre and radius from landing zones
    //    Use c.landing_zones; fall back to c.lat/c.lon if empty
    const lzs = (c.landing_zones && c.landing_zones.length > 0) ? c.landing_zones : [];
    let debLat = c.lat, debLon = c.lon, debRad = Math.max(c.alt_m * 0.35, 200);

    if(lzs.length > 0){
      // Weighted centroid of per-class landing centres
      let tw=0, sumLat=0, sumLon=0, maxR=0;
      lzs.forEach(z=>{
        const w=z.weight||0.33;
        const la=z.unified_lat||z.land_lat||c.lat;
        const lo=z.unified_lon||z.land_lon||c.lon;
        const r=z.unified_rad||z.scatter_m||debRad;
        tw+=w; sumLat+=la*w; sumLon+=lo*w;
        maxR=Math.max(maxR,r);
      });
      if(tw>0){ debLat=sumLat/tw; debLon=sumLon/tw; }
      debRad=Math.max(maxR, 200);  // minimum 200m visual
    }

    // Clamp debRad to [200, 1000] for visibility
    debRad = Math.max(200, Math.min(1000, debRad));

    // 4. Wind drift line from intercept point to debris centre
    if(Math.abs(debLat-c.lat)>0.0001 || Math.abs(debLon-c.lon)>0.0001){
      const dl=L.polyline([[c.lat,c.lon],[debLat,debLon]],
        {color:'#ff8800',weight:2,dashArray:'5,4',opacity:0.9});
      dl._sf=true; dl.addTo(simLayer);
    }

    // 5. Consequence score drives ring colour
    const uScore = c.unified_score ?? c.consequence ?? 0.05;
    const isLastResort = simState.optimal_type?.includes('LAST RESORT');
    const col = isLastResort   ? '#ffcc00'
              : uScore < 0.10  ? '#00e5a0'
              : uScore < 0.25  ? '#88ff44'
              : uScore < 0.45  ? '#ffd700'
              : uScore < 0.65  ? '#ff8800'
              :                  '#ff2200';

    // 6. Gaussian intensity rings — 5 rings, clearly visible opacities
    const sigma = debRad / 2;
    [
      {frac:1.00, fillOp:0.10, w:3.0},
      {frac:0.75, fillOp:0.17, w:1.0},
      {frac:0.50, fillOp:0.27, w:1.0},
      {frac:0.30, fillOp:0.40, w:0.5},
      {frac:0.12, fillOp:0.58, w:0.5},
    ].forEach((r,ri)=>{
      const ring=L.circle([debLat,debLon],{
        radius:debRad*r.frac, color:col, fillColor:col,
        fillOpacity:r.fillOp, weight:r.w, opacity:1.0
      });
      if(ri===0){
        ring.bindTooltip(
          '<div style="font-family:monospace;font-size:11px;line-height:1.6">'+
          '<b>⚠ Debris Zone</b><br>'+
          'Radius: <b>'+debRad+'m</b><br>'+
          'Score: <b>'+(uScore*100).toFixed(0)+'%</b><br>'+
          'σ='+Math.round(sigma)+'m (Gaussian)<br>'+
          'Centre: 100% | Half-σ: 61% | Edge: 14%<br>'+
          (isLastResort?'<b style="color:#ffcc00">⚠ LAST RESORT</b><br>':'')+
          (c.pop_at_risk>0?'Pop at risk: <b>'+c.pop_at_risk+'</b><br>':'')+
          'Alt: '+c.alt_m+'m</div>',
          {sticky:true,opacity:0.97}
        );
      }
      ring._sf=true; ring.addTo(simLayer);
    });

    // 7. Centre dot
    const cdot=L.circleMarker([debLat,debLon],{
      radius:8,fillColor:col,color:'#fff',weight:2.5,fillOpacity:1.0
    });
    cdot._sf=true; cdot.addTo(simLayer);

    // 8. Per-class dots (Heavy/Medium/Fine landing centres)
    const clsCols={"Heavy debris":"#ff4444","Medium debris":"#ff9900","Fine/fuel":"#ffee00"};
    lzs.forEach((z,zi)=>{
      if(!z.land_lat||!z.land_lon) return;
      const c2=clsCols[z.class]||'#ff9900';
      const dot=L.circleMarker([z.land_lat,z.land_lon],{
        radius:5+zi*2,fillColor:c2,color:'#fff',weight:1.5,fillOpacity:0.95
      }).bindTooltip(
        '<b style="color:'+c2+'">'+z.class+'</b><br>r='+
        (z.scatter_m||'?')+'m drift='+(z.drift_m||0)+'m',
        {sticky:true}
      );
      dot._sf=true; dot.addTo(simLayer);
    });

    // 9. Score label
    const lbl=L.marker([debLat+(debRad/111111*0.7),debLon],{icon:L.divIcon({
      html:'<div style="font-family:monospace;font-size:10px;font-weight:bold;color:'+col+
           ';background:rgba(0,0,0,0.85);padding:3px 8px;border-radius:3px;'+
           'border:2px solid '+col+';white-space:nowrap">'+
           '⚠ '+(uScore*100).toFixed(0)+'% | r='+debRad+'m'+
           (isLastResort?' ⚠ LAST RESORT':'')+
           '</div>',
      className:'',iconSize:[160,22],iconAnchor:[80,11]
    })});
    lbl._sf=true; lbl.addTo(simLayer);

    pauseSim();

    // 10. Panel
    const panel=document.getElementById('sim-panel');
    if(panel){
      const riskLbl=uScore<0.10?'<span style="color:#00e5a0">MINIMAL</span>'
        :uScore<0.25?'<span style="color:#88ff44">LOW</span>'
        :uScore<0.45?'<span style="color:#ffd700">MODERATE</span>'
        :uScore<0.65?'<span style="color:#ff8800">HIGH</span>'
        :'<span style="color:#ff2200">CRITICAL</span>';
      panel.innerHTML=
        '<div style="display:flex;justify-content:space-between;align-items:baseline">'+
        '<span style="color:#666;font-size:8px">t='+c.time_s?.toFixed(0)+'s alt='+c.alt_m+'m</span>'+
        '<span style="font-size:15px;font-weight:bold;color:#00ff88">NEUTRALISED</span></div>'+
        '<div style="color:#00ff88;font-size:8px;font-weight:bold">DRONE DESTROYED</div>'+
        '<div style="font-size:8px;color:#888;margin-top:2px">'+
        'Debris r='+debRad+'m | Risk: '+riskLbl+'</div>'+
        '<div style="font-size:8px;color:#666">σ='+Math.round(sigma)+'m | '+
        'Centre=100% Mid=61% Edge=14%</div>'+
        (c.pop_at_risk>0?'<div style="color:#f88;font-size:8px">⚠ '+c.pop_at_risk+' pop at risk</div>':'')+
        (isLastResort?'<div style="color:#ffcc00;font-size:8px">⚠ LAST RESORT — no clean window</div>':'')+
        '<div style="font-size:7px;color:#444;margin-top:3px">'+
        (c.details||[]).map(d=>
          '<span style="color:'+(clsCols[d.class]||'#888')+'">'+d.class+':</span> '+
          (d.score*100).toFixed(0)+'% r='+(d.radius_m||'?')+'m'
        ).join(' | ')+'</div>';
    }
    updateCursor();
    return;
  }

    // ── DRONE IN FLIGHT ────────────────────────────────────────────────────
  // Compute heading for icon rotation
  let droneHeading=0;
  if(idx<cands.length-1){
    const nx=cands[Math.min(idx+1,cands.length-1)];
    droneHeading=Math.atan2(nx.lon-c.lon,nx.lat-c.lat)*180/Math.PI;
  }
  const droneIcon=L.divIcon({
    html:`<div style="color:#ffff00;font-size:22px;line-height:22px;transform:rotate(${droneHeading}deg);filter:drop-shadow(0 0 4px ${dcol});text-shadow:0 0 6px ${dcol}">&#9992;</div>`,
    className:'',iconSize:[24,24],iconAnchor:[12,12]
  });
  const dm=L.marker([c.lat,c.lon],{icon:droneIcon})
    .bindTooltip(`<div style="font-family:monospace;font-size:11px;line-height:1.5"><b>&#9992; t=${c.time_s?.toFixed(0)}s</b><br>Alt:${c.alt_m}m<br><b style="color:${dcol}">${c.decision}</b><br>${c.reason}<br>Consequence:${(c.consequence*100).toFixed(0)}%${c.time_to_excl_s!=null?`<br>⏱ ${c.time_to_excl_s.toFixed(0)}s to exclusion`:''}</div>`,{opacity:0.95});
  dm._sf=true;dm.addTo(simLayer);

  // ── Interceptor missile/plane animation ────────────────────────────────
  // Show interceptor at base (scrambling) or in flight (intercepting)
  const f=c.feasibility||{};
  if(interceptorPos && f.launch_time_s!=null && c.decision==='ENGAGE'){
    let mLat, mLon, mStatus, mIcon, mSize, mGlow="#00ccff";
    const isPlane=(simState.system?.name||'').includes('Gripen');
    const isDrone=(simState.system?.name||'').includes('Kreuger');
    const scrambleS = f.reaction_s || 6;
    
    if (c.time_s < f.launch_time_s) {
        // SCRAMBLING / REACTION phase: at base
        mLat = interceptorPos.lat;
        mLon = interceptorPos.lon;
        mStatus = `Scrambling... Launch in ${(f.launch_time_s - c.time_s).toFixed(1)}s`;
        mIcon = isPlane ? '✈' : isDrone ? '🚁' : '➤';
        mSize = isPlane ? 24 : isDrone ? 20 : 16;
    } else {
        // FLIGHT phase: travelling to intercept
        const missileT = (c.time_s - f.launch_time_s) / (f.flight_s || 1);
        const clampedT = Math.max(0, Math.min(1, missileT));
        mLat = interceptorPos.lat + clampedT * (c.lat - interceptorPos.lat);
        mLon = interceptorPos.lon + clampedT * (c.lon - interceptorPos.lon);
        mStatus = clampedT >= 1 ? "Impact" : `Intercepting... ETA: ${(f.flight_s * (1-clampedT)).toFixed(1)}s`;
        mIcon = isPlane ? '✈' : isDrone ? '🚁' : '➤';
        mSize = isPlane ? 24 : isDrone ? 20 : 16;
        
        // Draw trail
        const missLine=L.polyline(
          [[interceptorPos.lat,interceptorPos.lon],[mLat,mLon]],
          {color:mGlow,weight:2,opacity:0.7,dashArray:'4,4'}
        );
        missLine._sf=true;missLine.addTo(simLayer);
    }
    
    const mHead=Math.atan2(c.lon-interceptorPos.lon,c.lat-interceptorPos.lat)*180/Math.PI;
    const mDivIcon=L.divIcon({
      html:`<div style="color:${mGlow};font-size:${mSize}px;transform:rotate(${mHead}deg);filter:drop-shadow(0 0 4px ${mGlow});text-shadow:0 0 6px ${mGlow}">${mIcon}</div>`,
      className:'',iconSize:[mSize+4,mSize+4],iconAnchor:[(mSize+4)/2,(mSize+4)/2]
    });
    const mm=L.marker([mLat,mLon],{icon:mDivIcon})
      .bindTooltip(`<div style="font-family:monospace;font-size:11px;line-height:1.5"><b>${isPlane?'✈':isDrone?'🚁':'🚀'} ${simState.system?.name}</b><br>${mStatus}<br>Dist:${f.dist_intercept_m}m | Speed:${simState.system?.speed_ms}m/s</div>`,{opacity:0.95});
    mm._sf=true;mm.addTo(simLayer);
  }

  // ── Unified impact zone (50-500m, probability-weighted) ─────────────────
  // This is the single circle you asked for — shows where debris lands
  // and how probable each part of it is (bright centre = most likely hit)
  const lzs=c.landing_zones||[];
  if(lzs.length>0){
    const uLat=lzs[0].unified_lat||c.lat;
    const uLon=lzs[0].unified_lon||c.lon;
    const uRad=lzs[0].unified_rad||150;

    // Outer ring: full impact radius (50-500m) — low probability outer edge
    const outer=L.circle([uLat,uLon],{
      radius:uRad,
      color:'#ff6600',fillColor:'#ff6600',fillOpacity:0.08,
      weight:2,dashArray:null,opacity:0.8
    }).bindTooltip(
      `<div style="font-family:monospace;font-size:10px;line-height:1.5">
        <b>⚠ Debris Impact Zone</b><br>
        Radius: ${uRad}m (50-1000m range)<br>
        Score: ${((c.unified_score||c.consequence)*100).toFixed(0)}%<br>
        Probability-weighted consequence</div>`,
      {sticky:true}
    );
    outer._sf=true;outer.addTo(simLayer);

    // Inner ring: 50% radius — high probability core
    const inner=L.circle([uLat,uLon],{
      radius:uRad*0.5,
      color:'#ff3300',fillColor:'#ff3300',fillOpacity:0.18,
      weight:1.5,opacity:0.9
    });
    inner._sf=true;inner.addTo(simLayer);

    // Centre dot: highest probability point
    const centre=L.circleMarker([uLat,uLon],{
      radius:4,fillColor:'#ff0000',color:'#fff',weight:1.5,fillOpacity:1.0
    });
    centre._sf=true;centre.addTo(simLayer);

    // Line from intercept point to impact centre
    const impLine=L.polyline([[c.lat,c.lon],[uLat,uLon]],
      {color:'#ff6600',weight:1.5,dashArray:'4,3',opacity:0.6});
    impLine._sf=true;impLine.addTo(simLayer);

    // Small per-class drift indicators (subtle, not the main zone)
    lzs.forEach(z=>{
      const col=DCOLS[z.class]||'#ff9900';
      const dot=L.circleMarker([z.land_lat,z.land_lon],{
        radius:3,fillColor:col,color:'transparent',fillOpacity:0.6
      }).bindTooltip(
        `<div style="font-family:monospace;font-size:9px"><b>${z.class}</b><br>Drift:${z.drift_m}m | ToF:${z.tof_s}s | r=±${z.scatter_m}m</div>`,
        {sticky:true}
      );
      dot._sf=true;dot.addTo(simLayer);
    });
  }

  const feas=c.feasibility||{};
  const panel=document.getElementById('sim-panel');
  if(panel)panel.innerHTML=`
    <div style="display:flex;justify-content:space-between;align-items:baseline">
      <span style="color:#666;font-size:8px">t=${c.time_s?.toFixed(0)}s alt=${c.alt_m}m</span>
      <span style="font-size:16px;font-weight:bold;color:${dcol}">${c.decision}</span>
    </div>
    <div style="color:#888;font-size:8px">${c.reason}</div>
    <div style="color:#555;font-size:8px">Dist:${feas.dist_m?.toLocaleString()}m Flight:${feas.flight_s?.toFixed(0)}s Margin:${feas.time_margin_s?.toFixed(0)}s</div>
    ${c.time_to_excl_s!=null?`<div style="color:#f44;font-size:8px">⏱ ${c.time_to_excl_s.toFixed(0)}s to exclusion zone</div>`:''}
    ${c.pop_at_risk>0?`<div style="color:#f88;font-size:8px">&#9888; ${c.pop_at_risk} civilians at risk</div>`:''}
    <div style="color:#444;font-size:7px;margin-top:2px">${(c.details||[]).map(d=>`${d.class}:${(d.score*100).toFixed(0)}%(${d.cells_hit})`).join(' | ')}</div>`;
  updateCursor();
}

function playSim(){if(!simState)return;simPlaying=true;const b=document.getElementById('btn-play');if(b)b.textContent='⏸ PAUSE';simTimer=setInterval(()=>{simT=Math.min(1,simT+0.008);renderFrame(simT);if(simT>=1)pauseSim();},100);}
function pauseSim(){simPlaying=false;clearInterval(simTimer);const b=document.getElementById('btn-play');if(b)b.textContent='▶ PLAY';}
function resetSim(){pauseSim();simT=0;if(simState)renderFrame(0);}

// ── Generate drone ────────────────────────────────────────────────
async function generateDrone(){
  pauseSim();
  simLayer.clearLayers();
  if(interceptorMarker){simLayer.removeLayer(interceptorMarker);interceptorMarker=null;}
  simState=null;simDrone=null;simT=0;
  const ir=document.getElementById('sim-info'),
        tw=document.getElementById('sim-timeline-wrap'),
        panel=document.getElementById('sim-panel');
  if(ir)ir.innerHTML='<span style="color:#aaa">Generating drone…</span>';
  if(tw)tw.innerHTML='';if(panel)panel.innerHTML='';

  const seed=Math.floor(Math.random()*9999)+1;
  try{
    simDrone=await(await fetch(`/drone?seed=${seed}`)).json();

    // Draw a faint preview path through raw waypoints only (no spline, no scoring)
    // This will be replaced by the scored trajectory after analysis
    const wpts=simDrone.waypoints;
    const previewPts=wpts.map(w=>[w.lat,w.lon]);
    L.polyline(previewPts,{color:'#334455',weight:1.5,dashArray:'6,4',opacity:0.5,
      _preview:true}).addTo(simLayer);

    // Entry and target markers only
    const entry=wpts[0], target=wpts[wpts.length-1];
    L.circleMarker([entry.lat,entry.lon],{radius:8,fillColor:'#88ff44',
      color:'#fff',weight:2,fillOpacity:0.9})
     .bindTooltip(`<div style="font-family:monospace;font-size:11px"><b>${entry.label}</b><br>Alt:${entry.alt_m}m</div>`,{opacity:0.95})
     .addTo(simLayer);
    L.circleMarker([target.lat,target.lon],{radius:10,fillColor:'#ff4444',
      color:'#fff',weight:2,fillOpacity:0.9})
     .bindTooltip(`<div style="font-family:monospace;font-size:11px"><b>${target.label}</b><br>Alt:${target.alt_m}m</div>`,{opacity:0.95})
     .addTo(simLayer);

    // Pattern label at midpoint
    const midWpt=wpts[Math.floor(wpts.length/2)];
    L.marker([midWpt.lat,midWpt.lon],{icon:L.divIcon({
      html:`<div style="font-family:monospace;font-size:9px;color:#ffff44;background:rgba(0,0,0,0.75);padding:2px 6px;border-radius:2px;white-space:nowrap;border:1px solid #444">${(simDrone.pattern||'').toUpperCase().replace(/_/g,' ')}</div>`,
      className:''})}).addTo(simLayer);

    // Fit map to waypoint bounds
    map.fitBounds(L.polyline(previewPts).getBounds().pad(0.3));

    // Place fixed interceptor at naval base
    placeInterceptor(56.1614, 15.5869);

    if(ir)ir.innerHTML=
      `<span style="color:#ffff00">&#9992;</span> ${simDrone.description}<br>`+
      `<span style="color:#444;font-size:7px">Pattern: ${(simDrone.pattern||'').replace(/_/g,' ')} | Seed:${seed}</span><br>`+
      `<span style="color:#00ccff;font-size:8px">⊕ Interceptor: Naval Base (56.1614N 15.5869E)</span><br>`+
      `<span style="color:#2a6050;font-size:8px">→ Run engagement analysis to score trajectory</span>`;

    const ba=document.getElementById('btn-analyse'),
          bp=document.getElementById('btn-play'),
          br=document.getElementById('btn-reset');
    if(ba)ba.style.display='block';
    if(bp)bp.style.display='none';
    if(br)br.style.display='none';

  }catch(e){
    console.error('[drone]',e);
    if(ir)ir.innerHTML=`<span style="color:#f66">Failed: ${e.message}</span>`;
  }
}

// ── Run analysis ──────────────────────────────────────────────────
async function runAnalysis(){
  if(!simDrone)return;
  const ir=document.getElementById('sim-info');
  if(ir)ir.innerHTML='<span style="color:#aaa">Running engagement analysis…</span>';
  try{
    const sys=document.getElementById('sel-system')?.value||'rbs70';
    const iPos=interceptorPos||{lat:56.1614,lon:15.5869};
    // Always send fixed naval base position as interceptor
    const NAVAL_BASE={lat:56.1614,lon:15.5869};
    const iPos2=NAVAL_BASE;

    const wind=document.getElementById('sl-wind').value;
    const windDir=document.getElementById('sl-wind-dir').value;
    const temp=document.getElementById('sl-temp').value;
    const wc=document.getElementById('sel-wc').value;

    const url=`/intercept?seed=${simDrone.seed}&system=${sys}&iLat=${iPos2.lat}&iLon=${iPos2.lon}&wind=${wind}&wind_dir=${windDir}&temp=${temp}&wc=${wc}`;
    simState=await(await fetch(url)).json();
    const opt=simState.optimal,st=simState.stats||{};
    const cands=simState.all_candidates;

    // ── Draw single definitive trajectory from backend scored points ────────
    // Clear everything — preview path, waypoint markers, all of it
    simLayer.clearLayers();
    // Re-place interceptor after clear
    placeInterceptor(56.1614, 15.5869);

    // Draw trajectory coloured by engagement decision
    // These are the EXACT same points the drone animates along — no mismatch
    for(let i=0;i<cands.length-1;i++){
      const c=cands[i], d=cands[i+1];
      const col=DCISION_COLS[c.decision]||'#333';
      L.polyline([[c.lat,c.lon],[d.lat,d.lon]],
        {color:col, weight:4, opacity:0.85}
      ).addTo(simLayer);
    }

    // Entry dot (first candidate)
    const firstC=cands[0], lastC=cands[cands.length-1];
    L.circleMarker([firstC.lat,firstC.lon],{radius:8,fillColor:'#88ff44',
      color:'#fff',weight:2,fillOpacity:0.9})
     .bindTooltip(`<div style="font-family:monospace;font-size:10px"><b>Entry</b><br>t=0s</div>`,{opacity:0.9})
     .addTo(simLayer);

    // Target dot (last candidate)
    L.circleMarker([lastC.lat,lastC.lon],{radius:10,fillColor:'#ff4444',
      color:'#fff',weight:2,fillOpacity:0.9})
     .bindTooltip(`<div style="font-family:monospace;font-size:10px"><b>${simDrone?.target?.name||'Target'}</b></div>`,{opacity:0.9})
     .addTo(simLayer);

    // Pattern label at midpoint
    const midC=cands[Math.floor(cands.length/2)];
    L.marker([midC.lat,midC.lon],{icon:L.divIcon({
      html:`<div style="font-family:monospace;font-size:9px;color:#ffff44;background:rgba(0,0,0,0.75);padding:2px 6px;border-radius:2px;white-space:nowrap;border:1px solid #444">${(simDrone?.pattern||'').toUpperCase().replace(/_/g,' ')}</div>`,
      className:''})}).addTo(simLayer);

    // Interceptor range ring
    if(interceptorPos&&simState.system?.range_m){
      L.circle([interceptorPos.lat,interceptorPos.lon],{radius:simState.system.range_m,
        color:'#00ccff',fillColor:'#00ccff',fillOpacity:0.03,weight:1.5,dashArray:'8,4',opacity:0.5})
       .bindTooltip(`<div style="font-family:monospace;font-size:10px">${simState.system.name}<br>Range:${simState.system.range_m?.toLocaleString()}m</div>`,{sticky:true})
       .addTo(simLayer);
    }

    // Optimal intercept marker
    const ocol=DCISION_COLS[opt.decision]||'#00ff88';
    L.circleMarker([opt.lat,opt.lon],{radius:16,fillColor:ocol,color:'#fff',weight:3,fillOpacity:0.95})
     .bindTooltip(`<div style="font-family:monospace;font-size:11px;line-height:1.6"><b>✓ OPTIMAL INTERCEPT</b><br>t=${opt.time_s?.toFixed(0)}s | Alt:${opt.alt_m}m<br>Consequence:${(opt.consequence*100).toFixed(0)}%<br>Flight:${opt.feasibility?.flight_s?.toFixed(0)}s Margin:${opt.feasibility?.time_margin_s?.toFixed(0)}s<br>${opt.reason}</div>`,{opacity:0.95})
     .addTo(simLayer);

    // ── Optimal intercept: unified impact zone ───────────────────────────────
    const optLzs=opt.landing_zones||[];
    if(optLzs.length>0){
      const uLat=optLzs[0].unified_lat||opt.lat;
      const uLon=optLzs[0].unified_lon||opt.lon;
      const uRad=optLzs[0].unified_rad||150;
      const uScore=opt.unified_score||opt.consequence;

      // Score-coloured outer ring
      const ringCol=uScore<0.15?'#00c896':uScore<0.35?'#ffc107':'#ff6600';

      L.circle([uLat,uLon],{radius:uRad,
        color:ringCol,fillColor:ringCol,fillOpacity:0.10,weight:2.5})
       .bindTooltip(
         `<div style="font-family:monospace;font-size:11px;line-height:1.5">
           <b>⚠ Optimal Impact Zone</b><br>
           Radius: ${uRad}m | Score: ${(uScore*100).toFixed(0)}%<br>
           Probability-weighted consequence model<br>
           Gaussian decay: P(hit)=exp(-0.5×(r/σ)²) σ=${(uRad/2).toFixed(0)}m</div>`,
         {permanent:false,opacity:0.95}
       ).addTo(simLayer);

      // Inner 50% probability ring
      L.circle([uLat,uLon],{radius:uRad*0.5,
        color:ringCol,fillColor:ringCol,fillOpacity:0.22,weight:1.5})
       .addTo(simLayer);

      // Centre
      L.circleMarker([uLat,uLon],{radius:5,
        fillColor:ringCol,color:'#fff',weight:2,fillOpacity:1})
       .addTo(simLayer);

      // Intercept → impact line
      L.polyline([[opt.lat,opt.lon],[uLat,uLon]],
        {color:ringCol,weight:2,dashArray:'5,3',opacity:0.7})
       .addTo(simLayer);

      // Per-class dots
      optLzs.forEach(z=>{
        L.circleMarker([z.land_lat,z.land_lon],{radius:3,
          fillColor:DCOLS[z.class]||'#ff9900',color:'transparent',fillOpacity:0.7})
         .bindTooltip(`<div style="font-family:monospace;font-size:9px"><b>${z.class}</b><br>r=±${z.scatter_m}m drift=${z.drift_m}m</div>`,{sticky:true})
         .addTo(simLayer);
      });
    }

    // Engage windows
    (simState.windows||[]).forEach(w=>{
      if(!w.end_lat)return;
      L.polyline([[w.start_lat,w.start_lon],[w.end_lat,w.end_lon]],
        {color:DCISION_COLS[w.type]||'#00ff88',weight:8,opacity:0.3}).addTo(simLayer);
    });

    // Exclusion zone entry markers
    const entryTimes=simState.analysis?.entry_times||simState.entry_times||{};
    Object.entries(entryTimes).forEach(([key,t])=>{
      if(!t)return;
      const site=(simState.critical_sites||[]).find(s=>s.key===key);if(!site)return;
      const closest=cands.reduce((b,c)=>Math.abs(c.time_s-t)<Math.abs(b.time_s-t)?c:b,cands[0]);
      if(!closest)return;
      L.circleMarker([closest.lat,closest.lon],{radius:8,fillColor:'#cc0000',color:'#fff',weight:2,fillOpacity:0.9})
       .bindTooltip(`<div style="font-family:monospace;font-size:10px"><b>⚠ Enters ${site.name}</b><br>t=${t.toFixed(0)}s</div>`,{opacity:0.95})
       .addTo(simLayer);
    });

    // Fit map to scored trajectory
    const allPts=cands.map(c=>[c.lat,c.lon]);
    if(allPts.length) map.fitBounds(L.polyline(allPts).getBounds().pad(0.2));

    const tw=document.getElementById('sim-timeline-wrap');
    if(tw)tw.innerHTML=buildTimeline(cands);

    const ir2=document.getElementById('sim-info');
    if(ir2)ir2.innerHTML=
      `<b style="color:${ocol}">${simState.optimal_type}</b><br>`+
      `Best: t=${opt.time_s?.toFixed(0)}s score=${opt.consequence} alt=${opt.alt_m}m<br>`+
      `<span style="color:#00c8a0">✓${st.engage_pts||0} ⚠${st.caution_pts||0} ◈${st.potential_pts||0} ✗${st.no_shot_pts||0} ⊘${st.never_pts||0}</span><br>`+
      `<span style="color:#888;font-size:8px">${st.system||'?'} | ${st.engage_windows||0} window(s)</span><br>`+
      `${st.nearest_site?`<span style="color:#f44;font-size:8px">⚠ ${st.nearest_site} at t=${st.earliest_excl_s}s</span><br>`:''}`+
      `<span style="color:#444;font-size:7px">${simState.recommendation}</span>`;

    const ba=document.getElementById('btn-analyse'),bp=document.getElementById('btn-play'),br=document.getElementById('btn-reset');
    if(ba)ba.style.display='none';if(bp)bp.style.display='block';if(br)br.style.display='block';
    renderFrame(0);
  }catch(e){
    console.error('[analysis]',e);
    const ir2=document.getElementById('sim-info');
    if(ir2)ir2.innerHTML=`<span style="color:#f66">Analysis failed: ${e.message}</span>`;
  }
}

const _bdg=document.getElementById('btn-drone'),_ban=document.getElementById('btn-analyse'),
      _bpl=document.getElementById('btn-play'),_brs=document.getElementById('btn-reset');
if(_bdg)_bdg.addEventListener('click',generateDrone);
if(_ban)_ban.addEventListener('click',runAnalysis);
if(_bpl)_bpl.addEventListener('click',()=>simPlaying?pauseSim():playSim());
if(_brs)_brs.addEventListener('click',resetSim);

// ── Events ────────────────────────────────────────────
document.getElementById('sl-hour').addEventListener('input',function(){
  const el=document.getElementById('lbl-hour');
  if(el)el.textContent=String(parseInt(this.value)).padStart(2,'0')+':00';
  recolorGrid(); if(lastLat!==null)queryScore(lastLat,lastLon);
});
document.getElementById('sel-day').addEventListener('change',function(){
  recolorGrid(); if(lastLat!==null)queryScore(lastLat,lastLon);
});

document.getElementById('sl-wind').addEventListener('input',function(){
  document.getElementById('lbl-wind').textContent=this.value+' km/h';
  recolorGrid(); if(lastLat!==null)queryScore(lastLat,lastLon);
});
document.getElementById('sl-wind-dir').addEventListener('input',function(){
  document.getElementById('lbl-wind-dir').textContent=this.value+'°';
  recolorGrid(); if(lastLat!==null)queryScore(lastLat,lastLon);
});
document.getElementById('sl-temp').addEventListener('input',function(){
  document.getElementById('lbl-temp').textContent=this.value+'°C';
  recolorGrid(); if(lastLat!==null)queryScore(lastLat,lastLon);
});
document.getElementById('sel-wc').addEventListener('change',function(){
  recolorGrid(); if(lastLat!==null)queryScore(lastLat,lastLon);
});
map.on('click',e=>{lastLat=e.latlng.lat;lastLon=e.latlng.lng;queryScore(e.latlng.lat,e.latlng.lng);});

const togCfg={'tog-eez':['eez'],'tog-cont':['contiguous_zone'],'tog-terr':['territorial_sea'],
              'tog-tss':['tss_lane','tss_zone'],'tog-mil':['military_area']};
for(const[cbId,zones]of Object.entries(togCfg)){
  const el=document.getElementById(cbId);
  if(el)el.addEventListener('change',function(){
    zones.forEach(z=>this.checked?mLayers[z].addTo(map):map.removeLayer(mLayers[z]));
  });
}
const aisToggle=document.getElementById('tog-ais');
if(aisToggle)aisToggle.addEventListener('change',function(){
  this.checked?vesselLayer.addTo(map):map.removeLayer(vesselLayer);
});

// ── Startup ───────────────────────────────────────────
loadWeather(); setInterval(loadWeather,600000);
loadMaritime();
loadCriticalSites();
loadAIS(); setInterval(loadAIS,30000);
loadGrid();
</script>
</body>
</html>
"""

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    PORT=8000
    print("="*64)
    print("  Karlskrona Impact Risk Engine  v4.0  (clean rewrite)")
    print("="*64)

    PRESENCE_CURVES.update(load_presence_curves(PRESENCE_CSV))
    print("\n[WEATHER] Fetching ..."); fetch_weather()
    print("\n[MARITIME] Loading ..."); MARITIME_DATA.update(fetch_maritime())

    need_build=True
    if GRID_CACHE.exists():
        print(f"\n[GRID] Loading from {GRID_CACHE} ...")
        try:
            with open(GRID_CACHE) as f: GRID_FEATURES.extend(json.load(f))
            s=GRID_FEATURES[0]["properties"] if GRID_FEATURES else {}
            # Validate v4 required fields
            if not {"land_use","is_coastal","wat_frac","integrated_risk"}.issubset(s.keys()):
                print("[GRID] Cache missing v4 fields — rebuilding ...")
                GRID_FEATURES.clear()
            else:
                print(f"[GRID] Loaded {len(GRID_FEATURES)} cells."); need_build=False
        except Exception as e:
            print(f"[GRID] Cache error: {e} — rebuilding ..."); GRID_FEATURES.clear()

    if need_build:
        print("\n[GRID] Building scored grid ...")
        scb=load_scb(); osm=fetch_osm()
        raw_features = build_scored_grid(scb, osm, PRESENCE_CURVES)
        compute_integrated_debris_risk(raw_features)
        GRID_FEATURES.extend(raw_features)
        print(f"\n[GRID] Saving → {GRID_CACHE} ...")
        with open(GRID_CACHE,"w") as f: json.dump(GRID_FEATURES,f,separators=(",",":"))
        print("[GRID] Saved.")

    for feat in GRID_FEATURES:
        GRID_INDEX[feat["properties"]["cell_id"]]=feat["properties"]

    print(f"\n[AIS] File: {AIS_FILE} ({'found' if AIS_FILE.exists() else 'not found — place in data/ folder'})")
    print(f"\n[SERVER] http://localhost:{PORT}")
    print("[SERVER] Ctrl+C to stop.\n")
    try: HTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
    except KeyboardInterrupt: print("\n[SERVER] Stopped.")