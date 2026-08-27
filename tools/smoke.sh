set -e
cd "$(dirname "$0")/.."   # Projektwurzel, unabhängig vom Ablageort
node --check web/app.js
node -e '
const s=require("fs").readFileSync("web/app.js","utf8");
const need=["const e3","const e2","const km =","const ico","const esc","function prefs","function cfg",
 "const dist","function total","function haversine","function brandOf","function ruleAmount",
 "function discountFor","function effPrice","function ranked","function render(","function renderNow(",
 "function renderDash(","function renderCards(","function drawMap(","function drawBand(","function drawHisto(",
 "function drawCycle(","function drawLeaders(","function drawBrands(","function drawVol(","function drawHours(",
 "function drawWeek(","function drawNoon(","function drawHistory("];
const miss=need.filter(f=>!s.includes(f));
if(miss.length){console.error("FEHLEND: "+miss.join(", "));process.exit(1);}
console.log("  alle "+need.length+" Bausteine vorhanden");'
node /tmp/harness.js / 2>&1 | grep -q "ohne Ausnahme" || { echo "  Startpfad / wirft"; exit 1; }
node /tmp/harness.js /dashboard 2>&1 | grep -q "ohne Ausnahme" || { echo "  Startpfad /dashboard wirft"; exit 1; }
echo "  beide Startpfade laufen ohne Ausnahme"
