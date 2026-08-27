# tools

## smoke.sh
Prüft `web/app.js` vor dem Deploy:

1. Syntax (`node --check`)
2. Ob alle erwarteten Funktionen noch existieren
3. Ob die App in einer DOM-Attrappe startet, für `/` und `/dashboard`

Hintergrund: Beim Umbauen per Textersetzung wurde einmal ein ganzer Abschnitt
mitgelöscht (`prefs`, `cfg`, `total`, die Formatierungshelfer). Syntaktisch war
die Datei danach fehlerfrei, sie warf erst zur Laufzeit — die Seite blieb leer.
Punkt 2 und 3 fangen genau das ab.

    bash tools/smoke.sh

## harness.js
Die DOM-Attrappe. Lädt `web/app.js` mit gestubbtem document, fetch und
localStorage und meldet Ausnahmen. Braucht `/tmp/st.json`, `/tmp/an.json`
und `/tmp/di.json` — Antworten von `/api/stations`, `/api/analysis` und
`/discounts.json`.
