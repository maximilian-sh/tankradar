'use strict';
const L2 = window.L;   // Leaflet, umbenannt: L ist im Code die Stationsliste
const $ = s => document.querySelector(s);
const FUEL_NAMES = { DIE: 'Diesel', SUP: 'Super 95', GAS: 'CNG' };
const LS = 'tankradar.prefs';

const S = {
  fuel: 'DIE', view: 'now', pos: null, posAccuracy: null, posAge: null,
  data: null, analysis: null, sel: null, offline: false, routing: false,
  sort: 'total', asc: true, err: null, locating: false,
  discounts: [], cards: [], cardDefs: [], useDiscount: true,
  map: null, layer: null, days: 7, scope: 'local'
};

/* Rabatte, die nicht in den E-Control-Daten stehen (JET-Kartenrabatt,
   ÖAMTC-Bonus bei Eni). Von Hand gepflegt in /discounts.json. Ein Rabatt zählt
   nur, wenn die Karte auch tatsächlich besessen wird. */
function brandOf(name) {
  const n = (name || '').toUpperCase();
  for (const b of ['OMV','SHELL','BP','JET','ENI','AVANTI','TURMÖL','DISKONT','DISK',
                   'HOFER','M3','AVIA','OIL!','ESW','GUTMANN'])
    if (n.includes(b)) return b === 'DISK' || b === 'HOFER' ? 'DISKONT' : b;
  return '';
}
function ruleAmount(r) {
  // Aktionszeitraum schlägt den Grundbetrag, aber nur solange er läuft
  if (r.ct_aktion && r.aktion_von && r.aktion_bis) {
    const t = new Date().toISOString().slice(0, 10);
    if (t >= r.aktion_von && t <= r.aktion_bis) return r.ct_aktion;
  }
  return r.ct;
}
function discountFor(st) {
  if (!S.useDiscount) return null;
  const b = brandOf(st.n);
  let best = null;
  for (const r of S.discounts) {
    if (String(r.marke).toUpperCase() !== b) continue;
    if (r.braucht && !S.cards.includes(r.braucht)) continue;
    if (r.sorten && !r.sorten.includes(S.fuel)) continue;
    const ct = ruleAmount(r);
    if (!ct) continue;
    if (!best || ct > ruleAmount(best)) best = r;
  }
  return best;
}
function effPrice(st) {
  const raw = st.p[S.fuel][0];
  const d = discountFor(st);
  return d ? raw - ruleAmount(d) / 100 : raw;
}

/* ---------- Formatierung ---------- */
const e3 = v => v.toFixed(3).replace('.', ',');
const e2 = v => v.toFixed(2).replace('.', ',');
const km = v => v == null ? '–' : (v < 10 ? v.toFixed(1) : Math.round(v)) + ' km';
const ico = n => `<svg class="ico"><use href="/sprite.svg#i-${n}"></use></svg>`;
const esc = s => String(s ?? '').replace(/[<>&"]/g, c =>
  ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));

function prefs(p) {
  try {
    if (p) { localStorage.setItem(LS, JSON.stringify(p)); return p; }
    return JSON.parse(localStorage.getItem(LS) || '{}');
  } catch { return {}; }
}
function cfg() {
  return { L: +$('#li').value || 45, C: +$('#co').value || 6.5, D: +$('#de').value || 2 };
}

/* Entfernung: echte Straßenkilometer, wenn vorhanden, sonst Luftlinie */
const dist = s => s.road_km != null ? s.road_km : s.air_km;

/* Echtkosten = Tankfüllung + Sprit für den Umweg */
function total(s, c) {
  const d = dist(s) ?? 0;
  const p = effPrice(s);
  return p * c.L + d * c.D * (c.C / 100) * p;
}

