/* Carte électorale — cantons, circonscriptions, communes, quartiers IRIS. */
'use strict';

const PALETTE = ['#ffadad', '#ffd6a5', '#fdffb6', '#caffbf', '#9bf6ff', '#a0c4ff', '#d4b8ff'];
const ZOOM_COMMUNES = 8; // en dessous, on ne charge pas les communes / quartiers
const FOND = 'https://tiles.openfreemap.org/styles/positron';
const POLICE = ['Noto Sans Regular'];
const POLICE_GRAS = ['Noto Sans Bold'];
const POLICE_ITAL = ['Noto Sans Italic'];

const etat = {
  index: null,           // index.json
  deps: [],              // polygones des départements
  cantons: [],           // polygones des cantons
  circos: [],            // polygones des circonscriptions
  parDep: new Map(),     // dep -> {communes, iris} (polygones), chargés à la demande
  enCours: new Map(),    // dep -> Promise
  cantonParCode: new Map(),
  circoParCode: new Map(),
};

const $ = (s) => document.querySelector(s);
const echapper = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const normaliser = (s) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[-'’]/g, ' ').replace(/\s+/g, ' ').trim();

/* ---------- Géométrie ---------- */

function bboxDe(geom) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const polys = geom.type === 'Polygon' ? [geom.coordinates] : geom.coordinates;
  for (const p of polys) for (const [x, y] of p[0]) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return [x0, y0, x1, y1];
}

function dansAnneau(x, y, anneau) {
  let dedans = false;
  for (let i = 0, j = anneau.length - 1; i < anneau.length; j = i++) {
    const [xi, yi] = anneau[i], [xj, yj] = anneau[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) dedans = !dedans;
  }
  return dedans;
}

function dansPolygone(x, y, f) {
  const b = f.bbox;
  if (x < b[0] || x > b[2] || y < b[1] || y > b[3]) return false;
  const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
  for (const p of polys) {
    if (dansAnneau(x, y, p[0]) && !p.slice(1).some((trou) => dansAnneau(x, y, trou))) return true;
  }
  return false;
}

const trouver = (liste, x, y) => liste.find((f) => dansPolygone(x, y, f)) || null;

function preparer(fc) {
  const polys = [];
  for (const f of fc.features) {
    if (f.geometry.type === 'Point') continue;
    f.bbox = bboxDe(f.geometry);
    polys.push(f);
  }
  return polys;
}

const unionBbox = (bs) => bs.reduce((a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]);

/* ---------- Carte ---------- */

const carte = new maplibregl.Map({
  container: 'carte',
  style: FOND,
  center: [2.4, 46.6],
  zoom: 5.2,
  minZoom: 4,
  maxZoom: 16,
  hash: true,
  attributionControl: { compact: true },
});
window.carte = carte; // pratique pour déboguer depuis la console
// La carte peut être créée avant que la feuille de style ait fixé la taille du conteneur.
new ResizeObserver(() => carte.resize()).observe(document.getElementById('carte'));
carte.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
carte.addControl(new maplibregl.ScaleControl({ unit: 'metric' }), 'bottom-right');
carte.addControl(new maplibregl.GeolocateControl({ positionOptions: { enableHighAccuracy: false } }), 'top-right');

const VIDE = { type: 'FeatureCollection', features: [] };
const estPoly = ['==', ['geometry-type'], 'Polygon'];
const estPoint = ['==', ['geometry-type'], 'Point'];
const couleurIndex = ['match', ['to-string', ['get', 'ci']], ...PALETTE.flatMap((c, i) => [String(i), c]), '#eeeeee'];

const COUCHES = {
  circos: ['circos-ligne', 'circos-ligne-det', 'circos-etiquette'],
  cantons: ['cantons-ligne', 'cantons-ligne-det', 'cantons-etiquette'],
  communes: ['communes-ligne', 'communes-etiquette'],
  iris: ['iris-ligne', 'iris-etiquette'],
  deps: ['deps-ligne', 'deps-ligne-det'],
};

