from core_imports import *
from config import *

def haversine_m(lat1,lon1,lat2,lon2):
    R=6_371_000; f1,f2=math.radians(lat1),math.radians(lat2)
    a=math.sin(math.radians(lat2-lat1)/2)**2+math.cos(f1)*math.cos(f2)*math.sin(math.radians(lon2-lon1)/2)**2
    return R*2*math.asin(math.sqrt(max(0.0,a)))

def gauss(d,s): return math.exp(-0.5*(d/s)**2)

def h3_shapely(cid):
    return Polygon([(lon,lat) for lat,lon in h3.cell_to_boundary(cid)])

def h3_ring(cid):
    r=[[lon,lat] for lat,lon in h3.cell_to_boundary(cid)]; r.append(r[0]); return r
