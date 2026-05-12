from core.core_imports import *
from core.config import *
from core import state
from core.utils import *

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

def fetch_weather():
    from core import state
    if state._wx_cache and time.time()-state._wx_ts < 600: return state._wx_cache
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
        state._wx_cache=w; state._wx_ts=time.time()
        print(f"[WEATHER] {w['condition']} {w['temperature_c']}°C wind={wind}km/h factor={of:.2f}")
        return w
    except Exception as e:
        print(f"[WEATHER] Failed: {e}")
        fb={"ok":False,"temperature_c":15,"rain_mm":0,"snowfall_cm":0,"windspeed_kmh":0,
            "weathercode":0,"is_day":1,"condition":"Unknown","fetched_at":"N/A","outdoor_factor":1.0}
        state._wx_cache=fb; state._wx_ts=time.time(); return fb

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

def load_ais():
    if not AIS_FILE.exists(): return []
    try:
        ships=[]
        with open(AIS_FILE,newline="",encoding="utf-8") as f:
            for row in csv.DictReader(f): ships.append(row)
        print(f"[AIS] {len(ships)} vessels from {AIS_FILE}"); return ships
    except Exception as e: print(f"[AIS] {e}"); return []
