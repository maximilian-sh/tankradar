# Messungen zur E-Control-API

Die Skripte, mit denen die Grenzen der Datenquelle ermittelt wurden.
Sie sind nicht Teil des Tools, dokumentieren aber, woher die Zahlen im
Haupt-README stammen.

- `coverage_probe.py` — Rasterabfrage über das Rheintal, misst wie viele
  Stationen überhaupt je einen Preis liefern (Ergebnis: ~71 %).
- `strategy_test.py` — vergleicht drei Abfragestrategien gegeneinander:
  Koordinatenraster, Gemeindezentren, stationszentriert.

Kernergebnis: Der 5-günstigste-Cap der API ist eine harte Decke. Auch eine
Abfrage aus 0 m Entfernung fördert die Preise von OMV, Shell, Eni oder BP
nicht zutage. Gemeindezentren als Abfragepunkte liefern dieselbe Abdeckung
bei einem Drittel der Abfragen.
