#!/usr/bin/env python3
"""Vergleicht Abfrage-Strategien: Raster vs. Gemeindezentren vs. stationszentriert."""
import json, time, urllib.request, urllib.parse, sys

B="https://api.e-control.at/sprit/1.0"
UA={"Accept":"application/json","User-Agent":"tankradar/1.0"}
def get(p,**q):
    r=urllib.request.Request(f"{B}{p}?"+urllib.parse.urlencode(q),headers=UA)
    with urllib.request.urlopen(r,timeout=30) as f: return json.load(f)
def addr(la,lo,ft="DIE"):
    return get("/search/gas-stations/by-address",latitude=la,longitude=lo,fuelType=ft,includeClosed="true")

def run(points, label):
    st, pr = {}, {}
    for i,(la,lo) in enumerate(points):
        try: res = addr(round(la,5), round(lo,5))
        except Exception as e: print("  !",e,file=sys.stderr); continue
        for s in res:
            st[s["id"]]=s
            if s.get("prices"): pr[s["id"]]=s["prices"][0]["amount"]
        time.sleep(0.25)
    print(f"{label:<34} {len(points):>4} Abfragen -> {len(st):>3} Stationen, "
          f"{len(pr):>3} mit Preis ({len(pr)/max(len(st),1)*100:.0f}%)")
    return st, pr

# --- A: bisheriges Raster ---
LAT0,LAT1,LON0,LON1,STEP = 47.22,47.48,9.56,9.78,0.02
grid=[(LAT0+i*STEP, LON0+j*STEP)
      for i in range(int((LAT1-LAT0)/STEP)+1) for j in range(int((LON1-LON0)/STEP)+1)]

# --- B: Gemeindezentren im selben Gebiet ---
units=get("/regions/units",type="GEM")
gem=[(g["b"],g["l"]) for bl in units for bz in bl["b"] for g in bz["g"]
     if LAT0<=g["b"]<=LAT1 and LON0<=g["l"]<=LON1]

print(f"Testgebiet Rheintal | Raster: {len(grid)} Punkte | Gemeinden: {len(gem)} Punkte\n")
gs, gp = run(grid, "A  Raster 2,2 km")
ms, mp = run(gem,  "B  Gemeindezentren")

# --- C: zusätzlich auf jede bekannte Station zentrieren ---
known = {**gs, **ms}
selfpts=[(s["location"]["latitude"], s["location"]["longitude"]) for s in known.values()
         if s.get("location",{}).get("latitude")]
ss, sp = run(selfpts, "C  auf jede Station zentriert")

allst = {**gs, **ms, **ss}
allpr = {**gp, **mp, **sp}
print(f"\n{'A+B+C kombiniert':<34} {len(grid)+len(gem)+len(selfpts):>4} Abfragen -> "
      f"{len(allst):>3} Stationen, {len(allpr):>3} mit Preis ({len(allpr)/len(allst)*100:.0f}%)")
print(f"{'nur B+C':<34} {len(gem)+len(selfpts):>4} Abfragen -> "
      f"{len(allst):>3} Stationen, {len({**mp,**sp}):>3} mit Preis ({len({**mp,**sp})/len(allst)*100:.0f}%)")

blind=[s for i,s in allst.items() if i not in allpr]
print(f"\nweiterhin ohne Preis: {len(blind)}")
for s in blind: print(f"   {s.get('name')} · {s['location'].get('city')}")
json.dump({"grid":list(gp),"gem":list(mp),"self":list(sp)},open("strategy_test.json","w"))