function haversine(a1, o1, a2, o2) {
  const R = 6371, r = Math.PI / 180;
  const dp = (a2 - a1) * r, dl = (o2 - o1) * r;
  const x = Math.sin(dp / 2) ** 2 + Math.cos(a1 * r) * Math.cos(a2 * r) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

/* ---------- Daten ---------- */
async function loadData() {
  const p = S.pos;
  const url = p
    ? `/api/route?lat=${p.lat.toFixed(5)}&lon=${p.lon.toFixed(5)}&fuel=${S.fuel}`
    : '/api/stations';
  try {
    const r = await fetch(url, { cache: 'no-store' });
    const d = await r.json();
    if (d.offline) { S.offline = true; render(); return; }
    S.offline = false;
    S.routing = !!d.routing;
    if (!p) {
      // Ohne Standort: Basis des Servers als Bezugspunkt
      const h = d.home;
      d.stations.forEach(s => { s.air_km = +haversine(h.lat, h.lon, s.lat, s.lon).toFixed(2); });
    }
    S.data = d;
  } catch {
    S.offline = true;
  }
  render();
}

async function loadDiscounts() {
  try {
    const d = await (await fetch('/discounts.json')).json();
    S.discounts = d.regeln || [];
    S.cardDefs = d.karten || [];
  } catch { S.discounts = []; S.cardDefs = []; }
}

async function loadAnalysis() {
  try { S.analysis = await (await fetch('/api/analysis', { cache: 'no-store' })).json(); }
  catch { /* Dashboard bleibt leer, die Hauptansicht funktioniert weiter */ }
  if (S.view === 'dash') render();
}

/* ---------- Standort ---------- */
function locate() {
  if (!navigator.geolocation) {
    S.err = 'Dieser Browser kennt keine Standortbestimmung.'; render(); return;
  }
  if (!window.isSecureContext) {
    S.err = 'Standort braucht HTTPS. Über eine unverschlüsselte Adresse sperrt der Browser das.';
    render(); return;
  }
  S.locating = true; S.err = null; render();
  navigator.geolocation.getCurrentPosition(
    p => {
      S.pos = { lat: p.coords.latitude, lon: p.coords.longitude };
      S.posAccuracy = p.coords.accuracy; S.posAge = Date.now();
      S.locating = false;
      prefs({ ...prefs(), pos: S.pos });
      loadData();
    },
    e => {
      S.locating = false;
      S.err = e.code === 1
        ? 'Standort nicht freigegeben. Die Liste zeigt solange die Umgebung der Serverbasis.'
        : 'Standort nicht ermittelbar. Die Liste zeigt solange die Umgebung der Serverbasis.';
      render();
    },
    { enableHighAccuracy: true, timeout: 10000, maximumAge: 120000 }
  );
}

/* ---------- Sortierte Liste ---------- */
function ranked() {
  if (!S.data) return [];
  const c = cfg();
  return S.data.stations
    .filter(s => s.p && s.p[S.fuel])
    .map(s => ({ ...s, raw: s.p[S.fuel][0], amount: effPrice(s), disc: discountFor(s),
                 ts: s.p[S.fuel][1], km: dist(s), total: total(s, c) }))
    .sort((a, b) => a.total - b.total);
}
function tierColor(v, mn, mx) {
  const t = mx > mn ? (v - mn) / (mx - mn) : 0;
  return t < .34 ? 'var(--good)' : t < .67 ? 'var(--mid)' : 'var(--bad)';
}

/* ---------- Rendern ---------- */
function render() {
  document.body.dataset.view = S.view;
  renderSeg(); renderRange(); renderStatus();
  renderCards();
  if (S.view === 'dash') renderDash(); else renderNow();
}

const RANGES = [[1, '24 h'], [7, '7 Tage'], [30, '30 Tage'], [365, 'Alles']];
function renderRange() {
  const el = $('#rangeseg');
  if (!el) return;
  el.innerHTML = RANGES.map(([d, l]) =>
    `<button data-days="${d}" aria-pressed="${d === S.days}">${l}</button>`).join('');
  el.querySelectorAll('button').forEach(b => b.onclick = async () => {
    S.days = +b.dataset.days;
    prefs({ ...prefs(), days: S.days });
    renderRange();
    await loadAnalysis();
  });
  const sc = $('#scopeseg');
  if (sc) {
    const bl = (S.analysis && S.analysis.regions) || {};
    const opts = [['local', 'Umkreis'], ['at', 'Österreich']]
      .concat(Object.entries(bl).map(([k, v]) => [`BL:${k}`, v]));
    sc.innerHTML = `<button data-scope="local" aria-pressed="${S.scope === 'local'}">Umkreis</button>`
      + `<button data-scope="at" aria-pressed="${S.scope === 'at'}">Österreich</button>`
      + `<select id="blsel" style="background:var(--surf2);border:0;color:var(--tx);
           padding:6px 9px;border-radius:6px;font-size:13.5px;font-weight:600;font-family:inherit">
           <option value="">Bundesland …</option>`
      + Object.entries(bl).map(([k, v]) =>
          `<option value="BL:${k}" ${S.scope === 'BL:' + k ? 'selected' : ''}>${esc(v)}</option>`).join('')
      + `</select>`;
    sc.querySelectorAll('button[data-scope]').forEach(b => b.onclick = async () => {
      S.scope = b.dataset.scope;
      prefs({ ...prefs(), scope: S.scope });
      renderRange();
      await loadAnalysis();
    });
    const sel = sc.querySelector('#blsel');
    if (sel) sel.onchange = async () => {
      if (!sel.value) return;
      S.scope = sel.value;
      prefs({ ...prefs(), scope: S.scope });
      renderRange();
      await loadAnalysis();
    };
  }
  const t = $('#discToggle');
  if (t) { t.checked = S.useDiscount; t.onchange = () => {
    S.useDiscount = t.checked; prefs({ ...prefs(), useDiscount: S.useDiscount }); render(); }; }
}

function renderSeg() {
  const fuels = (S.data && S.data.fuels) || ['DIE', 'SUP'];
  const list = Array.isArray(fuels) ? fuels : Object.keys(fuels);
  $('#fuelseg').innerHTML = list.map(f =>
    `<button data-fuel="${f}" aria-pressed="${f === S.fuel}">${FUEL_NAMES[f] || f}</button>`).join('');
}

function renderStatus() {
  const b = $('#status'); const bits = [];
  b.className = 'bar';
  if (S.offline) { b.classList.add('warn'); bits.push(ico('wifi-off') + 'Offline — letzte gespeicherte Preise'); }
  else if (S.err) { b.classList.add('warn'); bits.push(ico('triangle-alert') + esc(S.err)); }
  if (S.locating) bits.push(ico('crosshair') + 'Standort wird bestimmt …');
  else if (S.pos) bits.push(ico('map-pin') + `Dein Standort${S.posAccuracy ? ` (±${Math.round(S.posAccuracy)} m)` : ''}`);
  else if (!S.err) bits.push(ico('map-pin') + `Basis ${esc(S.data?.home?.name || '—')}`);
  if (S.pos && !S.routing) bits.push(ico('info') + 'Luftlinie — Routing nicht erreichbar');
  else if (S.routing) bits.push(ico('car') + 'Straßenkilometer');
  const sp = S.data?.span;
  if (sp) bits.push(ico('clock') + `Stand ${sp.to.slice(11, 16)} · ${sp.points} Messpunkte`);
  b.innerHTML = bits.join('<span style="opacity:.4">·</span>');
}

function renderCards() {
  const el = $('#cardList');
  if (!el) return;
  if (!S.cardDefs.length) { el.innerHTML = '<p class="hint">Keine Rabattregeln hinterlegt.</p>'; return; }
  el.innerHTML = S.cardDefs.map(c => {
    const rules = S.discounts.filter(r => r.braucht === c.id);
    const sub = rules.map(r =>
      `${esc(String(r.marke))} −${e2(ruleAmount(r))} ct`).join(' · ');
    return `<label class="chk">
      <input type="checkbox" data-card="${esc(c.id)}" ${S.cards.includes(c.id) ? 'checked' : ''}>
      <span class="cl">${esc(c.label)}<span class="cs">${sub || 'keine aktive Regel'}</span></span>
    </label>`;
  }).join('');
  el.querySelectorAll('input[data-card]').forEach(i => i.onchange = () => {
    const id = i.dataset.card;
    S.cards = i.checked ? [...new Set([...S.cards, id])] : S.cards.filter(x => x !== id);
    prefs({ ...prefs(), cards: S.cards });
    render();
  });
  const active = S.discounts.filter(r => S.cards.includes(r.braucht));
  $('#cardNote').innerHTML = active.length
    ? 'Angerechnet: ' + active.map(r =>
        `<b>${esc(String(r.marke))} −${e2(ruleAmount(r))} ct</b>${r.grenze ? ` (${esc(r.grenze)})` : ''}`)
        .join(', ') + '. Diese Werte stehen nicht in den E-Control-Daten und werden von Hand gepflegt.'
    : 'Ohne Auswahl werden nur die gemeldeten Preise verglichen.';
}

function renderNow() {
  const L = ranked();
  if (!L.length) {
    $('#hero').innerHTML = `<div class="panel empty">${S.offline
      ? 'Keine gespeicherten Daten vorhanden. Einmal mit Netz laden.'
      : 'Noch keine Preise für diese Spritsorte.'}</div>`;
    $('#list').innerHTML = ''; renderTiming(); return;
  }
  const c = cfg(), best = L[0];
  const nearest = L.reduce((a, b) => (a.km ?? 1e9) < (b.km ?? 1e9) ? a : b);
  const save = nearest.total - best.total;
  const maps = `https://www.google.com/maps/dir/?api=1&destination=${best.lat},${best.lon}&travelmode=driving`;
  const apple = `https://maps.apple.com/?daddr=${best.lat},${best.lon}&dirflg=d`;
  const isApple = /iPhone|iPad|Macintosh/.test(navigator.userAgent);

  $('#hero').innerHTML = `
    <div class="hero">
      <div class="hero-top">
        <div class="tagline">${ico('navigation')}Beste Wahl · ${FUEL_NAMES[S.fuel]}</div>
        <h2>${esc(best.n || 'Tankstelle ohne Namen')}</h2>
        <div class="where">${ico('map-pin')}${esc([best.a, best.c].filter(Boolean).join(', '))}
          ${best.h24 ? '<span class="chip">24 H</span>' : ''}
          ${best.sb ? '<span class="chip">SB</span>' : ''}</div>
      </div>
      <div class="facts">
        <div class="fact"><div class="k">Preis</div>
          <div class="v">${e3(best.amount)}</div>
          <div class="s">${best.disc
            ? `${e3(best.raw)} − ${e2(ruleAmount(best.disc))} ct ${esc(best.disc.label)}`
            : '€ je Liter'}</div></div>
        <div class="fact"><div class="k">Entfernung</div>
          <div class="v">${km(best.km)}</div>
          <div class="s">${best.min != null ? Math.round(best.min) + ' min Fahrt' : 'Luftlinie'}</div></div>
        <div class="fact"><div class="k">Echtkosten</div>
          <div class="v">${e2(best.total)}</div><div class="s">€ für ${c.L} l inkl. Umweg</div></div>
      </div>
      ${save > 0.01 ? `<div class="saving">${ico('circle-check')}
        <span><b>${e2(save)} € günstiger</b> als die nächstgelegene
        (${esc(nearest.n || '—')}, ${km(nearest.km)}, ${e3(nearest.amount)} €)</span></div>` : ''}
      <div class="cta">
        <a class="primary" href="${isApple ? apple : maps}" target="_blank" rel="noopener">
          ${ico('navigation')}Route starten</a>
        <button class="ghost" id="altBtn">${ico('list-ordered')}Alternativen</button>
      </div>
    </div>`;
  $('#altBtn').onclick = () => $('#list').scrollIntoView({ behavior: 'smooth', block: 'start' });

  const amts = L.map(s => s.amount), mn = Math.min(...amts), mx = Math.max(...amts);
  $('#list').innerHTML = L.slice(1, 9).map((s, i) => `
    <button class="row" data-id="${s.id}">
      <span class="rank">${i + 2}</span>
      <span class="tick" style="background:${tierColor(s.amount, mn, mx)}"></span>
      <span class="mid">
        <span class="nm">${esc(s.n || 'ohne Namen')}</span>
        <span class="sub">${esc(s.c || '')} · ${km(s.km)}${s.min != null ? ` · ${Math.round(s.min)} min` : ''}
          ${s.h24 ? '<span class="chip">24 H</span>' : ''}</span>
      </span>
      <span class="rt"><span class="pr">${e3(s.amount)}</span>
        <span class="tot">${e2(s.total)} €</span></span>
    </button>`).join('');
  $('#list').querySelectorAll('.row').forEach(r => r.onclick = () => {
    const s = L.find(x => x.id === +r.dataset.id);
    if (s) window.open(isApple
      ? `https://maps.apple.com/?daddr=${s.lat},${s.lon}&dirflg=d`
      : `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}&travelmode=driving`,
      '_blank', 'noopener');
  });
  renderTiming();
}

function renderTiming() {
  const a = S.analysis, box = $('#timing');
  const hp = a?.fuels?.[S.fuel]?.hour;
  const noon = a?.fuels?.[S.fuel]?.noon;
  const lvl = a?.confidence?.level ?? 0;

  let msg = `<p class="hint"><b>Österreich-Regel:</b> Preise dürfen nur <b>einmal täglich um 12:00</b>
    erhöht werden, Senkungen jederzeit. Daher ist es kurz vor Mittag typischerweise am günstigsten
    und direkt danach am teuersten.</p>`;

  if (noon && noon.length) {
    const last = noon[noon.length - 1];
    msg += `<p class="hint" style="margin-top:8px">${ico('circle-check')}
      <b>In deinen Daten gemessen:</b> am ${last.date.slice(8)}.${last.date.slice(5, 7)}. stieg der Preis
      um 12:00 im Mittel um <b>${e2(last.mean_ct)} ct</b> — ${last.up} Stationen teurer,
      ${last.down} günstiger (n=${last.n}).</p>`;
  }
  if (hp && lvl >= 1) {
    const es = Object.entries(hp);
    const best = es.reduce((x, y) => x[1].delta_ct < y[1].delta_ct ? x : y);
    const worst = es.reduce((x, y) => x[1].delta_ct > y[1].delta_ct ? x : y);
    msg += `<p class="hint" style="margin-top:8px">Günstigste Stunde bisher
      <b>${best[0].padStart(2, '0')}:00</b> (${e2(best[1].delta_ct)} ct),
      teuerste <b>${worst[0].padStart(2, '0')}:00</b> (+${e2(worst[1].delta_ct)} ct).</p>`;
  } else if (a) {
    msg += `<p class="hint" style="margin-top:8px">${ico('info')} Für ein belastbares Stundenprofil
      sammelt das Tool noch — ${a.confidence.label}.</p>`;
  }
  box.innerHTML = msg;
}


/* ---------- Diagramm-Werkzeuge ---------- */
function svgWrap(inner, W, H, label) {
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
    role="img" aria-label="${esc(label)}" style="height:${H}px">${inner}</svg>`;
}
function bars(rows, label) {
  // rows: [{l, v, txt, color, frac}]
  return `<div class="bars">` + rows.map(r => `
    <div class="brow"><span class="bl" title="${esc(r.l)}">${esc(r.l)}</span>
      <span class="bt" style="width:${(r.frac * 100).toFixed(1)}%;background:${r.color}"></span>
      <span class="bv">${r.txt}</span></div>`).join('') + `</div>`
    + (label ? `<p class="note">${label}</p>` : '');
}

/* Marktverlauf: Spanne als Fläche, Median und Bestpreis als Linien */
function drawBand(ser, sel) {
  const el = $(sel);
  if (!ser || ser.length < 2) { el.innerHTML = '<p class="note">Zu wenig Verlauf.</p>'; return; }
  const W = 1000, H = 260, PL = 46, PR = 12, PT = 14, PB = 26;
  const lo = Math.min(...ser.map(p => p.min)), hi = Math.max(...ser.map(p => p.max));
  const pad = (hi - lo) * .08 || .01;
  const v0 = lo - pad, v1 = hi + pad;
  const X = i => PL + i / (ser.length - 1) * (W - PL - PR);
  const Y = v => H - PB - (v - v0) / ((v1 - v0) || 1) * (H - PT - PB);
  const line = k => ser.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}`).join('');
  const area = ser.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.max).toFixed(1)}`).join('')
    + ser.slice().reverse().map((p, i) =>
        `L${X(ser.length - 1 - i).toFixed(1)},${Y(p.min).toFixed(1)}`).join('') + 'Z';
  const band2 = ser.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.med).toFixed(1)}`).join('')
    + ser.slice().reverse().map((p, i) =>
        `L${X(ser.length - 1 - i).toFixed(1)},${Y(p.p25).toFixed(1)}`).join('') + 'Z';
  const ticks = 4, grid = [];
  for (let i = 0; i <= ticks; i++) {
    const v = v0 + (v1 - v0) * i / ticks, y = Y(v);
    grid.push(`<line x1="${PL}" y1="${y.toFixed(1)}" x2="${W - PR}" y2="${y.toFixed(1)}"
      stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>
      <text x="${PL - 6}" y="${(y + 3).toFixed(1)}" fill="var(--tx3)" font-size="10"
        text-anchor="end">${e3(v)}</text>`);
  }
  const step = Math.max(1, Math.ceil(ser.length / 7));
  const xlab = ser.map((p, i) => i % step ? '' :
    `<text x="${X(i).toFixed(1)}" y="${H - 8}" fill="var(--tx3)" font-size="9.5"
      text-anchor="middle">${p.t.slice(8, 10)}.${p.t.slice(5, 7)}. ${p.t.slice(11, 16)}</text>`).join('');
  el.innerHTML = svgWrap(
    grid.join('')
    + `<path d="${area}" fill="var(--acc)" opacity=".10"/>`
    + `<path d="${band2}" fill="var(--acc)" opacity=".18"/>`
    + `<path d="${line('avg')}" fill="none" stroke="var(--acc)" stroke-width="2"
        vector-effect="non-scaling-stroke"/>`
    + `<path d="${line('min')}" fill="none" stroke="var(--good)" stroke-width="2"
        vector-effect="non-scaling-stroke"/>`
    + `<path d="${line('max')}" fill="none" stroke="var(--bad)" stroke-width="1.3"
        stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`
    + xlab, W, H, 'Marktverlauf mit Bestpreis, Schnitt und Spanne')
  + `<div class="legend">
      <span><i style="background:var(--good)"></i>Bestpreis</span>
      <span><i style="background:var(--acc)"></i>Schnitt</span>
      <span><i style="background:var(--bad)"></i>teuerste</span>
      <span><i style="background:var(--acc);opacity:.35"></i>25 % bis Median</span>
      <span>${ser[ser.length - 1].n} Stationen zuletzt</span></div>`;
}

