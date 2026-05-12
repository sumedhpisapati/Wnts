from core.core_imports import *
from core.config import *
from core import state
from core.utils import *
from data_processing.data import *

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