// Au-dessus de ZOOM_COMMUNES, cantons / circonscriptions / départements viennent des fichiers
// détaillés du département (reconstruits à partir des communes : limites superposées).
// En dessous, version nationale allégée.
function ajouterPaire(def, avant) {
  carte.addLayer({ ...def, source: def.source, maxzoom: ZOOM_COMMUNES }, avant);
  carte.addLayer({ ...def, id: def.id + '-det', source: def.source + '-det', minzoom: Math.max(def.minzoom ?? 0, ZOOM_COMMUNES) }, avant);
}

function ajouterCouches() {
  // Le fond OpenFreeMap a ses propres noms de villes : on les masque, nos étiquettes les remplacent.
  for (const l of carte.getStyle().layers) {
    if (l.type === 'symbol' && l['source-layer'] === 'place') carte.setLayoutProperty(l.id, 'visibility', 'none');
  }
  const premierSymbole = carte.getStyle().layers.find((l) => l.type === 'symbol')?.id;

  for (const s of ['deps', 'cantons', 'circos', 'deps-det', 'cantons-det', 'circos-det', 'communes', 'iris', 'selection']) {
    carte.addSource(s, { type: 'geojson', data: VIDE, tolerance: 0.3 });
  }

  // Aplats de couleur (sous les routes et noms de rues du fond)
  const opacite = ['interpolate', ['linear'], ['zoom'], 5, 0.55, 12, 0.3];
  ajouterPaire({ id: 'circos-aplat', type: 'fill', source: 'circos', filter: estPoly,
    paint: { 'fill-color': couleurIndex, 'fill-opacity': opacite } }, premierSymbole);
  ajouterPaire({ id: 'cantons-aplat', type: 'fill', source: 'cantons', filter: estPoly,
    layout: { visibility: 'none' },
    paint: { 'fill-color': couleurIndex, 'fill-opacity': opacite } }, premierSymbole);
  carte.addLayer({ id: 'selection-aplat', type: 'fill', source: 'selection',
    filter: ['==', ['get', 'role'], 'commune'],
    paint: { 'fill-color': '#6a2fb0', 'fill-opacity': 0.12 } });

  // Limites, de la plus fine à la plus forte
  carte.addLayer({ id: 'iris-ligne', type: 'line', source: 'iris', filter: estPoly, minzoom: 10,
    paint: { 'line-color': '#1c7ed6', 'line-width': ['interpolate', ['linear'], ['zoom'], 10, 0.5, 15, 1.4], 'line-dasharray': [3, 2], 'line-opacity': 0.85 } });
  carte.addLayer({ id: 'communes-ligne', type: 'line', source: 'communes', filter: estPoly, minzoom: ZOOM_COMMUNES,
    paint: { 'line-color': '#5c636a', 'line-width': ['interpolate', ['linear'], ['zoom'], 8, 0.4, 14, 1.6], 'line-opacity': 0.9 } });
  ajouterPaire({ id: 'cantons-ligne', type: 'line', source: 'cantons', filter: estPoly, minzoom: 6,
    layout: { 'line-join': 'round' },
    paint: { 'line-color': '#e8590c', 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 0.6, 10, 2, 14, 3.2] } });
  ajouterPaire({ id: 'circos-ligne', type: 'line', source: 'circos', filter: estPoly,
    layout: { 'line-join': 'round' },
    paint: { 'line-color': '#7b2cbf', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1, 9, 2.6, 14, 4.5], 'line-opacity': 0.9 } });
  ajouterPaire({ id: 'deps-ligne', type: 'line', source: 'deps', filter: estPoly,
    layout: { 'line-join': 'round' },
    paint: { 'line-color': '#212529', 'line-width': ['interpolate', ['linear'], ['zoom'], 5, 1, 10, 2.5] } });

  // Sélection
  carte.addLayer({ id: 'selection-ligne', type: 'line', source: 'selection',
    layout: { 'line-join': 'round' },
    paint: {
      'line-color': ['match', ['get', 'role'], 'circo', '#7b2cbf', 'canton', '#e8590c', 'iris', '#1c7ed6', '#111111'],
      'line-width': ['match', ['get', 'role'], 'circo', 6, 'canton', 4.5, 'iris', 3, 3],
      'line-opacity': 0.95,
    } });

  // Étiquettes
  const halo = { 'text-halo-color': 'rgba(255,255,255,0.92)', 'text-halo-width': 1.6 };
  carte.addLayer({ id: 'iris-etiquette', type: 'symbol', source: 'iris', filter: estPoint, minzoom: 12.5,
    layout: { 'text-field': ['get', 'nom'], 'text-font': POLICE_ITAL, 'text-size': 11, 'text-max-width': 8, 'text-padding': 4 },
    paint: { 'text-color': '#1864ab', ...halo } });
  carte.addLayer({ id: 'communes-etiquette', type: 'symbol', source: 'communes', filter: estPoint, minzoom: 9.5,
    layout: { 'text-field': ['get', 'nom'], 'text-font': POLICE, 'text-size': ['interpolate', ['linear'], ['zoom'], 9.5, 10.5, 13, 14], 'text-max-width': 8, 'text-padding': 3 },
    paint: { 'text-color': '#343a40', ...halo } });
  carte.addLayer({ id: 'cantons-etiquette', type: 'symbol', source: 'cantons', filter: estPoint, minzoom: 7.5,
    layout: { 'text-field': ['concat', 'Canton ', ['get', 'nom']], 'text-font': POLICE_ITAL, 'text-size': ['interpolate', ['linear'], ['zoom'], 7.5, 10.5, 11, 13], 'text-max-width': 9, 'text-padding': 6 },
    paint: { 'text-color': '#c2410c', ...halo } });
  carte.addLayer({ id: 'circos-etiquette', type: 'symbol', source: 'circos', filter: estPoint, minzoom: 5.5,
    layout: { 'text-field': ['get', 'court'], 'text-font': POLICE_GRAS, 'text-size': ['interpolate', ['linear'], ['zoom'], 5.5, 10, 9, 15], 'text-padding': 8, 'symbol-sort-key': 1 },
    paint: { 'text-color': '#5a189a', ...halo } });
}