/* Verteilung der aktuellen Preise */
function drawHisto(sp, sel) {
  const el = $(sel);
  if (!sp || !sp.hist || sp.hist.length < 3) {
    el.innerHTML = '<p class="note">Zu wenig Daten.</p>'; return;
  }
  const v = sp.hist, lo = v[0], hi = v[v.length - 1];
  const nb = 14, w = (hi - lo) / nb || 1;
  const b = new Array(nb).fill(0);
  v.forEach(x => b[Math.min(nb - 1, Math.floor((x - lo) / w))]++);
  const mx = Math.max(...b);
  const W = 340, H = 130, PB = 22, PT = 8;
  el.innerHTML = svgWrap(b.map((n, i) => {
    const bw = W / nb, h = n / mx * (H - PT - PB);
    return `<rect x="${(i * bw + 1).toFixed(1)}" y="${(H - PB - h).toFixed(1)}"
      width="${(bw - 2).toFixed(1)}" height="${h.toFixed(1)}"
      fill="${tierColor(lo + w * (i + .5), lo, hi)}" opacity=".85"/>`;
  }).join('')
    + `<text x="2" y="${H - 7}" fill="var(--tx3)" font-size="9.5">${e3(lo)}</text>`
    + `<text x="${W - 2}" y="${H - 7}" fill="var(--tx3)" font-size="9.5"
        text-anchor="end">${e3(hi)}</text>`, W, H, 'Verteilung der aktuellen Preise')
  + `<p class="note">${sp.n} Tankstellen · Spanne <b>${e2(sp.spread_ct)} ct</b>
     · Median ${e3(sp.med)} · Schnitt ${e3(sp.avg)}</p>`;
}

