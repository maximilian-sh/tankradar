#!/usr/bin/env python3
"""Misst, wie viel Preis-Abdeckung man per Grid-Tiling aus der E-Control API holt."""
import json, time, urllib.request, urllib.parse, sys

BASE = "https://api.e-control.at/sprit/1.0/search/gas-stations/by-address"

def q(lat, lon, ft="DIE"):
    url = BASE + "?" + urllib.parse.urlencode(
        {"latitude": lat, "longitude": lon, "fuelType": ft, "includeClosed": "true"})
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "tankradar-research/0.1"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)

# Rheintal-Bereich um Götzis/Altach
LAT0, LAT1, LON0, LON1 = 47.22, 47.48, 9.56, 9.78
STEP = 0.02   # ~2.2 km

stations, priced, hits = {}, {}, 0
lats = [LAT0 + i*STEP for i in range(int((LAT1-LAT0)/STEP)+1)]
lons = [LON0 + i*STEP for i in range(int((LON1-LON0)/STEP)+1)]

for la in lats:
    for lo in lons:
        try:
            res = q(round(la,5), round(lo,5))
        except Exception as e:
            print("ERR", la, lo, e, file=sys.stderr); continue
        hits += 1
        for s in res:
            sid = s["id"]
            stations[sid] = s
            if s.get("prices"):
                priced.setdefault(sid, s["prices"][0]["amount"])
        time.sleep(0.25)

print(f"Grid-Punkte abgefragt : {hits}")
print(f"Stationen gefunden    : {len(stations)}")
print(f"davon MIT Preis       : {len(priced)}")
print(f"ohne Preis (blind)    : {len(stations)-len(priced)}")
print(f"Abdeckung             : {len(priced)/len(stations)*100:.1f}%")
json.dump({"stations": {str(k): v for k, v in stations.items()},
           "priced": {str(k): v for k, v in priced.items()}},
          open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "coverage_probe.json"), "w"), ensure_ascii=False)
