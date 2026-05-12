FRONTEND_HTML = r'''FRONTEND_HTML = r"""<!DOCTYPE html>
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
  <button id="btn-predict" style="width:100%;padding:7px;margin-top:4px;background:#1a1a2a;color:#8888ff;border:1px solid #222266;border-radius:3px;font-family:monospace;font-size:9px;letter-spacing:1px;cursor:pointer;text-transform:uppercase">&#128302; Predict Paths</button>
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

  if(simPlaying && !intercepted) {
     liveTrackUpdate(c.lat, c.lon, droneHeading);
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
      _bpl=document.getElementById('btn-play'),_brs=document.getElementById('btn-reset'),
      _bpr=document.getElementById('btn-predict');
if(_bdg)_bdg.addEventListener('click',generateDrone);
if(_ban)_ban.addEventListener('click',runAnalysis);
if(_bpl)_bpl.addEventListener('click',()=>simPlaying?pauseSim():playSim());
if(_brs)_brs.addEventListener('click',resetSim);
if(_bpr)_bpr.addEventListener('click',runPrediction);

// ── Run Prediction ──────────────────────────────────────────────
let predictLayers = L.layerGroup().addTo(map);


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

async function runPrediction(){
  if(!simDrone) { alert("Generate a drone first."); return; }
  const ir=document.getElementById('sim-info');
  ir.innerHTML = '<span style="color:#aaa">Calculating probabilistic routes…</span>';
  
  const start = simDrone.waypoints[0];
  // Calculate current heading from first two points
  const p1 = simDrone.waypoints[0], p2 = simDrone.waypoints[1];
  const heading = Math.atan2(p2.lon-p1.lon, p2.lat-p1.lat)*180/Math.PI;

  try {
    const res = await fetch(`/predict?lat=${start.lat}&lon=${start.lon}&heading=${heading}&k=3`);
    const data = await res.json();
    
    predictLayers.clearLayers();
    
    // 1. Draw uncertainty corridors (Monte Carlo samples)
    data.uncertainty_corridor.forEach(route => {
      L.polyline(route.map(w=>[w.lat, w.lon]), {
        color: '#4444ff', weight: 1, opacity: 0.2, dashArray: '2,2'
      }).addTo(predictLayers);
    });

    // 2. Draw Multi-Hypothesis branching paths
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
        
      // Draw end marker for each hypothesis
      const last = h.waypoints[h.waypoints.length-1];
      L.circleMarker([last.lat, last.lon], {
        radius: 6, fillColor: col, color: '#fff', weight: 1, fillOpacity: 0.9
      }).addTo(predictLayers);
    });

    ir.innerHTML = `<b style="color:#8888ff">PROBABILISTIC FORECAST COMPLETE</b><br>` +
      `<span style="color:#aaa;font-size:8px">Inferred Top Target: ${data.hypotheses[0].target_name} (${(data.hypotheses[0].confidence*100).toFixed(1)}%)</span><br>` +
      `<span style="color:#444;font-size:7px">Multi-hypothesis branching (k=3) + Monte Carlo corridor enabled</span>`;

  } catch(e) {
    console.error('[predict]', e);
    ir.innerHTML = `<span style="color:#f66">Prediction failed: ${e.message}</span>`;
  }
}

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
"""'''