/* ---------- Données ---------- */

async function json(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} : HTTP ${r.status}`);
  return r.json();
}

function afficherEtat(msg) { $('#etat').textContent = msg; }

async function chargerNational() {
  afficherEtat('Chargement des contours…');
  const [index, deps, cantons, circos] = await Promise.all([
    json('data/index.json'), json('data/departements.json'), json('data/cantons.json'), json('data/circonscriptions.json'),
  ]);
  etat.index = index;
  etat.deps = preparer(deps);
  etat.cantons = preparer(cantons);
  etat.circos = preparer(circos);
  for (const f of etat.cantons) etat.cantonParCode.set(f.properties.code, f);
  for (const f of etat.circos) etat.circoParCode.set(f.properties.code, f);
  carte.getSource('deps').setData(deps);
  carte.getSource('cantons').setData(cantons);
  carte.getSource('circos').setData(circos);
  construireIndexRecherche();
  afficherEtat('');
}

function chargerDep(dep) {
  if (etat.parDep.has(dep)) return Promise.resolve(etat.parDep.get(dep));
  if (etat.enCours.has(dep)) return etat.enCours.get(dep);
  const fichier = (nom) => json(`data/dep/${dep}/${nom}.json`).catch(() => VIDE);
  const p = Promise.all(['communes', 'iris', 'cantons', 'circos', 'departement'].map(fichier))
    .then(([communes, iris, cantons, circos, departement]) => {
      const d = {
        communes, iris, cantons, circos, departement,
        communesP: preparer(communes), irisP: preparer(iris), cantonsP: preparer(cantons), circosP: preparer(circos),
      };
      etat.parDep.set(dep, d);
      etat.enCours.delete(dep);
      rafraichirSourcesLocales();
      return d;
    });
  etat.enCours.set(dep, p);
  return p;
}

function rafraichirSourcesLocales() {
  const couches = { communes: [], iris: [], 'cantons-det': [], 'circos-det': [], 'deps-det': [] };
  for (const d of etat.parDep.values()) {
    couches.communes.push(...d.communes.features);
    couches.iris.push(...d.iris.features);
    couches['cantons-det'].push(...d.cantons.features);
    couches['circos-det'].push(...d.circos.features);
    couches['deps-det'].push(...d.departement.features);
  }
  for (const [source, features] of Object.entries(couches)) {
    carte.getSource(source).setData({ type: 'FeatureCollection', features });
  }
}

// Version détaillée (alignée sur les communes) d'un canton / d'une circonscription, si son département est chargé.
function detaille(f, role) {
  const d = f && etat.parDep.get(f.properties.dep);
  if (!d) return f;
  return (role === 'canton' ? d.cantonsP : d.circosP).find((x) => x.properties.code === f.properties.code) || f;
}

function depsVisibles() {
  const b = carte.getBounds();
  const v = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  return Object.entries(etat.index.deps)
    .filter(([, d]) => d.bbox[0] < v[2] && d.bbox[2] > v[0] && d.bbox[1] < v[3] && d.bbox[3] > v[1])
    .map(([code]) => code);
}

async function chargerVisibles() {
  if (!etat.index) return;
  if (carte.getZoom() < ZOOM_COMMUNES) {
    afficherEtat('Zoomez pour afficher les communes et les quartiers.');
    return;
  }
  const manquants = depsVisibles().filter((d) => !etat.parDep.has(d));
  if (!manquants.length) { afficherEtat(''); return; }
  afficherEtat('Chargement des communes…');
  try {
    await Promise.all(manquants.map(chargerDep));
    afficherEtat('');
  } catch (e) {
    afficherEtat('Erreur de chargement : ' + e.message);
  }
}

/* ---------- Fiche d'information ---------- */

const nomDep = (code) => etat.index.deps[code]?.nom ?? code;
const nomCanton = (code) => etat.index.cantons[code] ?? code;
const nomCirco = (code) => etat.index.circos[code] ?? code;
const communeIndex = (code) => etat.index.communes.find((c) => c[0] === code);

function selectionner(features) {
  carte.getSource('selection').setData({ type: 'FeatureCollection', features: features.filter(Boolean) });
}

function avecRole(f, role) {
  return f && { type: 'Feature', geometry: f.geometry, properties: { role } };
}

function puces(codes, type, nommer, max = 40) {
  codes = [...codes].sort((a, b) => nommer(a).localeCompare(nommer(b), 'fr', { numeric: true }));
  const html = codes.slice(0, max).map((c) =>
    `<button type="button" class="puce ${type}" data-type="${type}" data-code="${echapper(c)}">${echapper(nommer(c))}</button>`).join('');
  const reste = codes.length > max ? `<span class="plus">+ ${codes.length - max} autres</span>` : '';
  return `<div class="puces">${html}${reste}</div>`;
}

const courtCirco = (code) => {
  const f = etat.circoParCode.get(code);
  if (!f) return code;
  const n = f.properties.num;
  return `${f.properties.dep} – ${n === 1 ? '1re' : n + 'e'} circ.`;
};

function afficherFiche(html) {
  $('#fiche').innerHTML = html;
  for (const b of $('#fiche').querySelectorAll('.puce')) {
    b.addEventListener('click', () => {
      const { type, code } = b.dataset;
      if (type === 'canton') allerCanton(code);
      else if (type === 'circo') allerCirco(code);
      else if (type === 'commune') allerCommune(code);
    });
  }
}

const ligne = (classe, titre, valeur) =>
  `<dt><i class="trait ${classe}"></i>${titre}</dt><dd>${valeur}</dd>`;

async function infosPoint(lngLat) {
  const { lng: x, lat: y } = lngLat;
  const dep = trouver(etat.deps, x, y);
  if (!dep) { selectionner([]); afficherFiche('<p class="vide">Hors de la France métropolitaine.</p>'); return; }
  const depCode = dep.properties.code;
  const donnees = await chargerDep(depCode);
  const canton = trouver(donnees.cantonsP, x, y) || detaille(trouver(etat.cantons, x, y), 'canton');
  const circo = trouver(donnees.circosP, x, y) || detaille(trouver(etat.circos, x, y), 'circo');
  const commune = trouver(donnees.communesP, x, y);
  const iris = trouver(donnees.irisP, x, y);
  selectionner([avecRole(commune, 'commune'), avecRole(iris, 'iris'), avecRole(canton, 'canton'), avecRole(circo, 'circo')]);

  const cp = commune?.properties;
  const ip = iris?.properties;
  let cantonTxt = canton ? echapper(canton.properties.nom) : '—';
  if (!canton && cp?.sanscanton) cantonTxt = `<span class="plus">${echapper(cp.sanscanton)}</span>`;
  let html = `<p class="titre">${echapper(cp?.nom ?? 'Point sélectionné')}</p>
    <p class="contexte">${echapper(nomDep(depCode))} (${depCode})${cp ? ` · code INSEE ${cp.code}` : ''}</p><dl>`;
  if (ip) {
    const arr = ip.nomcom && ip.nomcom !== cp?.nom ? ` <span class="plus">(${echapper(ip.nomcom)})</span>` : '';
    html += ligne('iris', 'Quartier', echapper(ip.nom) + arr);
  }
  html += ligne('canton', 'Canton', cantonTxt);
  html += ligne('circo', 'Circonscription', circo ? echapper(circo.properties.nom) : '—');
  html += '</dl>';
  if (cp && (cp.cantons.length > 1 || cp.circos.length > 1)) {
    html += `<p class="note">${echapper(cp.nom)} est partagée entre ${cp.cantons.length} canton(s) et ${cp.circos.length} circonscription(s) :</p>`;
    if (cp.cantons.length > 1) html += `<div class="bloc"><h3>Cantons</h3>${puces(cp.cantons, 'canton', nomCanton)}</div>`;
    if (cp.circos.length > 1) html += `<div class="bloc"><h3>Circonscriptions</h3>${puces(cp.circos, 'circo', courtCirco)}</div>`;
  }
  afficherFiche(html);
}

async function allerCommune(code) {
  const c = communeIndex(code);
  if (!c) return;
  const [, nom, dep, bbox, cantons, circos] = c;
  carte.fitBounds(bbox, { padding: 60, maxZoom: 13, duration: 900 });
  const d = await chargerDep(dep);
  const f = d.communesP.find((x) => x.properties.code === code);
  const p = f?.properties ?? {};
  selectionner([avecRole(f, 'commune')]);
  let html = `<p class="titre">${echapper(nom)}</p>
    <p class="contexte">${echapper(nomDep(dep))} (${dep}) · code INSEE ${code}${p.pop ? ` · ${p.pop.toLocaleString('fr-FR')} hab.` : ''}</p><dl>`;
  html += ligne('canton', cantons.length > 1 ? 'Cantons' : 'Canton',
    cantons.length ? (cantons.length === 1 ? echapper(nomCanton(cantons[0])) : `${cantons.length} cantons`)
      : `<span class="plus">${echapper(p.sanscanton ?? '—')}</span>`);
  html += ligne('circo', circos.length > 1 ? 'Circonscriptions' : 'Circonscription',
    circos.length === 1 ? echapper(nomCirco(circos[0])) : `${circos.length} circonscriptions`);
  if (p.quartiers) html += ligne('iris', 'Quartiers', `${p.quartiers} IRIS`);
  html += '</dl>';
  if (cantons.length > 1) html += `<div class="bloc"><h3>Cantons</h3>${puces(cantons, 'canton', nomCanton)}</div>`;
  if (circos.length > 1) html += `<div class="bloc"><h3>Circonscriptions</h3>${puces(circos, 'circo', courtCirco)}</div>`;
  if (cantons.length > 1 || circos.length > 1) html += '<p class="note">Cliquez dans la ville pour savoir de quel canton et de quelle circonscription dépend un quartier.</p>';
  afficherFiche(html);
}

function communesDe(champ, code) {
  const i = champ === 'canton' ? 4 : 5;
  return etat.index.communes.filter((c) => c[i].includes(code));
}

function ficheTerritoire(f, role) {
  const p = f.properties;
  const coms = communesDe(role, p.code).sort((a, b) => a[1].localeCompare(b[1], 'fr'));
  const autre = role === 'canton' ? 5 : 4;
  const autres = [...new Set(coms.flatMap((c) => c[autre]))].sort();
  carte.fitBounds(f.bbox, { padding: 50, maxZoom: 12, duration: 900 });
  selectionner([avecRole(f, role)]);
  // contour détaillé dès que le département est chargé
  chargerDep(p.dep).then(() => selectionner([avecRole(detaille(f, role), role)])).catch(() => {});
  let html = `<p class="titre">${role === 'canton' ? 'Canton ' : ''}${echapper(p.nom)}</p>
    <p class="contexte">${echapper(nomDep(p.dep))} (${p.dep}) · ${coms.length} commune(s), entières ou en partie</p>`;
  if (autres.length) {
    html += role === 'canton'
      ? `<div class="bloc"><h3>Circonscription(s) concernée(s)</h3>${puces(autres, 'circo', courtCirco)}</div>`
      : `<div class="bloc"><h3>Cantons concernés</h3>${puces(autres, 'canton', nomCanton)}</div>`;
  }
  html += `<div class="bloc"><h3>Communes</h3>${puces(coms.map((c) => c[0]), 'commune', (code) => communeIndex(code)?.[1] ?? code, 60)}</div>`;
  afficherFiche(html);
}

function allerCanton(code) { const f = etat.cantonParCode.get(code); if (f) ficheTerritoire(f, 'canton'); }
function allerCirco(code) { const f = etat.circoParCode.get(code); if (f) ficheTerritoire(f, 'circo'); }

/* ---------- Recherche ---------- */

let entrees = [];
function construireIndexRecherche() {
  entrees = [];
  for (const c of etat.index.communes) {
    entrees.push({ type: 'commune', code: c[0], nom: c[1], dep: c[2], cle: normaliser(c[1]) });
  }
  for (const f of etat.cantons) {
    const p = f.properties;
    entrees.push({ type: 'canton', code: p.code, nom: `Canton ${p.nom}`, dep: p.dep, cle: normaliser(`canton ${p.nom}`) });
  }
  for (const f of etat.circos) {
    const p = f.properties;
    entrees.push({ type: 'circo', code: p.code, nom: p.nom, dep: p.dep,
      cle: normaliser(`${p.court} ${p.dep}${String(p.num).padStart(2, '0')} ${p.nom} circonscription`) });
  }
}

const LIBELLES = { commune: 'Commune', canton: 'Canton', circo: 'Circonscription' };
let resultats = [], actif = -1;

function chercher(texte) {
  const q = normaliser(texte);
  if (q.length < 2) return [];
  const debut = [], dedans = [];
  for (const e of entrees) {
    if (e.cle.startsWith(q) || e.cle.includes(' ' + q) && e.type !== 'commune') debut.push(e);
    else if (e.cle.includes(q)) dedans.push(e);
    if (debut.length > 60) break;
  }
  const ordre = { commune: 0, canton: 1, circo: 2 };
  debut.sort((a, b) => ordre[a.type] - ordre[b.type] || a.cle.length - b.cle.length);
  return [...debut, ...dedans].slice(0, 12);
}

function afficherResultats() {
  const ul = $('#resultats');
  if (!resultats.length) { ul.hidden = true; $('#q').setAttribute('aria-expanded', 'false'); return; }
  ul.innerHTML = resultats.map((e, i) =>
    `<li role="option" id="r${i}" aria-selected="${i === actif}" data-i="${i}">
      <span>${echapper(e.nom)}</span><span class="dep">${echapper(e.dep)}</span><span class="type">${LIBELLES[e.type]}</span></li>`).join('');
  ul.hidden = false;
  $('#q').setAttribute('aria-expanded', 'true');
  $('#q').setAttribute('aria-activedescendant', actif >= 0 ? `r${actif}` : '');
}

function choisir(e) {
  if (!e) return;
  $('#q').value = e.nom;
  resultats = []; afficherResultats();
  if (e.type === 'commune') allerCommune(e.code);
  else if (e.type === 'canton') allerCanton(e.code);
  else allerCirco(e.code);
}

$('#q').addEventListener('input', (ev) => { resultats = chercher(ev.target.value); actif = resultats.length ? 0 : -1; afficherResultats(); });
$('#q').addEventListener('keydown', (ev) => {
  if (ev.key === 'ArrowDown') { actif = Math.min(actif + 1, resultats.length - 1); afficherResultats(); ev.preventDefault(); }
  else if (ev.key === 'ArrowUp') { actif = Math.max(actif - 1, 0); afficherResultats(); ev.preventDefault(); }
  else if (ev.key === 'Enter') { choisir(resultats[actif]); ev.preventDefault(); }
  else if (ev.key === 'Escape') { resultats = []; afficherResultats(); }
});
$('#resultats').addEventListener('mousedown', (ev) => {
  const li = ev.target.closest('li');
  if (li) { ev.preventDefault(); choisir(resultats[+li.dataset.i]); }
});
$('#q').addEventListener('blur', () => setTimeout(() => { resultats = []; afficherResultats(); }, 150));

/* ---------- Réglages ---------- */

// Un aplat s'affiche s'il est choisi dans « Couleurs » ET si sa couche est cochée dans « Limites ».
function appliquerCouleur() {
  const mode = document.querySelector('input[name="couleur"]:checked')?.value;
  const coche = (nom) => document.querySelector(`input[data-couche="${nom}"]`)?.checked;
  for (const nom of ['circos', 'cantons']) {
    const visible = mode === nom && coche(nom);
    // chaque aplat existe en deux couches : vue d'ensemble et détaillée (zoom ≥ ZOOM_COMMUNES)
    for (const id of [`${nom}-aplat`, `${nom}-aplat-det`]) {
      carte.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
    }
  }
}

// Rôle de la sélection (contour épais après un clic) correspondant à chaque case de la légende.
const ROLE_SELECTION = { circos: 'circo', cantons: 'canton', communes: 'commune', iris: 'iris' };
const rolesMasques = new Set();

function appliquerCouche(nom, visible) {
  for (const id of COUCHES[nom]) carte.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
  appliquerCouleur();
  // une couche décochée disparaît aussi de la sélection en cours
  const role = ROLE_SELECTION[nom];
  if (!role) return;
  if (visible) rolesMasques.delete(role); else rolesMasques.add(role);
  const visibles = ['!', ['in', ['get', 'role'], ['literal', [...rolesMasques]]]];
  carte.setFilter('selection-ligne', visibles);
  carte.setFilter('selection-aplat', ['all', ['==', ['get', 'role'], 'commune'], visibles]);
}

for (const r of document.querySelectorAll('input[name="couleur"]')) {
  r.addEventListener('change', appliquerCouleur);
}
for (const c of document.querySelectorAll('input[data-couche]')) {
  c.addEventListener('change', () => appliquerCouche(c.dataset.couche, c.checked));
}

/* ---------- Démarrage ---------- */

carte.on('load', async () => {
  ajouterCouches();
  try {
    await chargerNational();
  } catch (e) {
    afficherEtat('Impossible de charger les données : ' + e.message);
    return;
  }
  chargerVisibles();
  carte.on('moveend', chargerVisibles);
  carte.on('click', (ev) => infosPoint(ev.lngLat));
});
