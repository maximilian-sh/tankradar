#!/usr/bin/env python3
"""
tankradar.py — Spritpreis-Tracker für Österreich (Quelle: E-Control Spritpreisrechner API)

Befehle:
  python3 tankradar.py collect          einmalig Preise sammeln
  python3 tankradar.py watch [--every N]  dauerhaft alle N Minuten sammeln (default 30)
  python3 tankradar.py stats            Auswertung im Terminal
  python3 tankradar.py serve [--port P] Web-UI starten (default 842)
  python3 tankradar.py export           Snapshot als JSON nach data/snapshot.json
"""
import argparse, json, math, os, re, sqlite3, sys, time, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("TANKRADAR_DATA") or os.path.join(ROOT, "data")
DB   = os.path.join(DATA, "tankradar.db")
API  = "https://api.e-control.at/sprit/1.0"
UA   = "tankradar/1.0 (privates Preis-Monitoring)"
FUELS = ("DIE", "SUP", "GAS")
# E-Control meldet vereinzelt offensichtlich falsche Werte — eine Wiener Station
# führt 14,00 €/l Diesel, eine andere 4,00 €. Lokal fällt das nie auf, landesweit
# verzerrt es jeden Durchschnitt und würde als "günstigste" Wahl durchgehen, wenn
# der Fehler nach unten ginge. Alles außerhalb dieser Spanne wird nicht gespeichert.
PLAUSIBEL = (0.40, 3.50)
# Echte Zeitzone statt festem Offset: der gesamte Abtastplan hängt an 12:00 Wiener
# Zeit. Ein fester UTC+2-Offset läge ab der Winterzeitumstellung eine Stunde daneben
# und würde den Mittagssprung verfehlen.
try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo("Europe/Vienna")
except Exception:                                    # ohne tzdata im System
    TZ = timezone(timedelta(hours=1))

# ---- Konfiguration ----------------------------------------------------------
# Kein handgepflegtes Koordinatenraster mehr: Abfragepunkte sind die offiziellen
# Gemeindezentren der E-Control im Umkreis von `radius_km` um `home`.
CONF = {
    "home":      {"name": "Altach", "lat": 47.354655, "lon": 9.651645},
    "radius_km": 20,          # Gemeindezentren für die Empfehlung rundherum
    "app_km":    35,          # was die App höchstens anzeigt
    # Regionsabfragen für ganz Österreich: 9 Bundesländer + 117 Bezirke.
    # Kostet gut eine Minute je Lauf und liefert das günstige Marktsegment
    # landesweit — Historie lässt sich nicht nachträglich erheben.
    "national":  True,
    "fuels":     ["DIE", "SUP"],
    "delay":     0.25,
}

# --------------------------------------------------------------------------- API
def _get(path, **params):
    url = f"{API}{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def by_address(lat, lon, fuel):
    return _get("/search/gas-stations/by-address", latitude=lat, longitude=lon,
                fuelType=fuel, includeClosed="true")

def by_region(code, rtype, fuel):
    return _get("/search/gas-stations/by-region", code=code, type=rtype,
                fuelType=fuel, includeClosed="true")

_GEM_CACHE = os.path.join(DATA, "gemeinden.json")
_REG_CACHE = os.path.join(DATA, "regionen.json")

def all_regions(force=False):
    """Alle Bundesländer und Bezirke als (code, typ) — für die landesweite Stufe."""
    if not force and os.path.exists(_REG_CACHE):
        if time.time() - os.path.getmtime(_REG_CACHE) < 30 * 86400:
            return [tuple(x) for x in json.load(open(_REG_CACHE))]
    regs = _get("/regions")
    out = [(str(bl["code"]), "BL") for bl in regs]
    out += [(str(bz["code"]), "PB") for bl in regs for bz in bl.get("subRegions", [])]
    os.makedirs(os.path.dirname(_REG_CACHE), exist_ok=True)
    json.dump(out, open(_REG_CACHE, "w"))
    return out

def gemeinden(force=False):
    """Alle 2231 österreichischen Gemeinden mit amtlichen Koordinaten.
    Undokumentiert, aber stabil: /regions/units liefert eine dritte Ebene `g`."""
    if not force and os.path.exists(_GEM_CACHE):
        age = time.time() - os.path.getmtime(_GEM_CACHE)
        if age < 30 * 86400:
            return json.load(open(_GEM_CACHE))
    units = _get("/regions/units", type="GEM")
    out = [{"plz": g["p"], "name": g["n"], "lat": g["b"], "lon": g["l"],
            "bl": bl["n"], "bezirk": bz["n"]}
           for bl in units for bz in bl["b"] for g in bz["g"]]
    os.makedirs(os.path.dirname(_GEM_CACHE), exist_ok=True)
    json.dump(out, open(_GEM_CACHE, "w"), ensure_ascii=False)
    return out

def query_points(c=None, deep=False):
    """Abfragepunkte: Gemeindezentren im Umkreis. Beim Tiefenlauf (`deep`) zusätzlich
    die Koordinaten bekannter Tankstellen — das findet neue Stationen zwischen zwei
    Gemeinden, bringt aber kaum zusätzliche Preise. Daher nur einmal täglich."""
    h = CONF["home"]; r = CONF["radius_km"]
    pts = [(g["lat"], g["lon"], f'{g["name"]} ({g["plz"]})')
           for g in gemeinden()
           if haversine(h["lat"], h["lon"], g["lat"], g["lon"]) <= r]
    if c is not None and deep:
        for row in c.execute("SELECT lat, lon, name FROM stations "
                             "WHERE lat IS NOT NULL AND lon IS NOT NULL"):
            if haversine(h["lat"], h["lon"], row["lat"], row["lon"]) <= r * 1.3:
                pts.append((row["lat"], row["lon"], f'@{row["name"] or "Station"}'))
    seen, uniq = set(), []
    for la, lo, lbl in pts:
        k = (round(la, 4), round(lo, 4))
        if k not in seen:
            seen.add(k); uniq.append((la, lo, lbl))
    return uniq

# ---------------------------------------------------------------------------- DB
SCHEMA = """
CREATE TABLE IF NOT EXISTS stations (
  id INTEGER PRIMARY KEY, name TEXT, address TEXT, postal_code TEXT, city TEXT,
  lat REAL, lon REAL, website TEXT, telephone TEXT,
  self_service INT, unattended INT, open_247 INT,
  first_seen TEXT, last_seen TEXT,
  bl INTEGER, bezirk INTEGER, local INT DEFAULT 0, brand TEXT
);
CREATE TABLE IF NOT EXISTS prices (
  station_id INTEGER, fuel TEXT, amount REAL, ts TEXT,
  PRIMARY KEY (station_id, fuel, ts)
);
CREATE INDEX IF NOT EXISTS ix_prices_sf ON prices(station_id, fuel, ts);
-- Aktueller Stand je Station und Sorte. Ohne das müsste jede Abfrage des
-- "jetzigen" Preises MAX(ts) über die gesamte Historie gruppieren.
CREATE TABLE IF NOT EXISTS latest (
  station_id INTEGER, fuel TEXT, amount REAL, ts TEXT,
  PRIMARY KEY (station_id, fuel)
);
-- Aggregate: die Auswertungen lesen daraus statt jedes Mal die Treppenfunktion
-- aus den Rohzeilen zu rekonstruieren. Abgeschlossene Stunden ändern sich nie
-- wieder und werden genau einmal berechnet.
CREATE TABLE IF NOT EXISTS agg_hourly (
  fuel TEXT, region TEXT, hour TEXT,
  n INT, mn REAL, p25 REAL, med REAL, avg REAL, p75 REAL, mx REAL,
  PRIMARY KEY (fuel, region, hour)
);
CREATE TABLE IF NOT EXISTS agg_station_day (
  station_id INT, fuel TEXT, day TEXT,
  n INT, avg REAL, mn REAL, mx REAL, noon_pre REAL, noon_post REAL,
  PRIMARY KEY (station_id, fuel, day)
);
CREATE TABLE IF NOT EXISTS agg_hour_delta (
  fuel TEXT, region TEXT, day TEXT, hour INT,
  sum_delta REAL, n INT,
  PRIMARY KEY (fuel, region, day, hour)
);
CREATE TABLE IF NOT EXISTS agg_meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_agg_hourly ON agg_hourly(fuel, region, hour);
CREATE INDEX IF NOT EXISTS ix_agg_sd ON agg_station_day(fuel, day);
CREATE INDEX IF NOT EXISTS ix_agg_hd ON agg_hour_delta(fuel, region, day);
CREATE TABLE IF NOT EXISTS geofix (
  station_id INTEGER PRIMARY KEY, lat REAL, lon REAL,
  off_m REAL, osm_id INTEGER, osm_name TEXT, checked TEXT
);
CREATE TABLE IF NOT EXISTS polls (
  ts TEXT PRIMARY KEY, fuel TEXT, queries INT, stations INT, priced INT
);
"""

