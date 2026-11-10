import sys, io, csv, time, zipfile
import truststore; truststore.inject_into_ssl()
import requests
from google.transit import gtfs_realtime_pb2

RT_URL   = "https://www.ambmobilitat.cat/transit/trips-updates/trips.bin"
GTFS_URL = "https://www.ambmobilitat.cat/OpenData/google_transit.zip"
MIS_PARADAS = ["00178", "003508", "000965"]   # aquí van tus stop_id, p. ej. ["001407", "001410"]

def cargar_estatico():
    z = zipfile.ZipFile(io.BytesIO(requests.get(GTFS_URL, timeout=60).content))
    def leer(n):
        return list(csv.DictReader(io.TextIOWrapper(z.open(n), "utf-8-sig")))
    return leer("stops.txt"), leer("trips.txt"), leer("routes.txt")

stops, trips, routes = cargar_estatico()

if len(sys.argv) > 2 and sys.argv[1] == "buscar":
    q = " ".join(sys.argv[2:]).lower()
    for s in stops:
        if q in s["stop_name"].lower():
            print(s["stop_id"], "|", s["stop_name"], "|", s["stop_lat"], s["stop_lon"])
    sys.exit()

linea  = {r["route_id"]: r.get("route_short_name", r["route_id"]) for r in routes}
viaje  = {t["trip_id"]: (t["route_id"], t.get("trip_headsign", "")) for t in trips}
nombre = {s["stop_id"]: s["stop_name"] for s in stops}

feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(requests.get(RT_URL, timeout=10).content)
ahora = time.time()

salidas = []
for e in feed.entity:
    if not e.HasField("trip_update"):
        continue
    t = e.trip_update
    rid, destino = viaje.get(t.trip.trip_id, (t.trip.route_id, ""))
    for s in t.stop_time_update:
        if s.stop_id in MIS_PARADAS and s.arrival.time:
            salidas.append((s.arrival.time, s.stop_id, linea.get(rid, rid or "?"), destino))

for hora, parada, ln, destino in sorted(salidas):
    print(f"{nombre.get(parada, parada)} | {ln} -> {destino} | {max(0, round((hora - ahora) / 60))} min")