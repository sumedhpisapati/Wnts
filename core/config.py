from core.core_imports import *

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

# From maritime
MS={
    "territorial_sea":{"color":"#00bcd4","weight":2,"dashArray":"8,4","fillOpacity":0.06,"label":"Territorial Sea (12nm)"},
    "contiguous_zone":{"color":"#0077be","weight":2,"dashArray":"12,4","fillOpacity":0.04,"label":"Contiguous Zone (24nm)"},
    "eez":            {"color":"#003f87","weight":2,"dashArray":"16,6","fillOpacity":0.03,"label":"EEZ (200nm)"},
    "tss_lane":       {"color":"#cc00cc","weight":1.5,"dashArray":"4,2","fillOpacity":0.12,"label":"Traffic Sep. Lane"},
    "tss_zone":       {"color":"#ff66ff","weight":1,"dashArray":"4,2","fillOpacity":0.15,"label":"Traffic Sep. Zone"},
    "military_area":  {"color":"#cc0000","weight":2,"dashArray":"6,3","fillOpacity":0.10,"label":"Military Restricted"},
}

# From physics
AIR_DENSITY = 1.225   # kg/m³, ISA standard sea level
DEBRIS_CLASSES = [
    {"name":"Heavy debris",  "mass_kg":2.5, "Cd_v":0.80, "Cd_h":0.30, "area_m2":0.04, "weight":0.50},
    {"name":"Medium debris", "mass_kg":0.8, "Cd_v":1.20, "Cd_h":0.80, "area_m2":0.02, "weight":0.35},
    {"name":"Fine/fuel",     "mass_kg":0.1, "Cd_v":2.00, "Cd_h":1.50, "area_m2":0.005,"weight":0.15},
]

# From critical
CRITICAL_SITES = [
    {"key":"naval_base",  "name":"Naval Base Karlskrona",   "lat":56.1614,"lon":15.5869, "radius_m":3000, "type":"military",    "color":"#cc0000"},
    {"key":"kungsholm",   "name":"Kungsholms Fort",          "lat":56.1050,"lon":15.5906, "radius_m":2000, "type":"military",    "color":"#cc0000"},
    {"key":"port",        "name":"Dragsö Industrial Port",   "lat":56.1750,"lon":15.6200, "radius_m":2000, "type":"industrial",  "color":"#ff6600"},
    {"key":"ferry",       "name":"Ferry Terminal",           "lat":56.1607,"lon":15.5950, "radius_m":1500, "type":"civilian",    "color":"#ff9900"},
    {"key":"power",       "name":"Power Substation Lyckeby", "lat":56.1820,"lon":15.5600, "radius_m":1000, "type":"industrial",  "color":"#ff6600"},
    {"key":"stumholmen",  "name":"Stumholmen Naval Museum",  "lat":56.1590,"lon":15.5920, "radius_m":1000, "type":"military",    "color":"#cc0000"},
    {"key":"radar",       "name":"Aspö Island Radar",        "lat":56.0700,"lon":15.7100, "radius_m":2500, "type":"military",    "color":"#cc0000"},
]

INTERCEPTOR_SYSTEMS = {
    "rbs70": {
        "name":      "RBS 70 Mk2 (Karlskrona Naval)",
        "speed_ms":  680,    "range_m":   9000,   "min_range_m": 200,  "reaction_s":  6,
        "notes":     "SHORAD, laser-beam riding, stationed at Karlskrona",
    },
    "iris_t": {"name":"IRIS-T SLM", "speed_ms":1020, "range_m":40000, "min_range_m":500, "reaction_s":8, "notes":"Medium-range SAM"},
    "stinger": {"name":"Stinger MANPADS", "speed_ms":750, "range_m":4800, "min_range_m":200, "reaction_s":10, "notes":"Man-portable, short range"},
    "kreuger100": {"name":"Kreuger 100 Interceptor", "speed_ms":75, "range_m":2000, "min_range_m":50, "reaction_s":4, "notes":"Battery-powered counter-UAS drone"},
    "kreuger100xr": {"name":"Kreuger 100XR (Extended Range)", "speed_ms":85, "range_m":3500, "min_range_m":50, "reaction_s":4, "notes":"Extended range military variant"},
    "gripen": {"name":"JAS 39 Gripen (F17 Ronneby)", "speed_ms":750, "range_m":150000, "min_range_m":1000, "reaction_s":45, "base":{"lat": 56.2667, "lon": 15.2667, "name": "F17 Ronneby Airbase"}, "notes":"Scrambled from F17 Airbase"},
    "custom": {"name":"Custom system", "speed_ms":500, "range_m":8000, "min_range_m":200, "reaction_s":5, "notes":"User-defined parameters"},
}

DEFAULT_INTERCEPTOR = {"lat": 56.1614, "lon": 15.5869}
