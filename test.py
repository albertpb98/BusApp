
import requests, datetime
from google.transit import gtfs_realtime_pb2
import truststore
truststore.inject_into_ssl()

URL = "https://www.ambmobilitat.cat/transit/trips-updates/trips.bin"
feed = gtfs_realtime_pb2.FeedMessage()
feed.ParseFromString(requests.get(URL, timeout=10).content)

print("Generado:", datetime.datetime.fromtimestamp(feed.header.timestamp))
print("Entidades:", len(feed.entity))

for e in feed.entity:
    if e.HasField("trip_update"):
        t = e.trip_update
        for s in t.stop_time_update:
            print(t.trip.route_id, s.stop_id, s.arrival.time)
        break  # solo el primero, para ver la forma