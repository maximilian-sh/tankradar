const fs = require('fs');
const ST = JSON.parse(fs.readFileSync('/tmp/st.json','utf8'));
const AN = JSON.parse(fs.readFileSync('/tmp/an.json','utf8'));
const DI = JSON.parse(fs.readFileSync('/tmp/di.json','utf8'));

function el(id){ return {
  id, innerHTML:'', textContent:'', value:'', checked:false, className:'', dataset:{},
  style:{}, classList:{add(){},remove(){},contains(){return false}},
  querySelectorAll(){return []}, querySelector(){return null},
  setAttribute(){}, getAttribute(){return null}, appendChild(){}, onclick:null,
  scrollIntoView(){}, closest(){return null}
};}
const bag = {};
global.document = {
  body: { dataset:{} },
  querySelector(sel){ const k=sel.replace(/^[#.]/,''); return bag[k] || (bag[k]=el(k)); },
  querySelectorAll(){ return []; },
  addEventListener(){}
};
global.window = { innerWidth: 1600, isSecureContext: true, L: undefined,
                  addEventListener(){}, open(){} };
global.navigator = { userAgent:'node', geolocation:null, serviceWorker:undefined };
global.location = { pathname: process.argv[2] || '/' };
global.history = { pushState(){} };
global.localStorage = { getItem(){return null}, setItem(){} };
global.fetch = async (u) => ({
  ok:true,
  json: async () => u.includes('/api/analysis') ? AN
       : u.includes('/api/stations') ? ST
       : u.includes('discounts')     ? DI
       : u.includes('/api/route')    ? ST
       : ({})
});
process.on('unhandledRejection', e => { console.log('UNHANDLED REJECTION:'); console.log(e); });

try {
  const src = fs.readFileSync(__dirname + '/../web/app.js','utf8');
  eval(src);
  setTimeout(()=>console.log('--- ohne Ausnahme durchgelaufen ---'), 400);
} catch (e) {
  console.log('AUSNAHME beim Laden:');
  console.log(e && e.stack ? e.stack.split('\n').slice(0,6).join('\n') : e);
}