/* Sägezahn: mittlerer Preis relativ zum Mittag */
function drawCycle(cy, sel) {
  const el = $(sel);
  if (!cy || cy.length < 4) { el.innerHTML = '<p class="note">Noch kein voller Tageszyklus.</p>'; return; }
  const W = 340, H = 150, PL = 30, PB = 24, PT = 10;
  const vs = cy.map(p => p.delta_ct);
  const v0 = Math.min(...vs, 0), v1 = Math.max(...vs, 0);
  const X = i => PL + i / (cy.length - 1) * (W - PL - 8);
  const Y = v => H - PB - (v - v0) / ((v1 - v0) || 1) * (H - PT - PB);
  const d = cy.map((p, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(p.delta_ct).toFixed(1)}`).join('');
  el.innerHTML = svgWrap(
    `<line x1="${PL}" y1="${Y(0).toFixed(1)}" x2="${W - 8}" y2="${Y(0).toFixed(1)}"
      stroke="var(--line)" stroke-width="1"/>`
    + `<path d="${d}" fill="none" stroke="var(--acc)" stroke-width="2"/>`
    + cy.map((p, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(p.delta_ct).toFixed(1)}" r="2"
        fill="${p.delta_ct > 0 ? 'var(--bad)' : 'var(--good)'}"/>`).join('')
    + `<text x="${PL}" y="${H - 8}" fill="var(--tx3)" font-size="9.5">12:00</text>`
    + `<text x="${W - 8}" y="${H - 8}" fill="var(--tx3)" font-size="9.5"
        text-anchor="end">11:00</text>`
    + `<text x="2" y="${(Y(v1) + 4).toFixed(1)}" fill="var(--tx3)" font-size="9">+${e2(v1)}</text>`
    + `<text x="2" y="${(Y(v0) + 4).toFixed(1)}" fill="var(--tx3)" font-size="9">${e2(v0)}</text>`,
    W, H, 'Mittlerer Preisverlauf über den Tag ab 12:00')
  + `<p class="note">Stunden nach dem Mittagssprung. Der Aufschlag um 12:00 bröckelt bis
     zum nächsten Mittag wieder ab.</p>`;
}

