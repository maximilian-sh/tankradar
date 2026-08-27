# Tankradar

Verfolgt Tankstellenpreise in Österreich über die Zeit und wertet sie aus.

Die offizielle Quelle — der [Spritpreisrechner](https://www.spritpreisrechner.at)
der E-Control — zeigt immer nur *jetzt*. Es gibt keine öffentliche Historie:
die Bundeswettbewerbsbehörde gibt ihre Mikrodaten ausdrücklich nicht heraus,
auf data.gv.at liegt nichts. Historie lässt sich auch nicht nachträglich
erheben. Genau die sammelt dieses Projekt.

Dazu eine PWA, die anzeigt, wo gerade am günstigsten getankt wird — nicht nach
Literpreis, sondern nach dem, was eine Tankfüllung inklusive Umweg wirklich
kostet.

## Was bei der Recherche herauskam

Alles gemessen, nicht aus Dokumentation übernommen:

- **Eine Abfrage liefert nie alle Preise.** Adress- und Bezirksabfragen geben
  die fünf günstigsten Stationen zurück, Bundeslandabfragen die zehn
  günstigsten. Der Rest kommt mit `prices: []`.
- **Das ist eine harte Decke, keine Frage der Abfragedichte.** Auch eine
  Abfrage exakt auf den Koordinaten einer Tankstelle (Distanz 0,0 km) fördert
  deren Preis nicht zutage, wenn fünf günstigere in Reichweite sind.
- **Über Zeit löst sich das trotzdem auf.** Weil Preise sich bewegen, rutscht
  fast jede Station irgendwann in die Top 5. Nach zwei Tagen lagen für 85 %
  der bekannten Stationen Preise vor.
- **`/regions/units` hat eine undokumentierte dritte Ebene** mit allen 2231
  Gemeinden samt amtlichen Koordinaten. Damit braucht der Sammler kein
  handgepflegtes Koordinatenraster.
- **Preiserhöhungen sind nur um 12:00 erlaubt** und bis 12:10 meldepflichtig,
  alle übrigen Änderungen binnen 30 Minuten. Daraus folgt der Abtastplan.
- **Die Daten enthalten Fehler.** Eine Wiener Station meldet 14,00 €/l Diesel.
  Ohne Plausibilitätsfilter verzerrt das jeden Durchschnitt.
- **Die Koordinaten stimmen oft nicht.** Bis zu 368 m Abweichung gemessen; beim
  Navigieren landet man dann auf dem Nachbargrundstück.

## Schnellstart

Kein Paket, keine Abhängigkeiten — reine Standardbibliothek.

    python3 tankradar.py collect     # einmal sammeln
    python3 tankradar.py serve       # UI auf 127.0.0.1:842
    python3 tankradar.py stats       # Auswertung im Terminal

Oder als Container, Daten landen in `./data`:

    docker compose up -d --build

`TZ=Europe/Vienna` und `tzdata` im Image sind **nicht optional**: Der gesamte
Abtastplan hängt an 12:00 Wiener Zeit. Ein fester UTC-Offset läge ab der
Winterzeitumstellung eine Stunde daneben und verfehlte den Mittagssprung.

## Konfiguration

In `tankradar.py`, Dict `CONF`:

| Schlüssel | Bedeutung |
|---|---|
| `home` | Bezugspunkt für Empfehlung und Umkreis-Auswertung |
| `radius_km` | wie weit um `home` Gemeindezentren abgefragt werden |
| `app_km` | was die App höchstens anzeigt |
| `national` | zusätzlich alle Bundesländer und Bezirke abfragen |
| `fuels` | `DIE`, `SUP`, `GAS` |

Die Abfragepunkte ergeben sich aus der amtlichen Gemeindeliste — kein Raster
zu pflegen, funktioniert überall in Österreich.

## Abtastplan

Folgt den gesetzlichen Meldefristen statt einem festen Takt:

- **11:55 und 12:15** klammern den einzigen erlaubten Preissprung des Tages ein
- **stündlich 06:00–22:00** für den Abwärtsdrift danach
- **03:00** Tiefenlauf: sucht neue Stationen und gleicht Koordinaten gegen
  OpenStreetMap ab

## Aufbau

    tankradar.py          Sammler, Auswertung und HTTP-Server in einer Datei
    web/               PWA: mobile Empfehlung, Desktop-Dashboard
    web/discounts.json Kartenrabatte, von Hand gepflegt
    research/          die Messungen, aus denen die Befunde oben stammen
    tools/smoke.sh     prüft die App vor dem Ausliefern

### Datenmodell
`prices` speichert nur **Änderungen**, nicht jede Messung — die Datenbank
bleibt dadurch auch nach Jahren klein. Daraus rekonstruiert `aggregate()`
einmal die Treppenfunktion und legt Stunden-, Tages- und Gebietswerte ab.
Alle Auswertungen lesen nur noch diese Aggregate.

Bei 900.000 Zeilen gemessen: Auswertung kalt 20–2028 ms, warm 1–2 ms;
inkrementelle Aggregation 3,2 s, Vollaufbau 66 s.

## Mitmachen

Besonders willkommen:

- **`web/discounts.json`** — Kartenrabatte kennt jeder für seine eigene Region.
  Bitte mit Quelle und Datum, und nur was auf Diesel, Super 95 oder CNG wirkt.
  Rabatte auf Premium-Sorten trifft dieses Projekt nicht, weil die E-Control
  sie gar nicht erfasst.
- **Regionen außerhalb Vorarlbergs** — der Sammler kann ganz Österreich, die
  Erfahrung damit fehlt noch.
- **Auswertungen**, die aus der Historie etwas Neues zeigen.

Vor einem Pull Request bitte `bash tools/smoke.sh` laufen lassen.

## Lizenz

[PolyForm Noncommercial 1.0.0](LICENSE) — frei für private, wissenschaftliche
und andere nicht-kommerzielle Nutzung. Für kommerzielle Nutzung bitte vorher
Kontakt aufnehmen.

Das ist bewusst **keine** Open-Source-Lizenz im Sinne der OSI, sondern
quelloffen mit Einschränkung. Wer beiträgt, sollte wissen: Beiträge stehen
unter derselben Lizenz, kommerzielle Verwertung bleibt beim Projektinhaber.

## Daten und Fremdbestandteile

Preisdaten aus der Preistransparenzdatenbank der E-Control
([spritpreisrechner.at](https://www.spritpreisrechner.at)) und nicht Teil
dieses Repositories. Routing über [OSRM](https://project-osrm.org),
Kartendaten und Koordinatenkorrektur über
[OpenStreetMap](https://www.openstreetmap.org/copyright) (ODbL),
Icons von [Lucide](https://lucide.dev) (ISC),
Karte mit [Leaflet](https://leafletjs.com) (BSD-2-Clause).

Preise können sich jederzeit ändern; es gilt der Aushang an der Tankstelle.
Ohne Gewähr.
