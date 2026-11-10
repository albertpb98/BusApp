import io, csv, os, time, zipfile, json
import truststore; truststore.inject_into_ssl()
import requests
from google.transit import gtfs_realtime_pb2

# ---------- CONFIGURACIÓN ----------
PARADAS = [
    # tipo: "amb" (stop_id del GTFS del AMB) o "tmb" (codi_parada de TMB)
    {"nombre": "Parada 1", "tipo": "amb", "codigo": "000965"},
    {"nombre": "Parada 2", "tipo": "tmb", "codigo": "003508"},
    {"nombre": "Parada 3", "tipo": "tmb", "codigo": "001178"},
]
RT_URL   = "https://www.ambmobilitat.cat/transit/trips-updates/trips.bin"
GTFS_URL = "https://www.ambmobilitat.cat/OpenData/google_transit.zip"
TMB_URL  = "https://api.tmb.cat/v1/itransit/bus/parades/{}"
APP_ID, APP_KEY = os.environ.get("TMB_APP_ID"), os.environ.get("TMB_APP_KEY")
# -----------------------------------

def cargar_estatico():
    z = zipfile.ZipFile(io.BytesIO(requests.get(GTFS_URL, timeout=60).content))
    leer = lambda n: list(csv.DictReader(io.TextIOWrapper(z.open(n), "utf-8-sig")))
    routes, trips = leer("routes.txt"), leer("trips.txt")
    linea = {r["route_id"]: r.get("route_short_name", r["route_id"]) for r in routes}
    return {t["trip_id"]: (linea.get(t["route_id"], "?"), t.get("trip_headsign", "")) for t in trips}

def leer_amb(viajes, codigos):
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(requests.get(RT_URL, timeout=10).content)
    out = {c: [] for c in codigos}
    for e in feed.entity:
        if not e.HasField("trip_update"):
            continue
        t = e.trip_update
        ln, dest = viajes.get(t.trip.trip_id, ("?", ""))
        for s in t.stop_time_update:
            if s.stop_id in out and s.arrival.time:
                out[s.stop_id].append({"linea": ln, "destino": dest, "t": s.arrival.time})
    return out

def leer_tmb(codigo):
    r = requests.get(TMB_URL.format(codigo), params={"app_id": APP_ID, "app_key": APP_KEY}, timeout=10)
    r.raise_for_status()
    out = []
    for p in r.json().get("parades", []):
        for l in p.get("linies_trajectes", []):
            for b in l.get("propers_busos", []):
                t = b["temps_arribada"]
                out.append({"linea": l["nom_linia"], "destino": l["desti_trajecte"],
                            "t": t / 1000 if t > 1e11 else t})
    return out

# def leer_metro(...): pendiente, falta confirmar el endpoint de tiempo real

def todo():
    ahora = time.time()
    viajes = cargar_estatico() if any(p["tipo"] == "amb" for p in PARADAS) else {}
    amb = leer_amb(viajes, [p["codigo"] for p in PARADAS if p["tipo"] == "amb"])
    res = []
    for p in PARADAS:
        sal = amb[p["codigo"]] if p["tipo"] == "amb" else leer_tmb(p["codigo"])
        sal = sorted(sal, key=lambda x: x["t"])[:4]
        res.append({"parada": p["nombre"], "tipo": p["tipo"], "salidas": [
            {"linea": s["linea"], "destino": s["destino"], "min": max(0, round((s["t"] - ahora) / 60))}
            for s in sal]})
    return res

if __name__ == "__main__":
    print(json.dumps(todo(), ensure_ascii=False, indent=2))