function drawLeaders(ld, sel) {
  const el = $(sel);
  if (!ld || !ld.length) { el.innerHTML = '<p class="note">Noch keine Auswertung.</p>'; return; }
  const mx = Math.max(...ld.map(x => x.pct)) || 1;
  el.innerHTML = bars(ld.map(x => ({
    l: `${x.name || '—'}${x.city ? ', ' + x.city : ''}`,
    frac: x.pct / mx, color: 'var(--good)', txt: `${e2(x.pct)} %`
  })), 'Anteil der Stunden, in denen die Station den niedrigsten Preis im Gebiet hatte.');
}

function drawBrands(br, sel) {
  const el = $(sel);
  if (!br || !br.length) { el.innerHTML = '<p class="note">Noch keine Auswertung.</p>'; return; }
  const lo = Math.min(...br.map(x => x.avg)), hi = Math.max(...br.map(x => x.avg));
  el.innerHTML = bars(br.map(x => ({
    l: x.brand,
    frac: hi > lo ? .25 + (x.avg - lo) / (hi - lo) * .75 : 1,
    color: tierColor(x.avg, lo, hi),
    txt: `${e3(x.avg)} · n=${x.n}`
  })), 'Mittlerer gemeldeter Preis je Marke im gewählten Zeitraum.');
}

function drawVol(vo, sel) {
  const el = $(sel);
  if (!vo || !vo.length) { el.innerHTML = '<p class="note">Noch keine Auswertung.</p>'; return; }
  const mx = Math.max(...vo.map(x => x.changes)) || 1;
  el.innerHTML = bars(vo.map(x => ({
    l: `${x.name || '—'}${x.city ? ', ' + x.city : ''}`,
    frac: x.changes / mx, color: 'var(--mid)',
    txt: `${x.changes}× · ${e2(x.span_ct)} ct`
  })), 'Zahl der Preisänderungen und die Spanne zwischen höchstem und niedrigstem Wert.');
}