def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    # Bestandsdaten aus der Zeit vor der Umbenennung übernehmen
    old = os.path.join(DATA, "tanken.db")
    if not os.path.exists(DB) and os.path.exists(old):
        for suf in ("", "-wal", "-shm"):
            if os.path.exists(old + suf):
                os.rename(old + suf, DB + suf)
        print(f"Datenbank übernommen: {old} -> {DB}", file=sys.stderr, flush=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    # Spalten, die es in älteren Datenbanken noch nicht gab
    have = {r["name"] for r in c.execute("PRAGMA table_info(stations)")}
    for col, decl in (("bl", "INTEGER"), ("bezirk", "INTEGER"), ("local", "INT DEFAULT 0"),
                      ("brand", "TEXT")):
        if col not in have:
            c.execute(f"ALTER TABLE stations ADD COLUMN {col} {decl}")
    # Erst nach der Migration: bei einer alten Datenbank gibt es die Spalte
    # zum Zeitpunkt von executescript noch nicht.
    c.execute("CREATE INDEX IF NOT EXISTS ix_stations_bl ON stations(bl)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_stations_local ON stations(local)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_stations_brand ON stations(brand)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_asd_fd ON agg_station_day(fuel, day, station_id)")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c

def now(): return datetime.now(TZ).isoformat(timespec="seconds")

def haversine(a1, o1, a2, o2):
    R = 6371.0
    p1, p2 = math.radians(a1), math.radians(a2)
    dp, dl = math.radians(a2-a1), math.radians(o2-o1)
    h = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(h))

# ----------------------------------------------------------------------- COLLECT
def upsert_station(c, s):
    loc = s.get("location") or {}; con = s.get("contact") or {}; oi = s.get("offerInformation") or {}
    oh = s.get("openingHours") or []
    is247 = bool(oh) and all(h.get("from") == "00:00" and h.get("to") == "24:00" for h in oh)
    t = now()
    c.execute("""INSERT INTO stations (id,name,address,postal_code,city,lat,lon,website,telephone,
                 self_service,unattended,open_247,first_seen,last_seen)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET
                   name=COALESCE(excluded.name,stations.name), address=excluded.address,
                   postal_code=excluded.postal_code, city=excluded.city,
                   lat=excluded.lat, lon=excluded.lon, website=excluded.website,
                   telephone=excluded.telephone, self_service=excluded.self_service,
                   unattended=excluded.unattended, open_247=excluded.open_247,
                   last_seen=excluded.last_seen""",
              (s["id"], s.get("name"), loc.get("address"), loc.get("postalCode"), loc.get("city"),
               loc.get("latitude"), loc.get("longitude"), con.get("website"), con.get("telephone"),
               int(bool(oi.get("selfService"))), int(bool(oi.get("unattended"))), int(is247), t, t))

def record_price(c, sid, fuel, amount, ts):
    """Nur speichern, wenn sich der Preis geändert hat -> kompakte Änderungs-Historie."""
    if not (PLAUSIBEL[0] <= amount <= PLAUSIBEL[1]):
        return False
    last = c.execute("SELECT amount FROM prices WHERE station_id=? AND fuel=? "
                     "ORDER BY ts DESC LIMIT 1", (sid, fuel)).fetchone()
    if last and abs(last["amount"] - amount) < 1e-9:
        return False
    c.execute("INSERT OR IGNORE INTO prices (station_id,fuel,amount,ts) VALUES (?,?,?,?)",
              (sid, fuel, amount, ts))
    c.execute("""INSERT INTO latest (station_id,fuel,amount,ts) VALUES (?,?,?,?)
                 ON CONFLICT(station_id,fuel) DO UPDATE SET amount=excluded.amount, ts=excluded.ts
                 WHERE excluded.ts >= latest.ts""", (sid, fuel, amount, ts))
    return True

def collect(verbose=True, deep=False):
    c = db(); ts = now(); total_new = 0
    pts = query_points(c, deep=deep)

    implausible = []
    for fuel in CONF["fuels"]:
        seen, priced, queries = set(), set(), 0
        targets = [("addr", round(la, 5), round(lo, 5)) for la, lo, _ in pts]
        regions = all_regions() if CONF.get("national") else CONF.get("regions", [])
        targets += [("reg", code, rtype) for code, rtype in regions]
        for kind, a, b in targets:
            try:
                res = by_address(a, b, fuel) if kind == "addr" else by_region(a, b, fuel)
            except Exception as e:
                if verbose: print(f"  ! {kind} {a},{b} {fuel}: {e}", file=sys.stderr)
                continue
            queries += 1
            for s in res:
                upsert_station(c, s); seen.add(s["id"])
                for p in (s.get("prices") or []):
                    a = p.get("amount")
                    if not a:
                        continue
                    if not (PLAUSIBEL[0] <= a <= PLAUSIBEL[1]):
                        implausible.append((s["id"], s.get("name"), p["fuelType"], a))
                        continue
                    priced.add(s["id"])
                    if record_price(c, s["id"], p["fuelType"], a, ts):
                        total_new += 1
            time.sleep(CONF["delay"])
        c.execute("INSERT OR REPLACE INTO polls (ts,fuel,queries,stations,priced) VALUES (?,?,?,?,?)",
                  (ts, fuel, queries, len(seen), len(priced)))
        c.commit()
        if verbose:
            print(f"[{ts}] {fuel}: {queries} Abfragen, {len(seen)} Stationen, "
                  f"{len(priced)} mit Preis ({len(priced)/max(len(seen),1)*100:.0f}% Abdeckung)")
    if verbose:
        print(f"  -> {total_new} Preisänderungen gespeichert")
        if implausible:
            uniq = {(i, n, f): a for i, n, f, a in implausible}
            print(f"  !! {len(uniq)} unplausible Meldungen verworfen:")
            for (i, n, f), a in list(uniq.items())[:6]:
                print(f"     {a:>8.3f} €/l {f}  {str(n)[:30]} (id {i})")
    try:
        assign_regions(c, verbose=verbose)
    except Exception as e:
        print("Regionszuordnung übersprungen:", e, file=sys.stderr)
    try:
        aggregate(c, verbose=verbose)
    except Exception as e:
        print("Aggregation fehlgeschlagen:", e, file=sys.stderr)
    c.close()
    # Zwischenspeicher gleich füllen, damit nicht die erste Anfrage nach jedem
    # Lauf die Rechenzeit trägt.
    try:
        t0 = time.time()
        _analysis_cache.clear()
        for sc in ("local", "at"):
            for d in (1, 7, 30, 365):
                build_analysis(d, sc)
        if verbose:
            print(f"  Auswertungen vorgewärmt in {time.time()-t0:.1f}s")
    except Exception as e:
        print("Vorwärmen übersprungen:", e, file=sys.stderr)
    if deep:
        try: correct_coordinates(verbose=verbose)
        except Exception as e: print("OSM-Abgleich übersprungen:", e, file=sys.stderr)
    return total_new

# ------------------------------------------------------------------- AUSWERTUNG
def current_prices(c, fuel):
    """Letzter bekannter Preis je Station + Zeitpunkt der letzten Änderung."""
    rows = c.execute("""
      SELECT p.station_id, p.amount, p.ts, s.name, s.city, s.address, s.postal_code,
             s.lat, s.lon, s.open_247, s.unattended, s.website
      FROM prices p
      JOIN stations s ON s.id = p.station_id
      JOIN (SELECT station_id, MAX(ts) mts FROM prices WHERE fuel=? GROUP BY station_id) m
        ON m.station_id = p.station_id AND m.mts = p.ts
      WHERE p.fuel = ?
    """, (fuel, fuel)).fetchall()
    home = CONF["home"]
    out = []
    for r in rows:
        d = haversine(home["lat"], home["lon"], r["lat"], r["lon"]) if r["lat"] else None
        out.append({**dict(r), "distance_km": round(d, 2) if d is not None else None})
    return out

