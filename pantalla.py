#!/usr/bin/env python3
"""Pantalla de próximos pasos: AMB (GTFS-RT) + TMB (iBus).

Uso:
    python3 pantalla.py --app-id TU_APP_ID --app-key TU_APP_KEY
    (también vale TMB_APP_ID / TMB_APP_KEY como variables de entorno)
    Abre http://localhost:8080 (cambia el puerto con --puerto)
Dependencias: pip install gtfs-realtime-bindings requests truststore
"""
import io, csv, os, time, json, zipfile, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import truststore; truststore.inject_into_ssl()
import requests
from google.transit import gtfs_realtime_pb2

# ================= CONFIGURACIÓN =================
PARADAS = [
    # tipo "amb": codigo = stop_id del GTFS del AMB
    # tipo "tmb": codigo = codi_parada de TMB
    # El nombre de la parada siempre sale del GTFS del AMB (stops.txt)
    {"tipo": "amb", "codigo": "001178"},
    {"tipo": "tmb", "codigo": "003508"},
    {"tipo": "amb", "codigo": "000965"},
]
SALIDAS_POR_PARADA = 3
REFRESCO_S = 30
PUERTO = 8080
LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png")
RT_URL   = "https://www.ambmobilitat.cat/transit/trips-updates/trips.bin"
GTFS_URL = "https://www.ambmobilitat.cat/OpenData/google_transit.zip"
TMB_URL  = "https://api.tmb.cat/v1/itransit/bus/parades/{}"
CACHE_GTFS = "gtfs_amb_cache.zip"
import argparse
_ap = argparse.ArgumentParser(description="Pantalla de próximos pasos AMB + TMB")
_ap.add_argument("--app-id", default=os.environ.get("TMB_APP_ID"), help="APP ID de TMB")
_ap.add_argument("--app-key", default=os.environ.get("TMB_APP_KEY"), help="APP KEY de TMB")
_ap.add_argument("--puerto", type=int, default=PUERTO)
_args = _ap.parse_args()
APP_ID, APP_KEY, PUERTO = _args.app_id, _args.app_key, _args.puerto
# =================================================

estado = {"paradas": [], "generado": 0, "error": None}


def cargar_estatico():
    """GTFS estático del AMB, cacheado en disco 24 h. Devuelve (paradas, viajes)."""
    vieja = not os.path.exists(CACHE_GTFS) or time.time() - os.path.getmtime(CACHE_GTFS) > 86400
    if vieja:
        try:
            r = requests.get(GTFS_URL, timeout=90); r.raise_for_status()
            open(CACHE_GTFS, "wb").write(r.content)
        except Exception as e:
            print("Aviso: no se pudo descargar el GTFS estático:", e)
    if not os.path.exists(CACHE_GTFS):
        return {}, {}
    z = zipfile.ZipFile(CACHE_GTFS)
    leer = lambda n: csv.DictReader(io.TextIOWrapper(z.open(n), "utf-8-sig"))
    nombres = {s["stop_id"]: s["stop_name"] for s in leer("stops.txt")}
    rutas = {r["route_id"]: (r.get("route_short_name") or r["route_id"], r.get("route_long_name", ""))
             for r in leer("routes.txt")}
    viajes = {}
    for t in leer("trips.txt"):
        corto, largo = rutas.get(t["route_id"], ("?", ""))
        viajes[t["trip_id"]] = (corto, t.get("trip_headsign") or largo)
    return nombres, viajes


def leer_amb(viajes, ids):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(requests.get(RT_URL, timeout=10).content)
    out = {i: [] for i in ids}
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        tu = e.trip_update
        linea, destino = viajes.get(tu.trip.trip_id, (tu.trip.route_id or "?", ""))
        for s in tu.stop_time_update:
            if s.stop_id in out and s.arrival.time:
                out[s.stop_id].append((s.arrival.time, linea, destino))
    return out


def msg_error(e):
    """Texto de error sin URL (la URL de TMB lleva las claves)."""
    if isinstance(e, requests.HTTPError) and e.response is not None:
        return f"HTTP {e.response.status_code}"
    return type(e).__name__


def leer_tmb(codigo):
    if not (APP_ID and APP_KEY):
        raise RuntimeError("faltan --app-id / --app-key")
    codigo = codigo.lstrip("0") or codigo      # "003508" -> "3508"
    r = requests.get(TMB_URL.format(codigo), params={"app_id": APP_ID, "app_key": APP_KEY}, timeout=10)
    r.raise_for_status()
    data = r.json()
    nombre, sal = f"Parada {codigo}", []
    for p in data.get("parades", []):
        nombre = p.get("nom_parada", nombre)
        for l in p.get("linies_trajectes", []):
            for b in l.get("propers_busos", []):
                t = b["temps_arribada"]
                sal.append((t / 1000 if t > 1e11 else t, l["nom_linia"], l["desti_trajecte"]))
    return nombre, sal


def refrescar(nombres, viajes):
    ahora = time.time()
    ids_amb = [p["codigo"] for p in PARADAS if p["tipo"] == "amb"]
    amb, err = {}, None
    try:
        amb = leer_amb(viajes, ids_amb) if ids_amb else {}
    except Exception as e:
        err = f"AMB: {msg_error(e)}"
    res = []
    for p in PARADAS:
        nombre, sal = nombres.get(p["codigo"]), []   # maestro: AMB
        try:
            if p["tipo"] == "amb":
                sal = amb.get(p["codigo"], [])
            else:
                nombre_tmb, sal = leer_tmb(p["codigo"])
                nombre = nombre or nombre_tmb         # TMB solo si el AMB no lo tiene
        except Exception as e:
            err = f"{p['tipo'].upper()} {p['codigo']}: {msg_error(e)}"
        nombre = nombre or f"Parada {p['codigo']}"
        sal = sorted(x for x in sal if x[0] >= ahora - 30)[:SALIDAS_POR_PARADA]
        res.append({"nombre": nombre, "fuente": p["tipo"].upper(), "codigo": p["codigo"], "salidas": [
            {"linea": l, "destino": d, "min": max(0, round((t - ahora) / 60))} for t, l, d in sal]})
    return res, err