/* ---------- Dashboard ---------- */
function renderDash() {
  const a = S.analysis, L = ranked();
  const cov = a?.coverage;
  const tr = a?.fuels?.[S.fuel]?.trend || [];
  const first = tr.length ? tr[0][1] : null, last = tr.length ? tr[tr.length - 1][1] : null;

  const sp = a?.fuels?.[S.fuel]?.spread;
  const c2 = cfg();
  const cheapest = L.length ? L.reduce((x, y) => x.amount < y.amount ? x : y) : null;
  const dearest = L.length ? L.reduce((x, y) => x.amount > y.amount ? x : y) : null;
  const potential = cheapest && dearest ? (dearest.amount - cheapest.amount) * c2.L : null;
  $('#kpis').innerHTML = [
    ['Günstigster', cheapest ? e3(cheapest.amount) : '–',
      cheapest ? `${esc(cheapest.n || '—')}, ${esc(cheapest.c || '')}` : ''],
    ['Schnitt', sp ? e3(sp.avg) : '–', sp ? `Median ${e3(sp.med)}` : ''],
    ['Spanne', sp ? e2(sp.spread_ct) : '–',
      sp ? `ct zwischen billigster und teuerster` : ''],
    ['Sparpotenzial', potential != null ? e2(potential) : '–',
      `€ je ${c2.L} l, falsche statt beste Wahl`],
    ['Trend', first && last ? (last - first > 0 ? '+' : '') + e2((last - first) * 100) : '–',
      `ct im Zeitraum`],
    ['Abdeckung', cov ? `${cov.fuels[S.fuel]?.priced ?? 0}` : '–',
      cov ? `von ${cov.stations} Stationen (${cov.fuels[S.fuel]?.pct ?? 0} %)` : ''],
    ['Messpunkte', a?.span?.points ?? '–', a?.span ? `über ${a.span.days} Tage` : ''],
    ['Sammelläufe', cov?.polls?.n ?? '–', 'nach Abtastplan']
  ].map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div>
      <div class="s">${s}</div></div>`).join('');

  // Tabelle
  const amts = L.map(s => s.amount), mn = Math.min(...amts), mx = Math.max(...amts);
  const sorted = [...L].sort((x, y) => {
    let p = S.sort === 'name' ? (x.n || '').toLowerCase() : x[S.sort];
    let q = S.sort === 'name' ? (y.n || '').toLowerCase() : y[S.sort];
    if (p == null) p = Infinity; if (q == null) q = Infinity;
    return (p > q ? 1 : p < q ? -1 : 0) * (S.asc ? 1 : -1);
  });
  $('#dashRows').innerHTML = sorted.map(s => `
    <tr data-id="${s.id}" aria-selected="${S.sel === s.id}">
      <td class="num" style="font-weight:600">${e2(s.total)} €</td>
      <td class="num" style="color:${tierColor(s.amount, mn, mx)}">${e3(s.amount)}${
        s.disc ? `<div class="note" style="margin:0;color:var(--good)">${e3(s.raw)} − ${e2(ruleAmount(s.disc))} ct ${esc(s.disc.label)}</div>` : ''}</td>
      <td class="num">${s.km == null ? '–' : e2(s.km)}</td>
      <td>${esc(s.n || 'ohne Namen')}${s.h24 ? ' <span class="chip">24H</span>' : ''}${
        s.sb ? ' <span class="chip">SB</span>' : ''}${
        s.fix_m != null ? ` <span class="chip" title="Koordinate gegen OpenStreetMap korrigiert">±${Math.round(s.fix_m)}m</span>` : ''}</td>
      <td>${esc(s.c || '')}</td>
      <td class="num" style="font-size:11.5px;color:var(--tx3)">${(s.ts || '').slice(5, 16).replace('T', ' ')}</td>
    </tr>`).join('');
  const rn = a?.scope === 'at' ? 'österreichweit'
    : a?.scope === 'local' ? 'im Umkreis'
    : 'in ' + ((a?.regions || {})[String(a?.scope || '').slice(3)] || 'diesem Gebiet');
  const sn = `${a?.scope_n ?? '?'} Stationen ${rn}`;
  $('#dashNote').textContent =
    `Tabelle: ${L.length} Tankstellen im Umkreis · ${S.routing ? 'Straßenkilometer' : 'Luftlinie'}`
    + ` · Diagramme rechnen über ${sn} · Zeile anklicken für den Verlauf`;
  $('#dashRows').querySelectorAll('tr').forEach(tr => tr.onclick = () => {
    S.sel = +tr.dataset.id; loadHistory(S.sel); renderDash();
  });
  document.querySelectorAll('th[data-s]').forEach(th => th.onclick = () => {
    const k = th.dataset.s;
    if (S.sort === k) S.asc = !S.asc; else { S.sort = k; S.asc = true; }
    renderDash();
  });

  const F = a?.fuels?.[S.fuel] || {};
  drawMap(L, mn, mx);
  drawBand(F.series, '#bandChart');
  drawHisto(F.spread, '#histoChart');
  drawHours(F.hour, '#hourChart');
  drawCycle(F.cycle, '#cycleChart');
  drawNoon(F.noon, '#noonChart');
  drawWeek(F.weekday, '#weekChart');
  drawLeaders(F.leaders, '#leadChart');
  drawBrands(F.brands, '#brandChart');
  drawVol(F.volatility, '#volChart');
  drawHistory();
}

function drawMap(L, mn, mx) {
  const pts = L.filter(s => s.lat);
  if (!pts.length || typeof L2 === 'undefined') return;
  const o = S.pos || S.data.home;
  if (!S.map) {
    S.map = L2.map('map', { scrollWheelZoom: false, attributionControl: true });
    L2.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap'
    }).addTo(S.map);
    S.map.on('click', () => { S.sel = null; renderDash(); });
  }
  if (S.layer) S.layer.remove();
  S.layer = L2.layerGroup().addTo(S.map);

  L2.circleMarker([o.lat, o.lon], {
    radius: 7, color: 'var(--acc)', weight: 3, fill: false, interactive: false
  }).addTo(S.layer);

  const isApple = /iPhone|iPad|Macintosh/.test(navigator.userAgent);
  pts.forEach(s => {
    const adv = (mx - s.amount) / ((mx - mn) || 1);
    const nav = isApple
      ? `https://maps.apple.com/?daddr=${s.lat},${s.lon}&dirflg=d`
      : `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}&travelmode=driving`;
    const m = L2.circleMarker([s.lat, s.lon], {
      radius: 5 + adv * 6, color: tierColor(s.amount, mn, mx), weight: S.sel === s.id ? 3 : 1,
      fillColor: tierColor(s.amount, mn, mx), fillOpacity: .72,
      opacity: S.sel === s.id ? 1 : .85
    }).addTo(S.layer);
    m.bindPopup(
      `<b>${esc(s.n || 'ohne Namen')}</b><br>${esc([s.a, s.c].filter(Boolean).join(', '))}<br>`
      + `<span style="font-family:var(--mono)">${e3(s.amount)} €/l</span>`
      + (s.disc ? ` <span style="color:var(--good)">(${esc(s.disc.label)})</span>` : '')
      + ` &middot; ${km(s.km)}`
      + (s.min != null ? ` &middot; ${Math.round(s.min)} min` : '')
      + `<br><a href="${nav}" target="_blank" rel="noopener">Route starten</a>`);
    m.on('click', () => { S.sel = s.id; loadHistory(s.id); });
  });

  if (!S.mapFitted) {
    S.map.fitBounds(L2.latLngBounds(pts.map(s => [s.lat, s.lon]).concat([[o.lat, o.lon]]))
      .pad(0.08));
    S.mapFitted = true;
  }
  setTimeout(() => S.map.invalidateSize(), 60);
  $('#mapNote').textContent =
    `${pts.length} Tankstellen · Punktgröße zeigt den Preisvorteil · Ring = dein Standort`;
}