def hour_profile(c, fuel, days=60, ids=None):
    """Durchschnittliche Abweichung vom Tagesmittel je Stunde.

    Normiert wird je Station, nicht über alle Stationen gepoolt: Der Tiefenlauf um
    03:00 sieht Stationen, die sonst nie auftauchen (und teurer sind). Gepoolt sähe
    diese Stunde dadurch teuer aus, obwohl sich kein einziger Preis geändert hat —
    gemessen würde die Stichprobenzusammensetzung, nicht die Uhrzeit.
    """
    since = (datetime.now(TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    cl, pa = _idclause(ids)
    rows = c.execute("SELECT station_id, amount, ts FROM prices WHERE fuel=? AND ts>=?" + cl,
                     (fuel, since, *pa)).fetchall()
    per = {}
    for r in rows:
        per.setdefault((r["station_id"], r["ts"][:10]), []).append(r["amount"])
    base = {k: sum(v) / len(v) for k, v in per.items()}
    buckets = {}
    for r in rows:
        b = base.get((r["station_id"], r["ts"][:10]))
        if b is None:
            continue
        try: h = int(r["ts"][11:13])
        except ValueError: continue
        buckets.setdefault(h, []).append(r["amount"] - b)
    return {h: {"delta_ct": round(sum(v)/len(v)*100, 2), "n": len(v)}
            for h, v in sorted(buckets.items())}

def weekday_profile(c, fuel, days=90, ids=None):
    """Wie hour_profile: je Station normiert, sonst misst man die Stichprobe."""
    since = (datetime.now(TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    cl, pa = _idclause(ids)
    rows = c.execute("SELECT station_id, amount, ts FROM prices WHERE fuel=? AND ts>=?" + cl,
                     (fuel, since, *pa)).fetchall()
    per = {}
    for r in rows:
        per.setdefault(r["station_id"], []).append(r["amount"])
    base = {k: sum(v)/len(v) for k, v in per.items()}
    names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    b = {}
    for r in rows:
        try: dt = datetime.fromisoformat(r["ts"])
        except ValueError: continue
        m = base.get(r["station_id"])
        if m is None: continue
        b.setdefault(dt.weekday(), []).append(r["amount"] - m)
    return {names[k]: {"delta_ct": round(sum(v)/len(v)*100, 2), "n": len(v)}
            for k, v in sorted(b.items())}

def noon_jump(c, fuel, days=30, ids=None):
    """Preis um 11:55 gegen 12:15 desselben Tages, paarweise je Station.

    Schärfer als das Stundenprofil: dieselbe Station, 20 Minuten Abstand, damit
    fällt jede Zusammensetzungsfrage weg. Misst direkt, was die 12:00-Regel bewirkt.
    """
    def at(sid, when):
        # Vergleich auf der lokalen Wanduhr (ohne Offset-Suffix): die Zeitstempel
        # tragen je nach Jahreszeit +02:00 oder +01:00, ein direkter String-
        # Vergleich mit fixem Offset wäre im Winter falsch.
        r = c.execute("SELECT amount FROM prices WHERE station_id=? AND fuel=? "
                      "AND substr(ts,1,19)<=? ORDER BY ts DESC LIMIT 1",
                      (sid, fuel, when)).fetchone()
        return r["amount"] if r else None

    now_d = datetime.now(TZ)
    cl, pa = _idclause(ids)
    sids = [r["station_id"] for r in
            c.execute("SELECT DISTINCT station_id FROM prices WHERE fuel=?" + cl, (fuel, *pa))]
    out = []
    for i in range(days):
        d = (now_d - timedelta(days=i)).date()
        # nur Tage, deren Mittag schon vorbei ist
        if d == now_d.date() and now_d.hour < 13:
            continue
        pre, post = f"{d}T11:59:00", f"{d}T12:20:00"
        deltas = []
        for sid in sids:
            a, b = at(sid, pre), at(sid, post)
            if a is not None and b is not None:
                deltas.append(b - a)
        if len(deltas) < 5:
            continue
        deltas.sort()
        med = deltas[len(deltas)//2]
        out.append({"date": str(d), "n": len(deltas),
                    "median_ct": round(med*100, 2),
                    "mean_ct": round(sum(deltas)/len(deltas)*100, 2),
                    "up": sum(1 for x in deltas if x > 0.0005),
                    "down": sum(1 for x in deltas if x < -0.0005)})
    return list(reversed(out))

def trend(c, fuel, days=30):
    since = (datetime.now(TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    return [[r["d"], round(r["a"], 4), r["n"]] for r in c.execute(
        "SELECT substr(ts,1,13) d, AVG(amount) a, COUNT(*) n FROM prices "
        "WHERE fuel=? AND ts>=? GROUP BY d ORDER BY d", (fuel, since))]

def coverage(c):
    cached = c.execute("SELECT v FROM agg_meta WHERE k='coverage'").fetchone()
    if cached:
        return json.loads(cached["v"])
    tot = c.execute("SELECT COUNT(*) n FROM stations").fetchone()["n"]
    out = {"stations": tot, "fuels": {}}
    for f in CONF["fuels"]:
        n = c.execute("SELECT COUNT(DISTINCT station_id) n FROM prices WHERE fuel=?",
                      (f,)).fetchone()["n"]
        out["fuels"][f] = {"priced": n, "pct": round(n/tot*100, 1) if tot else 0}
    r = c.execute("SELECT COUNT(DISTINCT ts) n, MIN(ts) a, MAX(ts) b FROM polls").fetchone()
    out["polls"] = {"n": r["n"], "first": r["a"], "last": r["b"]}
    return out

def data_span(c):
    cached = c.execute("SELECT v FROM agg_meta WHERE k='span'").fetchone()
    if cached:
        d = json.loads(cached["v"])
        if d.get("a") is None and d.get("from") is None: return None
        try:
            days = (datetime.fromisoformat(d["to"]) - datetime.fromisoformat(d["from"])).total_seconds()/86400
        except (ValueError, TypeError, KeyError): days = 0
        return {"from": d["from"], "to": d["to"], "days": round(days, 2), "points": d["points"]}
    r = c.execute("SELECT MIN(ts) a, MAX(ts) b, COUNT(*) n FROM prices").fetchone()
    if not r or not r["a"]: return None
    try:
        d = (datetime.fromisoformat(r["b"]) - datetime.fromisoformat(r["a"])).total_seconds()/86400
    except ValueError: d = 0
    return {"from": r["a"], "to": r["b"], "days": round(d, 2), "points": r["n"]}

def confidence(span):
    """Ehrliche Selbsteinschätzung, ob eine Zeitpunkt-Prognose überhaupt trägt."""
    if not span: return ("keine Daten", 0)
    d = span["days"]
    if d < 3:   return ("zu wenig Daten – nur Momentaufnahme", 0)
    if d < 14:  return ("erste Tendenz, statistisch noch nicht belastbar", 1)
    if d < 28:  return ("brauchbare Tendenz", 2)
    return ("belastbar", 3)

# ------------------------------------------------------------------------ STATS
def cmd_stats(args):
    c = db(); span = data_span(c)
    print("=" * 74)
    print("  TANKRADAR — Auswertung", f"(Basis: {CONF['home']['name']})")
    print("=" * 74)
    if not span:
        print("Noch keine Daten. Zuerst:  python3 tankradar.py collect"); return
    lbl, lvl = confidence(span)
    print(f"Zeitraum : {span['from'][:16]} → {span['to'][:16]}  ({span['days']} Tage)")
    print(f"Messwerte: {span['points']} Preisänderungen | Aussagekraft: {lbl}\n")

    for fuel in CONF["fuels"]:
        cur = sorted([x for x in current_prices(c, fuel) if x["distance_km"] is not None],
                     key=lambda x: x["amount"])
        if not cur: continue
        label = {"DIE": "Diesel", "SUP": "Super 95", "GAS": "CNG"}[fuel]
        print(f"── {label} ──  günstigste im Umkreis")
        print(f'{"Preis":>7}  {"km":>5}  {"Station":<30} {"Ort":<14} Stand')
        for x in cur[:10]:
            nm = (x["name"] or "—")[:29]
            print(f'{x["amount"]:>7.3f}  {x["distance_km"]:>5.1f}  {nm:<30} '
                  f'{str(x["city"])[:13]:<14} {x["ts"][5:16]}')
        near = sorted(cur, key=lambda x: x["distance_km"])[:5]
        print(f'\n  nächstgelegene: ' + ", ".join(
            f'{(x["name"] or "—")[:18]} {x["distance_km"]}km {x["amount"]:.3f}' for x in near))
        hp = hour_profile(c, fuel)
        if hp and lvl >= 1:
            best = min(hp.items(), key=lambda kv: kv[1]["delta_ct"])
            worst = max(hp.items(), key=lambda kv: kv[1]["delta_ct"])
            print(f'  Tagesprofil : günstigste Stunde {best[0]:02d}:00 ({best[1]["delta_ct"]:+.2f} ct), '
                  f'teuerste {worst[0]:02d}:00 ({worst[1]["delta_ct"]:+.2f} ct)')
        wp = weekday_profile(c, fuel)
        if wp and lvl >= 2:
            bw = min(wp.items(), key=lambda kv: kv[1]["delta_ct"])
            print(f'  Wochenprofil: günstigster Tag {bw[0]} ({bw[1]["delta_ct"]:+.2f} ct)')
        print()
    print("Hinweis: In AT dürfen Preise nur um 12:00 erhöht werden, Senkungen jederzeit.")
    print("         Erwartung: kurz vor 12:00 am günstigsten, direkt danach am teuersten.")
    c.close()

# ----------------------------------------------------------------------- EXPORT
def build_snapshot():
    c = db(); span = data_span(c); lbl, lvl = confidence(span)
    snap = {"generated": now(), "home": CONF["home"], "span": span,
            "confidence": {"label": lbl, "level": lvl}, "fuels": {}}
    for fuel in CONF["fuels"]:
        cur = [x for x in current_prices(c, fuel) if x["distance_km"] is not None]
        cur.sort(key=lambda x: x["amount"])
        hist = {}
        for x in cur[:40]:
            rows = c.execute("SELECT amount, ts FROM prices WHERE station_id=? AND fuel=? "
                             "ORDER BY ts", (x["station_id"], fuel)).fetchall()
            hist[str(x["station_id"])] = [[r["ts"], r["amount"]] for r in rows]
        snap["fuels"][fuel] = {"stations": cur, "history": hist,
                               "hour_profile": hour_profile(c, fuel),
                               "weekday_profile": weekday_profile(c, fuel)}
    c.close(); return snap

def build_stations(max_km=None, origin=None):
    """Alle Stationen mit letztem Preis je Sorte — ohne Bezug auf CONF['home'],
    damit die App die Distanz vom tatsächlichen Standort rechnen kann."""
    c = db()
    st = {}
    for r in c.execute("""SELECT s.*, g.lat AS glat, g.lon AS glon, g.off_m
                          FROM stations s LEFT JOIN geofix g ON g.station_id = s.id
                          WHERE s.lat IS NOT NULL"""):
        # Wo OSM einen Tankstellenknoten kennt, gilt dessen Position: die
        # E-Control-Koordinate liegt teils mehrere hundert Meter daneben.
        lat = r["glat"] if r["glat"] is not None else r["lat"]
        lon = r["glon"] if r["glon"] is not None else r["lon"]
        st[r["id"]] = {"id": r["id"], "n": r["name"], "c": r["city"], "a": r["address"],
                       "z": r["postal_code"], "lat": lat, "lon": lon,
                       "fix_m": r["off_m"], "h24": r["open_247"], "sb": r["unattended"],
                       "w": r["website"], "p": {}}
    for f in CONF["fuels"]:
        for r in c.execute("SELECT station_id s, amount a, ts t FROM latest WHERE fuel=?", (f,)):
            if r["s"] in st:
                st[r["s"]]["p"][f] = [r["a"], r["t"]]
    sp = data_span(c); lbl, lvl = confidence(sp)
    c.close()
    out = [v for v in st.values() if v["p"]]
    if max_km:
        o = origin or CONF["home"]
        out = [v for v in out
               if haversine(o["lat"], o["lon"], v["lat"], v["lon"]) <= max_km]
    return {"generated": now(), "fuels": CONF["fuels"], "home": CONF["home"],
            "span": sp, "confidence": {"label": lbl, "level": lvl},
            "count_total": len(st), "stations": out}

# --------------------------------------------------------------- KOORDINATEN
# Die Koordinaten der E-Control sind teils mehrere hundert Meter daneben — die
# Adresse stimmt, der Punkt nicht. Für Distanzberechnung und Navigation ist das
# der Unterschied zwischen "richtige Einfahrt" und "Nachbargrundstück". Deshalb
# Abgleich gegen die Tankstellen-Knoten in OpenStreetMap.
# Mehrere Instanzen: die öffentliche Haupt-Instanz antwortet unter Last mit 504.
OVERPASS = [u for u in [os.environ.get("OVERPASS_URL"),
                        "https://overpass-api.de/api/interpreter",
                        "https://overpass.kumi.systems/api/interpreter",
                        "https://overpass.osm.ch/api/interpreter"] if u]

def _norm(t):
    t = (t or "").lower()
    for a, b in (("ö","oe"),("ä","ae"),("ü","ue"),("ß","ss")):
        t = t.replace(a, b)
    return set(re.findall(r"[a-z0-9]{3,}", t))

def correct_coordinates(verbose=True):
    """Jede Station dem nächsten OSM-Tankstellenknoten zuordnen (max. 400 m)."""
    c = db()
    rows = c.execute("SELECT id, name, address, lat, lon FROM stations "
                     "WHERE lat IS NOT NULL AND lon IS NOT NULL").fetchall()
    if not rows:
        c.close(); return 0
    pad = 0.02
    la = [r["lat"] for r in rows]; lo = [r["lon"] for r in rows]
    bbox = (min(la)-pad, min(lo)-pad, max(la)+pad, max(lo)+pad)
    query = (f"[out:json][timeout:60];"
             f"node[amenity=fuel]({bbox[0]:.4f},{bbox[1]:.4f},{bbox[2]:.4f},{bbox[3]:.4f});"
             f"out center;")
    osm, err = None, None
    for url in OVERPASS:
        try:
            req = urllib.request.Request(url, data=("data=" + urllib.parse.quote(query)).encode(),
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as f:
                osm = json.load(f)["elements"]
            break
        except Exception as e:
            err = f"{url}: {e}"
            if verbose: print(f"  Overpass ausgefallen, nächster Versuch — {err}", file=sys.stderr)
            time.sleep(2)
    if osm is None:
        if verbose: print("OSM-Abgleich nicht möglich:", err, file=sys.stderr)
        c.close(); return 0

    def tags(n): return n.get("tags") or {}
    nodes = [{"id": n["id"], "lat": n["lat"], "lon": n["lon"],
              "name": tags(n).get("name") or tags(n).get("brand") or "",
              "tok": _norm(tags(n).get("name", "") + " " + tags(n).get("brand", "")),
              "street": _norm(tags(n).get("addr:street", "")),
              "hn": (tags(n).get("addr:housenumber") or "").strip().lower()}
             for n in osm if "lat" in n]

    t, fixed, far, unsure = now(), 0, 0, 0
    for r in rows:
        near = sorted([(haversine(r["lat"], r["lon"], n["lat"], n["lon"]), n) for n in nodes],
                      key=lambda x: x[0])
        near = [x for x in near if x[0] <= 0.4]
        if not near:
            far += 1
            continue
        tok = _norm(r["name"])
        adr = (r["address"] or "").strip().lower()
        adr_tok = _norm(adr)
        hn = next(iter(re.findall(r"\b(\d+[a-z]?)\b", adr)), None)

        pick = None
        # 1. Name oder Marke stimmt überein — stärkstes Indiz
        for d, n in near:
            if tok & n["tok"]:
                pick = (d, n, "name"); break
        # 2. Straße und Hausnummer stimmen überein
        if not pick:
            for d, n in near:
                if n["street"] and n["street"] & adr_tok and hn and n["hn"] == hn:
                    pick = (d, n, "adresse"); break
        # 3. Ohne Beleg nur, wenn der Knoten sehr nah und eindeutig allein ist.
        #    Sonst zieht man in dichten Gebieten den Nachbarbetrieb heran — eine
        #    falsche Position ist schlechter als eine ungenaue.
        if not pick and near[0][0] <= 0.15 and (len(near) == 1 or near[1][0] >= 0.3):
            pick = (near[0][0], near[0][1], "eindeutig")
        if not pick:
            unsure += 1
            continue

        d, n, why = pick
        c.execute("""INSERT INTO geofix (station_id,lat,lon,off_m,osm_id,osm_name,checked)
                     VALUES (?,?,?,?,?,?,?)
                     ON CONFLICT(station_id) DO UPDATE SET lat=excluded.lat, lon=excluded.lon,
                       off_m=excluded.off_m, osm_id=excluded.osm_id,
                       osm_name=excluded.osm_name, checked=excluded.checked""",
                  (r["id"], n["lat"], n["lon"], round(d*1000, 1), n["id"],
                   f'{n["name"]} [{why}]', t))
        fixed += 1

    c.commit()
    if verbose:
        st = c.execute("SELECT COUNT(*) n, AVG(off_m) a, MAX(off_m) m FROM geofix").fetchone()
        print(f"  OSM-Abgleich: {fixed} zugeordnet, {unsure} verworfen (kein Beleg), "
              f"{far} ohne Knoten in Reichweite")
        if st["n"]:
            print(f"                Versatz im Mittel {st['a']:.0f} m, maximal {st['m']:.0f} m")
    c.close()
    return fixed

# ------------------------------------------------------------------- ROUTING
# Luftlinie unterschätzt die Fahrstrecke im Rheintal je nach Ziel um 26–47 %
# (Rhein, Autobahnauffahrten). Für eine App, die "fahr dorthin" sagt, ist das
# der Kern, also echte Straßendistanz — aber mit einer einzigen Matrix-Anfrage
# für alle Kandidaten und mit Zwischenspeicher.
OSRM = os.environ.get("OSRM_URL", "https://router.project-osrm.org")
# Im öffentlichen Betrieb gesetzt: ohne Token kein manueller Sammellauf. Sonst
# könnte jeder Besucher den Server dazu bringen, E-Control zu überrennen.
ADMIN_TOKEN = os.environ.get("TANKRADAR_TOKEN", "")
PUBLIC = os.environ.get("TANKRADAR_PUBLIC", "") == "1"
# Ratenbremse fürs Routing, das an einen fremden Demo-Dienst weiterreicht.
_rate = {}
RATE_MAX, RATE_WIN = 30, 60      # Anfragen je Fenster (Sekunden) und Client

def rate_ok(who):
    t = time.time()
    q = [x for x in _rate.get(who, []) if t - x < RATE_WIN]
    if len(q) >= RATE_MAX:
        _rate[who] = q
        return False
    q.append(t); _rate[who] = q
    if len(_rate) > 2000:
        for k in [k for k, v in _rate.items() if not v or t - v[-1] > RATE_WIN * 5]:
            _rate.pop(k, None)
    return True
_route_cache = {}
ROUTE_TTL = 3600
ROUTE_MAX = 25          # Kandidaten je Anfrage

def road_distances(lat, lon, targets):
    """[(id, lat, lon)] -> {id: {"km":…, "min":…}}. Bei Ausfall: leeres Dict."""
    key = (round(lat, 3), round(lon, 3), tuple(sorted(t[0] for t in targets)))
    hit = _route_cache.get(key)
    if hit and time.time() - hit[0] < ROUTE_TTL:
        return hit[1]
    coords = ";".join([f"{lon:.6f},{lat:.6f}"] + [f"{t[2]:.6f},{t[1]:.6f}" for t in targets])
    url = f"{OSRM}/table/v1/driving/{coords}?sources=0&annotations=distance,duration"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=12) as f:
            d = json.load(f)
        if d.get("code") != "Ok":
            return {}
        dist, dur = d["distances"][0], d["durations"][0]
        out = {}
        for i, t in enumerate(targets, start=1):
            if i < len(dist) and dist[i] is not None:
                out[t[0]] = {"km": round(dist[i] / 1000, 2),
                             "min": round(dur[i] / 60, 1) if dur[i] is not None else None}
        _route_cache[key] = (time.time(), out)
        if len(_route_cache) > 400:
            for k in sorted(_route_cache, key=lambda k: _route_cache[k][0])[:200]:
                _route_cache.pop(k, None)
        return out
    except Exception as e:
        print("Routing nicht verfügbar:", e, file=sys.stderr, flush=True)
        return {}

def build_route(lat, lon, fuel, max_km=None):
    """Kandidaten per Luftlinie vorfiltern, dann EINE Matrix-Anfrage."""
    data = build_stations(max_km or CONF.get("app_km"), {"lat": lat, "lon": lon})
    cand = []
    for s in data["stations"]:
        if fuel not in s["p"]:
            continue
        s = dict(s, air_km=round(haversine(lat, lon, s["lat"], s["lon"]), 2))
        cand.append(s)
    if not cand:
        return {"stations": [], "routing": False}
    # Vorfilter: die nächsten und die günstigsten, damit ein weit entfernter
    # Billigpreis nicht durchs Raster fällt
    by_air = sorted(cand, key=lambda x: x["air_km"])[:ROUTE_MAX - 8]
    by_price = sorted(cand, key=lambda x: x["p"][fuel][0])[:8]
    pick, seen = [], set()
    for s in by_air + by_price:
        if s["id"] not in seen:
            seen.add(s["id"]); pick.append(s)
    road = road_distances(lat, lon, [(s["id"], s["lat"], s["lon"]) for s in pick])
    for s in pick:
        r = road.get(s["id"])
        s["road_km"] = r["km"] if r else None
        s["min"] = r["min"] if r else None
    return {"generated": now(), "fuel": fuel, "from": {"lat": lat, "lon": lon},
            "routing": bool(road), "span": data["span"],
            "confidence": data["confidence"], "stations": pick}

# ------------------------------------------------------------------ AGGREGATE
BL_NAMES = {1: "Burgenland", 2: "Kärnten", 3: "Niederösterreich", 4: "Oberösterreich",
            5: "Salzburg", 6: "Steiermark", 7: "Tirol", 8: "Vorarlberg", 9: "Wien"}

def assign_regions(c, verbose=False):
    """Bundesland und Bezirk je Station aus der PLZ, plus Umkreis-Kennzeichen.

    Läuft nach jedem Sammellauf; nur Stationen ohne Zuordnung werden angefasst.
    """
    units = gemeinden()
    by_plz = {}
    for g in units:
        by_plz.setdefault(g["plz"], g)
    bl_code = {v: k for k, v in BL_NAMES.items()}
    bez_code = {}
    try:
        for bl in _get("/regions"):
            for bz in bl.get("subRegions", []):
                for plz in bz.get("postalCodes", []):
                    bez_code[plz] = (bl["code"], bz["code"])
    except Exception:
        pass

    h = CONF["home"]; km = CONF.get("app_km", 35)
    n = 0
    for r in c.execute("SELECT id, name, postal_code, lat, lon, bl FROM stations").fetchall():
        bl = bez = None
        pc = (r["postal_code"] or "").strip()
        if pc in bez_code:
            bl, bez = bez_code[pc]
        elif pc in by_plz:
            bl = bl_code.get(by_plz[pc]["bl"])
        loc = 0
        if r["lat"] is not None:
            loc = int(haversine(h["lat"], h["lon"], r["lat"], r["lon"]) <= km)
        c.execute("UPDATE stations SET bl=?, bezirk=?, local=?, brand=? WHERE id=?",
                  (bl, bez, loc, brand_of(r["name"]), r["id"]))
        n += 1
    c.commit()
    if verbose:
        miss = c.execute("SELECT COUNT(*) n FROM stations WHERE bl IS NULL").fetchone()["n"]
        print(f"  Regionen zugeordnet: {n} Stationen, {miss} ohne Bundesland")
    return n

def _regions_of(row):
    """Zu welchen Auswertungsgebieten zählt eine Station."""
    out = ["AT"]
    if row["bl"]:
        out.append(f"BL:{row['bl']}")
    if row["local"]:
        out.append("LOCAL")
    return out

def _pct(sorted_vals, q):
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    i = q * (n - 1)
    lo = int(i)
    hi = min(lo + 1, n - 1)
    f = i - lo
    return sorted_vals[lo] * (1 - f) + sorted_vals[hi] * f

AGG_REDO_DAYS = 2      # die letzten Tage immer neu, ältere Stunden sind endgültig

def aggregate(c=None, full=False, verbose=True):
    """Einmal über die Zeitachse, Aggregate schreiben.

    Die Rohtabelle enthält nur Änderungen. Hier wird die Treppenfunktion genau
    einmal rekonstruiert und stündlich verdichtet — danach lesen alle
    Auswertungen nur noch fertige Zahlen.
    """
    own = c is None
    if own:
        c = db()
    t_start = time.time()

    first = c.execute("SELECT MIN(ts) t FROM prices").fetchone()["t"]
    if not first:
        if own: c.close()
        return 0

    if full:
        c.execute("DELETE FROM agg_hourly")
        c.execute("DELETE FROM agg_station_day")
        c.execute("DELETE FROM agg_hour_delta")
        start = datetime.fromisoformat(first).replace(minute=0, second=0, microsecond=0)
    else:
        # Die letzten Tage sind noch in Bewegung (Tagesmittel wächst), also neu.
        start = (datetime.now(TZ) - timedelta(days=AGG_REDO_DAYS)).replace(
            minute=0, second=0, microsecond=0)
        f0 = datetime.fromisoformat(first).replace(minute=0, second=0, microsecond=0)
        if start < f0:
            start = f0
        cut = start.isoformat(timespec="minutes")
        c.execute("DELETE FROM agg_hourly WHERE hour >= ?", (cut[:13],))
        c.execute("DELETE FROM agg_station_day WHERE day >= ?", (cut[:10],))
        c.execute("DELETE FROM agg_hour_delta WHERE day >= ?", (cut[:10],))

    regs = {r["id"]: _regions_of(r) for r in
            c.execute("SELECT id, bl, local FROM stations")}
    end = datetime.now(TZ).replace(minute=0, second=0, microsecond=0)
    cutoff = start.isoformat(timespec="seconds")

    n_hours = 0
    for fuel in FUELS:
        # Ausgangszustand: letzter bekannter Preis je Station VOR dem Startzeitpunkt
        cur = {}
        for r in c.execute("""SELECT p.station_id s, p.amount a FROM prices p
              JOIN (SELECT station_id, MAX(ts) m FROM prices
                    WHERE fuel=? AND ts < ? GROUP BY station_id) x
                ON x.station_id=p.station_id AND x.m=p.ts WHERE p.fuel=?""",
              (fuel, cutoff, fuel)):
            cur[r["s"]] = r["a"]

        rows = c.execute("SELECT station_id, amount, ts FROM prices "
                         "WHERE fuel=? AND ts >= ? ORDER BY ts", (fuel, cutoff)).fetchall()
        if not rows and not cur:
            continue
        i = 0
        # je Station und Tag mitschreiben, für Tagesmittel und Mittagssprung
        sday = {}
        hourvals = {}
        t = start
        while t <= end:
            key = t.isoformat(timespec="minutes")
            while i < len(rows) and rows[i]["ts"] <= key[:16] + ":59":
                r = rows[i]
                if r["ts"][:13] <= key[:13]:
                    cur[r["station_id"]] = r["amount"]
                    i += 1
                else:
                    break
            if cur:
                buckets = {}
                for sid, v in cur.items():
                    for rg in regs.get(sid, ["AT"]):
                        buckets.setdefault(rg, []).append(v)
                    d = sday.setdefault((sid, t.date().isoformat()), [])
                    d.append(v)
                    hourvals.setdefault((sid, t.date().isoformat(), t.hour), []).append(v)
                for rg, vals in buckets.items():
                    vals.sort()
                    c.execute("""INSERT OR REPLACE INTO agg_hourly
                        (fuel,region,hour,n,mn,p25,med,avg,p75,mx) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (fuel, rg, key[:13], len(vals), vals[0],
                         round(_pct(vals, .25), 4), round(_pct(vals, .5), 4),
                         round(sum(vals)/len(vals), 4), round(_pct(vals, .75), 4), vals[-1]))
                n_hours += 1
            t += timedelta(hours=1)

        # Tageswerte je Station, inklusive der beiden Mittagsmessungen
        for (sid, day), vals in sday.items():
            pre = _price_at(c, sid, fuel, f"{day}T11:59:00")
            post = _price_at(c, sid, fuel, f"{day}T12:20:00")
            c.execute("""INSERT OR REPLACE INTO agg_station_day
                (station_id,fuel,day,n,avg,mn,mx,noon_pre,noon_post) VALUES (?,?,?,?,?,?,?,?,?)""",
                (sid, fuel, day, len(vals), round(sum(vals)/len(vals), 4),
                 min(vals), max(vals), pre, post))

        # Stundenabweichung vom Tagesmittel derselben Station, je Gebiet gebündelt
        dmean = {k: sum(v)/len(v) for k, v in sday.items()}
        acc = {}
        for (sid, day, hh), vals in hourvals.items():
            base = dmean.get((sid, day))
            if base is None:
                continue
            delta = sum(vals)/len(vals) - base
            for rg in regs.get(sid, ["AT"]):
                a = acc.setdefault((rg, day, hh), [0.0, 0])
                a[0] += delta; a[1] += 1
        for (rg, day, hh), (sd, n) in acc.items():
            c.execute("""INSERT OR REPLACE INTO agg_hour_delta
                (fuel,region,day,hour,sum_delta,n) VALUES (?,?,?,?,?,?)""",
                (fuel, rg, day, hh, round(sd, 6), n))

    # latest neu aufbauen, falls Zeilen nachträglich entfernt wurden
    if full:
        c.execute("DELETE FROM latest")
        c.execute("""INSERT INTO latest (station_id,fuel,amount,ts)
            SELECT p.station_id, p.fuel, p.amount, p.ts FROM prices p
            JOIN (SELECT station_id, fuel, MAX(ts) m FROM prices GROUP BY station_id, fuel) x
              ON x.station_id=p.station_id AND x.fuel=p.fuel AND x.m=p.ts""")
    # Kennzahlen einmal berechnen statt bei jeder Anfrage
    sp = c.execute("SELECT MIN(ts) a, MAX(ts) b, COUNT(*) n FROM prices").fetchone()
    cov = {"stations": c.execute("SELECT COUNT(*) n FROM stations").fetchone()["n"], "fuels": {}}
    for f in CONF["fuels"]:
        cov["fuels"][f] = {"priced": c.execute(
            "SELECT COUNT(*) n FROM latest WHERE fuel=?", (f,)).fetchone()["n"]}
        cov["fuels"][f]["pct"] = round(cov["fuels"][f]["priced"]/max(cov["stations"], 1)*100, 1)
    pr = c.execute("SELECT COUNT(DISTINCT ts) n, MIN(ts) a, MAX(ts) b FROM polls").fetchone()
    cov["polls"] = {"n": pr["n"], "first": pr["a"], "last": pr["b"]}
    c.execute("INSERT OR REPLACE INTO agg_meta (k,v) VALUES ('span', ?)",
              (json.dumps({"from": sp["a"], "to": sp["b"], "points": sp["n"]}),))
    c.execute("INSERT OR REPLACE INTO agg_meta (k,v) VALUES ('coverage', ?)", (json.dumps(cov),))
    c.execute("INSERT OR REPLACE INTO agg_meta (k,v) VALUES ('last_run', ?)", (now(),))
    c.commit()
    dt = time.time() - t_start
    if verbose:
        print(f"  Aggregate: {n_hours} Stundenwerte in {dt:.1f}s"
              f"{' (Vollaufbau)' if full else ''}")
    if own:
        c.close()
    return n_hours

def _price_at(c, sid, fuel, when):
    r = c.execute("SELECT amount FROM prices WHERE station_id=? AND fuel=? "
                  "AND substr(ts,1,19)<=? ORDER BY ts DESC LIMIT 1",
                  (sid, fuel, when)).fetchone()
    return r["amount"] if r else None

# ------------------------------------------------------------------ STATISTIK
BRANDS = [("OMV","OMV"),("SHELL","Shell"),("BP","BP"),("JET","JET"),("ENI","Eni"),
          ("AVANTI","Avanti"),("TURMÖL","Turmöl"),("DISKONT","Diskont"),("DISK","Diskont"),
          ("HOFER","Diskont"),("M3","M3"),("AVIA","Avia"),("OIL!","OIL!"),("ESW","ESW"),
          ("TK ","TK"),("GUTMANN","Gutmann")]

def brand_of(name):
    n = (name or "").upper()
    for key, label in BRANDS:
        if key in n:
            return label
    return "sonstige"

def local_ids(c, km=None):
    """IDs der Stationen im Umkreis — für die Umschaltung Österreich/Umkreis."""
    km = km or CONF.get("app_km", 35)
    h = CONF["home"]
    return [r["id"] for r in c.execute("SELECT id, lat, lon FROM stations WHERE lat IS NOT NULL")
            if haversine(h["lat"], h["lon"], r["lat"], r["lon"]) <= km]

def _idclause(ids):
    """SQL-Fragment plus Parameter; ohne Filter leer."""
    if ids is None:
        return "", []
    return " AND station_id IN (%s)" % ",".join("?" * len(ids)), list(ids)

def _region(scope):
    return "LOCAL" if scope == "local" else ("AT" if scope in (None, "at") else scope)

def _steps(c, fuel, days, region="AT", step_h=None):
    """Marktverlauf aus agg_hourly — eine indizierte Bereichsabfrage."""
    since = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%dT%H")
    rows = c.execute("""SELECT hour, n, mn, p25, med, avg, p75, mx FROM agg_hourly
                        WHERE fuel=? AND region=? AND hour>=? ORDER BY hour""",
                     (fuel, region, since)).fetchall()
    if not rows:
        return []
    if step_h is None:
        span_h = len(rows)
        step_h = 1 if span_h <= 24*7 else (3 if span_h <= 24*31 else (6 if span_h <= 24*90 else 24))
    out = []
    for k in range(0, len(rows), step_h):
        r = rows[k]
        out.append({"t": r["hour"] + ":00", "n": r["n"], "min": r["mn"], "max": r["mx"],
                    "avg": r["avg"], "med": r["med"], "p25": r["p25"]})
    return out

def hour_profile(c, fuel, days=60, region="AT"):
    """Stundenabweichung aus agg_hour_delta — je Station normiert, vorberechnet."""
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    rows = c.execute("""SELECT hour, SUM(sum_delta) sd, SUM(n) n FROM agg_hour_delta
                        WHERE fuel=? AND region=? AND day>=? GROUP BY hour ORDER BY hour""",
                     (fuel, region, since)).fetchall()
    return {r["hour"]: {"delta_ct": round(r["sd"]/r["n"]*100, 2), "n": r["n"]}
            for r in rows if r["n"]}

def weekday_profile(c, fuel, days=90, region="AT"):
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    names = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    acc = {}
    for r in c.execute("""SELECT day, SUM(sum_delta) sd, SUM(n) n FROM agg_hour_delta
                          WHERE fuel=? AND region=? AND day>=? GROUP BY day""",
                       (fuel, region, since)):
        try: wd = datetime.fromisoformat(r["day"]).weekday()
        except ValueError: continue
        a = acc.setdefault(wd, [0.0, 0]); a[0] += r["sd"]; a[1] += r["n"]
    return {names[k]: {"delta_ct": round(v[0]/v[1]*100, 2), "n": v[1]}
            for k, v in sorted(acc.items()) if v[1]}

def cycle(c, fuel, days=30, region="AT"):
    """Sägezahn: Stunden nach dem Mittagssprung."""
    hp = hour_profile(c, fuel, days, region)
    return [{"h": (h - 12) % 24, "delta_ct": d["delta_ct"], "n": d["n"]}
            for h, d in sorted(hp.items(), key=lambda kv: (kv[0] - 12) % 24)]

def noon_jump(c, fuel, days=30, region="AT"):
    """Mittagssprung aus agg_station_day — die beiden Messungen liegen dort schon."""
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    cl, pa = ("", [])
    if region != "AT":
        cl = (" AND station_id IN (SELECT id FROM stations WHERE local=1)" if region == "LOCAL"
              else " AND station_id IN (SELECT id FROM stations WHERE bl=?)")
        if region.startswith("BL:"):
            pa = [int(region[3:])]
    # Median, Mittel und die Zählungen in einer Abfrage — statt 50.000 Zeilen
    # nach Python zu holen kommen so rund 30 zurück.
    rows = c.execute(f"""
      WITH d AS (
        SELECT day, (noon_post - noon_pre) v,
               ROW_NUMBER() OVER (PARTITION BY day ORDER BY (noon_post - noon_pre)) rn,
               COUNT(*)    OVER (PARTITION BY day) c
        FROM agg_station_day
        WHERE fuel=? AND day>=? AND noon_pre IS NOT NULL AND noon_post IS NOT NULL{cl}
      )
      SELECT day, c n,
             AVG(v) FILTER (WHERE rn IN ((c+1)/2, (c+2)/2)) med,
             AVG(v) mean,
             SUM(CASE WHEN v >  0.0005 THEN 1 ELSE 0 END) up,
             SUM(CASE WHEN v < -0.0005 THEN 1 ELSE 0 END) dn
      FROM d GROUP BY day ORDER BY day""", (fuel, since, *pa)).fetchall()
    nd = datetime.now(TZ)
    today = nd.date().isoformat()
    out = []
    for r in rows:
        if r["n"] < 5:
            continue
        # Der heutige Tag zählt erst, wenn der Mittag vorbei ist — sonst stehen
        # dort lauter Nullen, weil vor 12:00 beide Messungen gleich sind.
        if r["day"] == today and nd.hour < 13:
            continue
        out.append({"date": r["day"], "n": r["n"],
                    "median_ct": round((r["med"] or 0)*100, 2),
                    "mean_ct": round((r["mean"] or 0)*100, 2),
                    "up": r["up"], "down": r["dn"]})
    return out

def trend(c, fuel, days=30, region="AT"):
    since = (datetime.now(TZ) - timedelta(days=days)).strftime("%Y-%m-%dT%H")
    return [[r["hour"], r["avg"], r["n"]] for r in c.execute(
        "SELECT hour, avg, n FROM agg_hourly WHERE fuel=? AND region=? AND hour>=? ORDER BY hour",
        (fuel, region, since))]

def _station_filter(region):
    if region == "LOCAL":
        return " AND s.local=1", []
    if region.startswith("BL:"):
        return " AND s.bl=?", [int(region[3:])]
    return "", []

def leaders(c, fuel, days=30, top=10, region="AT"):
    """Wie oft war eine Station in ihrem Gebiet die günstigste — aus Tageswerten."""
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    cl, pa = _station_filter(region)
    # Tagesbester per Fensterfunktion, dann zählen — beides in SQLite
    rows = c.execute(f"""
        WITH d AS (
          SELECT d.station_id sid, d.day, d.mn,
                 MIN(d.mn) OVER (PARTITION BY d.day) best
          FROM agg_station_day d JOIN stations s ON s.id=d.station_id
          WHERE d.fuel=? AND d.day>=?{cl}
        ), t AS (SELECT COUNT(DISTINCT day) n FROM d)
        SELECT d.sid, s.name, s.city, COUNT(*) wins, (SELECT n FROM t) days
        FROM d JOIN stations s ON s.id=d.sid
        WHERE d.mn <= d.best + 1e-9
        GROUP BY d.sid ORDER BY wins DESC LIMIT ?""",
        (fuel, since, *pa, top)).fetchall()
    return [{"id": r["sid"], "name": r["name"], "city": r["city"], "wins": r["wins"],
             "pct": round(r["wins"]/max(r["days"], 1)*100, 1)} for r in rows]

def by_brand(c, fuel, days=30, region="AT"):
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    cl, pa = _station_filter(region)
    # Marke steht als Spalte an der Station, daher reicht ein GROUP BY
    return [{"brand": r["brand"], "n": r["n"], "avg": round(r["av"], 4),
             "min": round(r["mn"], 4), "max": round(r["mx"], 4)}
            for r in c.execute(f"""
              SELECT COALESCE(s.brand,'sonstige') brand, COUNT(*) n,
                     AVG(d.avg) av, MIN(d.mn) mn, MAX(d.mx) mx
              FROM agg_station_day d JOIN stations s ON s.id=d.station_id
              WHERE d.fuel=? AND d.day>=?{cl}
              GROUP BY COALESCE(s.brand,'sonstige') HAVING COUNT(*)>=3
              ORDER BY av""", (fuel, since, *pa))]

def volatility(c, fuel, days=30, top=12, region="AT"):
    since = (datetime.now(TZ) - timedelta(days=days)).date().isoformat()
    cl, pa = _station_filter(region)
    rows = c.execute(f"""SELECT d.station_id sid, s.name, s.city,
        SUM(d.n) n, MIN(d.mn) mn, MAX(d.mx) mx, AVG(d.avg) av
        FROM agg_station_day d JOIN stations s ON s.id=d.station_id
        WHERE d.fuel=? AND d.day>=?{cl} GROUP BY d.station_id""",
        (fuel, since, *pa)).fetchall()
    out = [{"id": r["sid"], "name": r["name"], "city": r["city"],
            "changes": r["n"], "sd_ct": 0.0, "span_ct": round((r["mx"] - r["mn"])*100, 2)}
           for r in rows if r["n"] and r["mx"] is not None]
    return sorted(out, key=lambda x: -x["span_ct"])[:top]

def spread_now(c, fuel, region="AT"):
    cl, pa = _station_filter(region)
    rows = [r["amount"] for r in c.execute(
        f"SELECT l.amount FROM latest l JOIN stations s ON s.id=l.station_id "
        f"WHERE l.fuel=?{cl}", (fuel, *pa))]
    if not rows: return None
    rows.sort(); n = len(rows)
    return {"n": n, "min": round(rows[0], 4), "max": round(rows[-1], 4),
            "avg": round(sum(rows)/n, 4),
            "med": round(rows[n//2] if n % 2 else (rows[n//2-1]+rows[n//2])/2, 4),
            "spread_ct": round((rows[-1]-rows[0])*100, 2),
            "hist": rows}

_analysis_cache = {}

def build_analysis(days=30, scope="at"):
    days = max(1, min(int(days), 3650))
    rg = _region(scope)
    c = db()
    stamp = (c.execute("SELECT v FROM agg_meta WHERE k='last_run'").fetchone() or {"v": ""})["v"]
    key = (days, rg, stamp)
    hit = _analysis_cache.get(key)
    if hit is not None:
        c.close()
        return hit
    sp = data_span(c); lbl, lvl = confidence(sp)
    n = c.execute("SELECT COUNT(*) n FROM stations" +
                  (" WHERE local=1" if rg == "LOCAL" else
                   " WHERE bl=?" if rg.startswith("BL:") else ""),
                  ([int(rg[3:])] if rg.startswith("BL:") else [])).fetchone()["n"]
    out = {"generated": now(), "days": days, "scope": scope, "region": rg, "scope_n": n,
           "span": sp, "confidence": {"label": lbl, "level": lvl},
           "agg_last": (c.execute("SELECT v FROM agg_meta WHERE k='last_run'").fetchone() or {"v": None})["v"],
           "coverage": coverage(c), "regions": BL_NAMES, "fuels": {}}
    for f in CONF["fuels"]:
        out["fuels"][f] = {
            "hour": hour_profile(c, f, days, rg), "weekday": weekday_profile(c, f, days, rg),
            "noon": noon_jump(c, f, days, rg), "trend": trend(c, f, days, rg),
            "series": _steps(c, f, days, rg), "leaders": leaders(c, f, days, 10, rg),
            "brands": by_brand(c, f, days, rg), "volatility": volatility(c, f, days, 12, rg),
            "cycle": cycle(c, f, days, rg), "spread": spread_now(c, f, rg)}
    c.close()
    if len(_analysis_cache) > 60:
        _analysis_cache.clear()
    _analysis_cache[key] = out
    return out

def build_history(sid, fuel):
    c = db()
    rows = c.execute("SELECT ts, amount FROM prices WHERE station_id=? AND fuel=? ORDER BY ts",
                     (sid, fuel)).fetchall()
    c.close()
    return [[r["ts"], r["amount"]] for r in rows]

def cmd_export(args):
    snap = build_snapshot()
    p = os.path.join(DATA, "snapshot.json")
    json.dump(snap, open(p, "w"), ensure_ascii=False, indent=1)
    print(f"geschrieben: {p}")
    for f, d in snap["fuels"].items():
        print(f"  {f}: {len(d['stations'])} Stationen mit Preis")


# --------------------------------------------------------------- ABTASTPLAN
# Rechtlicher Rahmen (E-Control-FAQ):
#   • Preiserhöhungen sind NUR um 12:00 erlaubt und bis 12:10 zu melden.
#   • Alle übrigen Änderungen (= Senkungen) müssen binnen 30 min gemeldet sein.
# Daraus folgt: den Tagessprung braucht man exakt (11:55 / 12:15), den langsamen
# Abwärtsdrift danach reicht stündlich. Nachts passiert praktisch nichts.
SLOTS = ([(h, 0) for h in range(6, 23) if h != 12]  # stündlich 06:00–22:00
         + [(11, 55), (12, 15)]              # der Sprung, eng eingefasst (12:00 entfällt)
         + [(3, 0)])                           # Tiefenlauf: neue Stationen suchen
DEEP_SLOT = (3, 0)

def next_slot(ref=None):
    """Nächster Abtastzeitpunkt und ob es ein Tiefenlauf ist."""
    ref = ref or datetime.now(TZ)
    cands = []
    for d in (0, 1):
        base = (ref + timedelta(days=d)).replace(second=0, microsecond=0)
        for hh, mm in SLOTS:
            t = base.replace(hour=hh, minute=mm)
            if t > ref:
                cands.append(t)
    t = min(cands)
    return t, (t.hour, t.minute) == DEEP_SLOT

# ------------------------------------------------------------------------ WATCH
def cmd_watch(args):
    if args.every:
        print(f"Fester Takt: alle {args.every} Minuten. Ctrl+C beendet.")
        while True:
            try: collect()
            except KeyboardInterrupt: print("\nbeendet."); return
            except Exception as e: print("Fehler:", e, file=sys.stderr)
            time.sleep(args.every * 60)

    print("Abtastplan nach den gesetzlichen Meldefristen. Ctrl+C beendet.")
    print(f"  Slots: {len(SLOTS)}/Tag · Sprung um 12:00 eng eingefasst (11:55 / 12:15)")
    while True:
        t, deep = next_slot()
        wait = (t - datetime.now(TZ)).total_seconds()
        print(f"  nächster Lauf {t.strftime('%a %H:%M')}"
              f"{' [Tiefenlauf]' if deep else ''} — in {wait/60:.0f} min")
        try:
            time.sleep(max(wait, 1))
            collect(deep=deep)
        except KeyboardInterrupt: print("\nbeendet."); return
        except Exception as e: print("Fehler:", e, file=sys.stderr); time.sleep(60)

def cmd_collect(args): collect(deep=args.deep)

# ------------------------------------------------------------------------ SERVE
def cmd_serve(args):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    WEB = os.path.join(ROOT, "web")
    TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
             ".png": "image/png", ".json": "application/json; charset=utf-8",
             ".webmanifest": "application/manifest+json; charset=utf-8",
             ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8"}

    class H(BaseHTTPRequestHandler):
        server_version = "tankradar"
        def log_message(self, *a): pass

        def _send(self, code, body, ctype, cache=None):
            b = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            # Die App darf von einem anderen Origin geladen werden (Pages/Tunnel).
            self.send_header("Access-Control-Allow-Origin", "*")
            if cache: self.send_header("Cache-Control", cache)
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(b)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False),
                       "application/json; charset=utf-8", "no-cache")

        def _static(self, rel):
            # Pfad-Ausbruch verhindern
            full = os.path.realpath(os.path.join(WEB, rel.lstrip("/")))
            if not full.startswith(os.path.realpath(WEB)) or not os.path.isfile(full):
                return False
            ext = os.path.splitext(full)[1]
            # Der Service Worker darf nie aus dem Cache kommen, sonst frieren Updates ein.
            cache = "no-cache" if os.path.basename(full) == "sw.js" else "max-age=3600"
            self._send(200, open(full, "rb").read(),
                       TYPES.get(ext, "application/octet-stream"), cache)
            return True

        def _client(self):
            # Hinter Traefik ist die Peer-Adresse der Proxy — die echte steht im Header.
            fwd = self.headers.get("X-Forwarded-For", "")
            return fwd.split(",")[0].strip() if fwd else self.client_address[0]

        def do_HEAD(self): self.do_GET()
        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            path, q = u.path, urllib.parse.parse_qs(u.query)
            try:
                # Die App ist eine Einzelseite; /dashboard muss auch beim direkten
                # Aufruf und beim Neuladen dieselbe Datei ausliefern.
                if path.rstrip("/") in ("", "/index.html", "/dashboard"):
                    return self._static("index.html")
                if path == "/healthz":
                    c = db()
                    last = c.execute("SELECT MAX(ts) t FROM polls").fetchone()["t"]
                    c.close()
                    age = ((datetime.now(TZ) - datetime.fromisoformat(last)).total_seconds()
                           if last else None)
                    ok = age is not None and age < 6 * 3600
                    return self._json({"ok": ok, "last_poll": last,
                                       "age_s": round(age) if age else None},
                                      200 if ok else 503)
                if path == "/api/stations":
                    try: rk = float(q["radius"][0]) if "radius" in q else CONF.get("app_km")
                    except ValueError: rk = CONF.get("app_km")
                    return self._json(build_stations(rk))
                if path == "/api/analysis":
                    try: dd = int(q.get("days", ["30"])[0])
                    except ValueError: dd = 30
                    sc = q.get("scope", ["at"])[0]
                    if sc not in ("at", "local") and not (
                            sc.startswith("BL:") and sc[3:].isdigit() and 1 <= int(sc[3:]) <= 9):
                        sc = "at"
                    return self._json(build_analysis(dd, sc))
                if path == "/api/snapshot":  return self._json(build_snapshot())
                if path == "/api/history":
                    sid = int(q.get("id", ["0"])[0]); f = q.get("fuel", ["DIE"])[0]
                    return self._json({"id": sid, "fuel": f, "points": build_history(sid, f)})
                if path == "/api/route":
                    if not rate_ok(self._client()):
                        return self._json({"error": "zu viele Anfragen"}, 429)
                    try:
                        la = float(q["lat"][0]); lo = float(q["lon"][0])
                    except Exception:
                        return self._json({"error": "lat und lon erforderlich"}, 400)
                    if not (-90 <= la <= 90 and -180 <= lo <= 180):
                        return self._json({"error": "Koordinaten außerhalb des gültigen Bereichs"}, 400)
                    try: rk = float(q["radius"][0]) if "radius" in q else None
                    except ValueError: rk = None
                    return self._json(build_route(la, lo, q.get("fuel", ["DIE"])[0], rk))
                if path == "/api/collect":
                    if ADMIN_TOKEN:
                        given = (self.headers.get("Authorization", "")
                                 .removeprefix("Bearer ").strip()) or q.get("token", [""])[0]
                        # Vergleich in konstanter Zeit, damit das Token nicht
                        # zeichenweise erraten werden kann
                        import hmac
                        if not hmac.compare_digest(given, ADMIN_TOKEN):
                            return self._json({"error": "nicht berechtigt"}, 403)
                    elif PUBLIC:
                        return self._json({"error": "im öffentlichen Betrieb deaktiviert"}, 403)
                    return self._json({"new": collect(verbose=False)})
                if self._static(path): return
                self._send(404, "not found", "text/plain; charset=utf-8")
            except BrokenPipeError:
                pass
            except Exception as e:
                self._json({"error": str(e)}, 500)

    srv = ThreadingHTTPServer((args.host, args.port), H)
    print(f"UI läuft auf  http://{args.host}:{args.port}   (Ctrl+C beendet)", flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: print("\nbeendet.")

def cmd_run(args):
    """Ein Prozess, zwei Aufgaben — der Container-Einstiegspunkt."""
    import threading
    def loop():
        if not data_span(db()):
            print("Erstbefüllung …", flush=True)
            try: collect(deep=True)
            except Exception as e: print("Erstlauf fehlgeschlagen:", e, file=sys.stderr, flush=True)
        while True:
            t, deep = next_slot()
            wait = (t - datetime.now(TZ)).total_seconds()
            print(f"nächster Lauf {t:%a %d.%m. %H:%M}{' [tief]' if deep else ''} "
                  f"(in {wait/60:.0f} min)", flush=True)
            try:
                time.sleep(max(wait, 1)); collect(deep=deep)
            except Exception as e:
                print("Sammelfehler:", e, file=sys.stderr, flush=True); time.sleep(120)
    threading.Thread(target=loop, daemon=True, name="collector").start()
    cmd_serve(args)

# ------------------------------------------------------------------------- MAIN
def main():
    ap = argparse.ArgumentParser(description="Spritpreis-Tracker Österreich (E-Control)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cp = sub.add_parser("collect"); cp.add_argument("--deep", action="store_true",
    help="zusätzlich auf jede bekannte Station zentriert abfragen"); cp.set_defaults(fn=cmd_collect)
    w = sub.add_parser("watch"); w.add_argument("--every", type=int, default=None,
    help="fester Takt in Minuten statt des Abtastplans"); w.set_defaults(fn=cmd_watch)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    sub.add_parser("geofix", help="Koordinaten gegen OpenStreetMap korrigieren")\
       .set_defaults(fn=lambda a: correct_coordinates())
    ag = sub.add_parser("aggregate", help="Aggregate neu berechnen")
    ag.add_argument("--full", action="store_true", help="alles von vorn statt nur die letzten Tage")
    ag.set_defaults(fn=lambda a: (assign_regions(db(), verbose=True), aggregate(full=a.full)))
    sub.add_parser("export").set_defaults(fn=cmd_export)
    sv = sub.add_parser("serve"); sv.add_argument("--port", type=int, default=842)
    sv.add_argument("--host", default="127.0.0.1"); sv.set_defaults(fn=cmd_serve)
    rn = sub.add_parser("run", help="Sammler + UI in einem Prozess (für Docker)")
    rn.add_argument("--port", type=int, default=842); rn.add_argument("--host", default="0.0.0.0")
    rn.set_defaults(fn=cmd_run)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