def bucle():
    nombres, viajes = cargar_estatico()
    ultima_carga = time.time()
    while True:
        try:
            if time.time() - ultima_carga > 86400:
                nombres, viajes = cargar_estatico(); ultima_carga = time.time()
            res, err = refrescar(nombres, viajes)
            estado.update(paradas=res, error=err)
            if not err:
                estado["generado"] = time.time()
        except Exception as e:
            estado["error"] = msg_error(e)
        time.sleep(REFRESCO_S)


PAGINA = r"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>App Busos Pont del Treball</title>
<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@500;700;800&family=Barlow+Condensed:wght@600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:#161617;color:#fff;font-family:'Barlow',sans-serif;display:flex;flex-direction:column;overflow:hidden}
header{height:13vh;min-height:64px;background:#DC241F;display:flex;align-items:center;justify-content:space-between;padding:0 3.5vw}
.logo{height:7vh;display:block}
.tit{font:600 4vh 'Barlow Condensed';letter-spacing:.06em;margin-left:2.4vh}
.hora{font:700 6.5vh 'Barlow Condensed'}
main{flex:1;display:flex;gap:2vw;padding:4vh 3.5vw 3vh;min-height:0}
.c{flex:1;background:#262628;border-radius:1.8vh;display:flex;flex-direction:column;overflow:hidden}
.ch{padding:2.4vh 2vw 2vh;border-bottom:.5vh solid #DC241F}
.h{font:700 4.6vh/1.05 'Barlow Condensed';text-transform:uppercase}
.s{font:500 2.1vh 'Barlow';color:#A9A9AE;margin-top:.6vh}
.r{display:flex;align-items:center;gap:1.2vw;padding:2.6vh 2vw;border-bottom:1px solid #3A3A3D}
.l{font:800 3.6vh 'Barlow';min-width:8vh;text-align:center;padding:.7vh 0;border-radius:1vh}
.d{flex:1;font:500 2.7vh 'Barlow';color:#E6E6E8;min-width:0}
.m{font:800 9vh/1 'Barlow Condensed'}.m.ahora{font-size:5.6vh}.m small{font:600 2.6vh 'Barlow';color:#A9A9AE;margin-left:.5vh}
.v{padding:4vh 2vw;font:500 3vh/1.3 'Barlow';color:#A9A9AE}
.av{position:fixed;bottom:.8vh;right:2vw;font:500 1.8vh 'Barlow';color:#FFB000}
</style></head><body>
<header><div style="display:flex;align-items:center"><img class="logo" src="/logo.png" alt="TMB"><div class="tit"></div></div><div class="hora" id="hora"></div></header>
<main id="main"></main><div class="av" id="av"></div>
<script>
const COL=[[/^H/,'#0072BC'],[/^V/,'#6BA32A'],[/^D/,'#8E3B98'],[/^N/,'#1C3F94'],[/^B/,'#C77700'],[/^\d/,'#DC241F']];
const col=l=>(COL.find(c=>c[0].test(l))||[0,'#6B6B70'])[1];
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
function pinta(d){const m=document.getElementById('main');m.replaceChildren();
 d.paradas.forEach(p=>{const c=el('div','c'),h=el('div','ch');
  h.append(el('div','h',p.nombre),el('div','s','Código de parada: '+p.codigo));c.append(h);
  if(!p.salidas.length)c.append(el('div','v','Sin servicio ahora mismo'));
  p.salidas.forEach(s=>{const r=el('div','r'),l=el('span','l',s.linea);l.style.background=col(s.linea);
   const t=el('span',s.min<1?'m ahora':'m',s.min<1?'ahora':s.min);if(s.min>=1)t.append(el('small','','min'));
   r.append(l,el('span','d',s.destino),t);c.append(r)});m.append(c)});
 const edad=d.generado?Math.round(Date.now()/1000-d.generado):null;
 document.getElementById('av').textContent=d.error?('Datos con incidencias: '+d.error):(edad!==null&&edad>120?'Datos de hace '+Math.round(edad/60)+' min':'')}
async function carga(){try{pinta(await (await fetch('/data')).json())}catch(e){document.getElementById('av').textContent='Sin conexión con el servidor'}}
const reloj=()=>document.getElementById('hora').textContent=new Date().toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit'});
reloj();setInterval(reloj,10000);carga();setInterval(carga,15000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data"):
            cuerpo, tipo = json.dumps(estado, ensure_ascii=False).encode(), "application/json"
        elif self.path == "/logo.png":
            if not os.path.exists(LOGO):
                self.send_error(404)
                return
            cuerpo, tipo = open(LOGO, "rb").read(), "image/png"
        else:
            cuerpo, tipo = PAGINA.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    if not (APP_ID and APP_KEY) and any(p["tipo"] == "tmb" for p in PARADAS):
        print("Aviso: faltan --app-id / --app-key; las paradas de TMB darán error.")
    threading.Thread(target=bucle, daemon=True).start()
    print(f"Pantalla en http://localhost:{PUERTO}  (Ctrl+C para salir)")
    ThreadingHTTPServer(("0.0.0.0", PUERTO), H).serve_forever()