function drawHours(hp, sel) {
  const el = $(sel);
  if (!hp || !Object.keys(hp).length) {
    el.innerHTML = '<p class="note">Noch keine Stundendaten.</p>'; return;
  }
  const es = Object.entries(hp).map(([h, d]) => [+h, d]);
  const mx = Math.max(...es.map(([, d]) => Math.abs(d.delta_ct))) || 1;
  el.innerHTML = es.map(([h, d]) => `
    <div class="hrow">
      <span class="hl">${String(h).padStart(2, '0')}:00</span>
      <span class="hb" style="width:${(Math.abs(d.delta_ct) / mx * 62).toFixed(1)}%;
        background:${d.delta_ct < 0 ? 'var(--good)' : 'var(--bad)'}"></span>
      <span class="hv">${d.delta_ct > 0 ? '+' : ''}${e2(d.delta_ct)} ct · n=${d.n}</span>
    </div>`).join('')
    + '<p class="note">Abweichung vom Tagesmittel derselben Station.</p>';
}

function drawWeek(wp, sel) {
  const el = $(sel);
  if (!wp || !Object.keys(wp).length) {
    el.innerHTML = '<p class="note">Noch keine Wochendaten. Dafür braucht es mindestens zwei Wochen.</p>';
    return;
  }
  const es = Object.entries(wp);
  const mx = Math.max(...es.map(([, d]) => Math.abs(d.delta_ct))) || 1;
  el.innerHTML = es.map(([d, v]) => `
    <div class="hrow"><span class="hl">${d}</span>
      <span class="hb" style="width:${(Math.abs(v.delta_ct) / mx * 62).toFixed(1)}%;
        background:${v.delta_ct < 0 ? 'var(--good)' : 'var(--bad)'}"></span>
      <span class="hv">${v.delta_ct > 0 ? '+' : ''}${e2(v.delta_ct)} ct · n=${v.n}</span></div>`).join('');
}

function drawNoon(noon, sel) {
  const el = $(sel);
  if (!noon || !noon.length) {
    el.innerHTML = '<p class="note">Noch kein vollständiger Mittag erfasst.</p>'; return;
  }
  el.innerHTML = `<div class="scroll"><table>
    <thead><tr><th>Tag</th><th>Median</th><th>Mittel</th><th>rauf</th><th>runter</th></tr></thead>
    <tbody>${noon.map(d => `<tr>
      <td class="num">${d.date.slice(8)}.${d.date.slice(5, 7)}.</td>
      <td class="num" style="color:${d.median_ct > 0 ? 'var(--bad)' : 'var(--tx3)'}">
        ${d.median_ct > 0 ? '+' : ''}${e2(d.median_ct)}</td>
      <td class="num">${d.mean_ct > 0 ? '+' : ''}${e2(d.mean_ct)}</td>
      <td class="num" style="color:var(--bad)">${d.up}</td>
      <td class="num" style="color:var(--good)">${d.down}</td></tr>`).join('')}</tbody></table></div>
    <p class="note">Preis um 11:55 gegen 12:15, paarweise je Station. Werte in Cent.</p>`;
}

function drawTrend(tr, sel) {
  const el = $(sel);
  if (!tr || tr.length < 2) { el.innerHTML = '<p class="note">Zu wenig Verlauf.</p>'; return; }
  const W = 340, H = 130, P = 26;
  const vs = tr.map(r => r[1]);
  const v0 = Math.min(...vs), v1 = Math.max(...vs);
  const X = i => P + i / (tr.length - 1) * (W - 2 * P);
  const Y = v => H - P - (v - v0) / ((v1 - v0) || 1) * (H - 2 * P);
  const d = tr.map((r, i) => `${i ? 'L' : 'M'}${X(i).toFixed(1)},${Y(r[1]).toFixed(1)}`).join('');
  el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Preistrend">
    <path d="${d}L${X(tr.length - 1).toFixed(1)},${H - P}L${P},${H - P}Z"
      fill="var(--acc)" opacity=".12"/>
    <path d="${d}" fill="none" stroke="var(--acc)" stroke-width="2" stroke-linejoin="round"/>
    <circle cx="${X(tr.length - 1).toFixed(1)}" cy="${Y(vs[vs.length - 1]).toFixed(1)}" r="3.5"
      fill="var(--acc)"/>
    <text x="2" y="${P - 8}" fill="var(--tx3)" font-size="9">${e3(v1)}</text>
    <text x="2" y="${H - P + 11}" fill="var(--tx3)" font-size="9">${e3(v0)}</text>
  </svg><p class="note">Stundenmittel aller Stationen · ${tr[0][0].slice(5, 13)} bis
    ${tr[tr.length - 1][0].slice(5, 13)}</p>`;
}

let histCache = {};
async function loadHistory(id) {
  const k = id + S.fuel;
  if (histCache[k]) { drawHistory(); return; }
  try {
    const d = await (await fetch(`/api/history?id=${id}&fuel=${S.fuel}`)).json();
    histCache[k] = d.points || [];
  } catch { histCache[k] = []; }
  drawHistory();
}
function drawHistory() {
  const el = $('#hist'), id = S.sel;
  const st = id && S.data ? S.data.stations.find(s => s.id === id) : null;
  $('#histTitle').innerHTML = `${ico('trending-down')}Preisverlauf${st ? ' · ' + esc(st.n || '—') : ''}`;
  const pts = id ? histCache[id + S.fuel] : null;
  if (!pts || pts.length < 2) {
    el.innerHTML = `<p class="note">${st
      ? 'Für diese Station gibt es erst einen Messpunkt.'
      : 'Zeile in der Tabelle anklicken.'}</p>`;
    return;
  }
  const W = 340, H = 130, P = 26;
  const ts = pts.map(p => new Date(p[0]).getTime()), vs = pts.map(p => p[1]);
  const t0 = Math.min(...ts), t1 = Math.max(...ts), v0 = Math.min(...vs), v1 = Math.max(...vs);
  const X = t => P + (t - t0) / ((t1 - t0) || 1) * (W - 2 * P);
  const Y = v => H - P - (v - v0) / ((v1 - v0) || 1) * (H - 2 * P);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${X(ts[i]).toFixed(1)},${Y(vs[i]).toFixed(1)}`).join('');
  el.innerHTML = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Preisverlauf">
    <path d="${d}" fill="none" stroke="var(--acc)" stroke-width="2" stroke-linejoin="round"/>
    ${pts.map((p, i) => `<circle cx="${X(ts[i]).toFixed(1)}" cy="${Y(vs[i]).toFixed(1)}"
      r="2.2" fill="var(--acc)"/>`).join('')}
    <text x="2" y="${P - 8}" fill="var(--tx3)" font-size="9">${e3(v1)}</text>
    <text x="2" y="${H - P + 11}" fill="var(--tx3)" font-size="9">${e3(v0)}</text>
  </svg><p class="note">${pts.length} Messpunkte · Spanne ${e2((v1 - v0) * 100)} ct</p>`;
}

/* ---------- Ereignisse ---------- */
document.addEventListener('click', e => {
  const f = e.target.closest('[data-fuel]');
  if (f) { S.fuel = f.dataset.fuel; histCache = {}; prefs({ ...prefs(), fuel: S.fuel }); loadData(); return; }
  const v = e.target.closest('.tabs button');
  if (v) { S.view = v.dataset.view; prefs({ ...prefs(), view: S.view });
    history.pushState({ view: S.view }, '', S.view === 'dash' ? '/dashboard' : '/');
    document.querySelectorAll('.tabs button').forEach(b =>
      b.setAttribute('aria-pressed', b === v));
    render(); }
});
$('#locate').onclick = locate;
$('#reload').onclick = async e => {
  const b = e.currentTarget, i = b.querySelector('.ico');
  i.classList.add('spin');
  await Promise.all([loadData(), loadAnalysis()]);
  i.classList.remove('spin');
};
['li', 'co', 'de'].forEach(id => $('#' + id).oninput = () => {
  const p = prefs();
  prefs({ ...p, L: +$('#li').value, C: +$('#co').value, D: +$('#de').value });
  render();
});
window.addEventListener('popstate', () => {
  S.view = location.pathname.replace(/\/+$/, '') === '/dashboard' ? 'dash' : 'now';
  document.querySelectorAll('.tabs button').forEach(b =>
    b.setAttribute('aria-pressed', b.dataset.view === S.view));
  render();
});
window.addEventListener('online', () => { S.offline = false; loadData(); });
window.addEventListener('offline', () => { S.offline = true; render(); });

/* ---------- Start ---------- */
(function init() {
  const p = prefs();
  if (p.fuel) S.fuel = p.fuel;
  if (p.L) $('#li').value = p.L;
  if (p.C) $('#co').value = p.C;
  if (p.D) $('#de').value = p.D;
  // Ansicht aus dem Pfad, damit /dashboard verlinkbar und neu ladbar ist
  if (location.pathname.replace(/\/+$/, '') === '/dashboard') S.view = 'dash';
  else if (window.innerWidth >= 900 && p.view) S.view = p.view;
  if (p.days) S.days = p.days;
  if (p.scope) S.scope = p.scope;
  if (p.useDiscount === false) S.useDiscount = false;
  if (Array.isArray(p.cards)) S.cards = p.cards;
  loadDiscounts().then(loadData); loadAnalysis();
  if (navigator.geolocation && window.isSecureContext) locate();
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
})();
