// Odtwarzacz przepływów magazynu w widoku 3D modelu (three.js).
//
// Dane = scena „palviz.blender-flow" z /magazyn/model/<pk>/przeplywy.json — ta sama co eksport
// do Blendera. Trasy (A*), osie czasu wózków i ludzi oraz palety na stanie liczy GROOVE
// (wh3d/blender_scene.py, blender_agents.py, blender_stock.py); tutaj tylko liniowa
// interpolacja klatek kluczowych, jak w tools/blender/palviz_warehouse_anim.py.
//
// Układ: hala x → three X, hala y („w głąb") → three Z, wysokość z → three Y.
// Kierunek heading [°] (0 = +x, 90 = +y) → rotation.y = −heading (jak rotation.y regałów).
import * as THREE from 'three';

const FLOW_Y = { inbound: 0.03, outbound: 0.05, picking: 0.07, replenishment: 0.09, transfer: 0.11 };
const FLOW_LABELS = { inbound: 'Przyjęcie (dok → regał)', outbound: 'Wydanie (regał → dok)', picking: 'Kompletacja',
                      replenishment: 'Uzupełnienie (regał → regał)', transfer: 'Przesunięcie (regał → regał)' };
const WOOD = 0xb45309, LOAD = 0xc8a26a, FORK = 0x27272a, SKIN = 0xf2c9a0, TROUSERS = 0x374151;
const NO_DATA = '#a1a1aa';
const SKU_PALETTE = ['#2563eb', '#16a34a', '#f59e0b', '#dc2626', '#7c3aed', '#0891b2',
                     '#db2777', '#65a30d', '#ea580c', '#4f46e5', '#0d9488', '#a16207'];
const DEG = Math.PI / 180;

// ── Kolor palety wg trybu — lustro pallet_color() ze skryptu Blendera ──────────────
export function palletColor(p, mode, maxPicks) {
  if (p.state === 'blocked_empty') return ['#dc2626', 'Zablokowana (pusta)'];
  if (mode === 'state') return p.state === 'blocked' ? ['#dc2626', 'Zablokowana'] : ['#c8a26a', 'Zajęta'];
  if (mode === 'sku') {
    if (!p.sku) return [NO_DATA, 'Brak SKU (tylko SAP)'];
    let s = 0; for (const ch of p.sku) s += ch.charCodeAt(0);
    return [SKU_PALETTE[s % SKU_PALETTE.length], 'SKU (kolor = indeks)'];
  }
  if (mode === 'abc') {
    return ({ A: ['#dc2626', 'A — najczęściej pobierane'], B: ['#f59e0b', 'B'],
              C: ['#3b82f6', 'C — rzadko'] })[p.abc] || [NO_DATA, 'Brak pobrań'];
  }
  if (mode === 'expiry') {
    const d = p.days_to_expiry;
    if (d === null || d === undefined) return [NO_DATA, 'Brak terminu'];
    if (d < 0) return ['#7f1d1d', 'Po terminie'];
    if (d < 30) return ['#dc2626', '< 30 dni'];
    if (d < 90) return ['#f59e0b', '30–90 dni'];
    return ['#16a34a', '> 90 dni'];
  }
  const n = p.picks || 0;                                        // mode === 'picks'
  if (!n) return [NO_DATA, '0 pobrań'];
  const share = n / Math.max(1, maxPicks);
  if (share > 0.66) return ['#dc2626', 'Dużo pobrań (top ⅓)'];
  if (share > 0.33) return ['#f59e0b', 'Średnio pobrań'];
  return ['#3b82f6', 'Mało pobrań'];
}

// ── Interpolacja klatek kluczowych (wyszukiwanie binarne, liniowo) ──────────────────
export function sampleKeyframes(kfs, t, out = {}) {
  const n = kfs.length;
  let a = kfs[0], b = a, f = 0;
  if (n > 1 && t > kfs[0].t) {
    if (t >= kfs[n - 1].t) { a = b = kfs[n - 1]; }
    else {
      let lo = 0, hi = n - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (kfs[mid].t <= t) lo = mid; else hi = mid; }
      a = kfs[lo]; b = kfs[hi]; f = (t - a.t) / ((b.t - a.t) || 1);
    }
  }
  out.x = a.x + (b.x - a.x) * f;
  out.y = a.y + (b.y - a.y) * f;
  out.z = (a.z || 0) + ((b.z || 0) - (a.z || 0)) * f;
  out.heading = a.heading + (b.heading - a.heading) * f;       // kąty już „rozwinięte" po stronie GROOVE
  out.lift = (a.lift || 0) + ((b.lift || 0) - (a.lift || 0)) * f;
  return out;
}

export function fmtTime(s) {
  s = Math.max(0, Math.round(s));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const mm = String(m).padStart(h ? 2 : 1, '0'), ss = String(sec).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// ── Geometria pomocnicza ───────────────────────────────────────────────────────────
const UNIT_BOX = new THREE.BoxGeometry(1, 1, 1);
const _m = new THREE.Matrix4(), _q = new THREE.Quaternion(), _p = new THREE.Vector3(),
      _s = new THREE.Vector3(), _e = new THREE.Euler(), _up = new THREE.Vector3(0, 1, 0);

// Pudełka jak w skrypcie Blendera: (cx, cy, cz, sx, sy, sz, idx) — X do przodu, Z w górę.
function boxes(list, mats, parent) {
  for (const [cx, cy, cz, sx, sy, sz, mi] of list) {
    const mesh = new THREE.Mesh(UNIT_BOX, mats[mi]);
    mesh.position.set(cx, cz, cy); mesh.scale.set(sx, sz, sy);
    mesh.castShadow = true;
    parent.add(mesh);
  }
}

function labelSprite(text) {
  const cv = document.createElement('canvas'); cv.width = 256; cv.height = 56;
  const x = cv.getContext('2d');
  x.fillStyle = 'rgba(15,23,42,0.78)'; x.fillRect(0, 0, 256, 56);
  x.fillStyle = '#fff'; x.font = 'bold 26px sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle';
  x.fillText(String(text).slice(0, 18), 128, 29);
  const tex = new THREE.CanvasTexture(cv); tex.colorSpace = THREE.SRGBColorSpace;
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
  spr.scale.set(2.2, 0.48, 1); spr.renderOrder = 10;
  return spr;
}

// Przepływ jako płaska „wstęga" na posadzce (linie WebGL mają zawsze 1 px — za cienkie).
function ribbon(polylines, width, y) {
  const pos = [], half = width / 2;
  for (const pts of polylines) {
    for (let i = 1; i < pts.length; i++) {
      const [x0, z0] = pts[i - 1], [x1, z1] = pts[i];
      const len = Math.hypot(x1 - x0, z1 - z0);
      if (len < 1e-6) continue;
      const nx = -(z1 - z0) / len * half, nz = (x1 - x0) / len * half;
      const a = [x0 + nx, y, z0 + nz], b = [x0 - nx, y, z0 - nz], c = [x1 + nx, y, z1 + nz], d = [x1 - nx, y, z1 - nz];
      pos.push(...a, ...b, ...c, ...b, ...d, ...c);
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
  return g;
}

function disposeTree(obj) {
  obj.traverse(o => {
    if (o.geometry && o.geometry !== UNIT_BOX) o.geometry.dispose();
    const mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
    mats.forEach(m => { if (m.map) m.map.dispose(); m.dispose(); });
  });
}

// ═══════════════════════════════════════════════════════════════════════════════════
const SIM_KEYS = ['sim', 'sim_h', 'p', 'mult', 'agv', 'kombi', 'ept', 'kt', 'kp'];

export function createFlowPlayer({ scene, camera, canvas, requestRender, root, url }) {
  const simParams = new URLSearchParams(window.location.search);
  const $ = sel => root.querySelector(sel);
  const ui = {
    batch: $('[data-f="batch"]'), pickers: $('[data-f="pickers"]'), picks: $('[data-f="picks"]'),
    forklifts: $('[data-f="forklifts"]'), wt: $('[data-f="wt"]'), wtFrom: $('[data-f="wt-from"]'),
    wtHours: $('[data-f="wt-hours"]'), wtScale: $('[data-f="wt-scale"]'), stock: $('[data-f="stock"]'), load: $('[data-f="load"]'),
    status: $('[data-f="status"]'), player: $('[data-f="player"]'), play: $('[data-f="play"]'),
    speed: $('[data-f="speed"]'), seek: $('[data-f="seek"]'), time: $('[data-f="time"]'),
    showFlows: $('[data-f="show-flows"]'), showPallets: $('[data-f="show-pallets"]'),
    showLabels: $('[data-f="show-labels"]'), colorBy: $('[data-f="color-by"]'),
    info: $('[data-f="info"]'), legend: $('[data-f="legend"]'),
  };
  let group = null, data = null, t = 0, duration = 0, playing = false, last = 0, loopId = 0;
  let agents = [], movers = null, stock = null, flowMeshes = [], labels = [];
  const tmp = {};

  const palletInfo = document.createElement('div');
  palletInfo.className = 'flow-pallet-info'; palletInfo.hidden = true;
  canvas.parentElement.appendChild(palletInfo);

  // ── Budowa sceny ──────────────────────────────────────────────────────────────────
  function clear() {
    setPlaying(false);
    if (group) { scene.remove(group); disposeTree(group); }
    group = null; agents = []; movers = null; stock = null; flowMeshes = []; labels = [];
    palletInfo.hidden = true;
  }

  function buildAgent(a) {
    const rootObj = new THREE.Group();
    const color = new THREE.MeshStandardMaterial({ color: a.color, roughness: 0.55, metalness: 0.1 });
    let carriage = null;
    if (a.kind === 'forklift') {
      const dark = new THREE.MeshStandardMaterial({ color: FORK, roughness: 0.6, metalness: 0.4 });
      boxes([[-0.2, 0, 0.45, 1.5, 0.95, 0.6, 0], [-0.6, 0, 1.0, 0.5, 0.8, 0.5, 0], [-0.15, 0, 1.55, 0.9, 0.9, 0.06, 1],
             [0.2, 0.4, 1.0, 0.05, 0.05, 1.1, 1], [0.2, -0.4, 1.0, 0.05, 0.05, 1.1, 1],
             [0.62, 0, 1.2, 0.08, 0.7, 2.2, 1]], [color, dark], rootObj);
      carriage = new THREE.Group();
      boxes([[1.2, 0.22, 0.05, 1.1, 0.1, 0.04, 0], [1.2, -0.22, 0.05, 1.1, 0.1, 0.04, 0],
             [0.7, 0, 0.4, 0.06, 0.8, 0.8, 0]], [dark], carriage);
      rootObj.add(carriage);
    } else if (a.kind === 'kombi') {
      // Wózek systemowy VNA: maszt 7 m, kabina operatora jedzie w górę razem z widłami.
      const dark = new THREE.MeshStandardMaterial({ color: FORK, roughness: 0.6, metalness: 0.4 });
      boxes([[-0.3, 0, 0.4, 2.6, 1.4, 0.8, 0], [0.95, 0, 3.5, 0.15, 1.2, 7.0, 1]], [color, dark], rootObj);
      carriage = new THREE.Group();
      boxes([[0.5, 0, 1.25, 0.8, 1.0, 1.2, 0], [0.5, 0, 1.9, 0.9, 1.1, 0.06, 1],
             [1.3, 0.22, 0.05, 1.1, 0.1, 0.04, 1], [1.3, -0.22, 0.05, 1.1, 0.1, 0.04, 1]], [color, dark], carriage);
      rootObj.add(carriage);
    } else if (a.kind === 'agv') {
      const dark = new THREE.MeshStandardMaterial({ color: FORK, roughness: 0.6, metalness: 0.4 });
      boxes([[0, 0, 0.17, 1.3, 0.9, 0.3, 0], [0, 0, 0.33, 1.2, 0.8, 0.04, 1], [0.6, 0, 0.42, 0.1, 0.1, 0.12, 1]],
            [color, dark], rootObj);
    } else {
      // Pracownik; „ept" = ten sam pracownik na elektrycznym wózku paletowym (widły z przodu).
      const x = a.kind === 'ept' ? -0.45 : 0;
      boxes([[x, 0, 0.45, 0.22, 0.3, 0.9, 1], [x, 0, 1.2, 0.26, 0.46, 0.62, 0],
             [x, 0, 1.66, 0.22, 0.2, 0.26, 2], [x + 0.14, 0, 1.2, 0.05, 0.3, 0.05, 0]],
            [color, new THREE.MeshStandardMaterial({ color: TROUSERS, roughness: 0.8 }),
             new THREE.MeshStandardMaterial({ color: SKIN, roughness: 0.7 })], rootObj);
      if (a.kind === 'ept') {
        const dark = new THREE.MeshStandardMaterial({ color: FORK, roughness: 0.6, metalness: 0.4 });
        boxes([[0.1, 0, 0.45, 0.5, 0.7, 0.8, 0], [-0.45, 0, 0.05, 0.5, 0.7, 0.1, 1],
               [0.9, 0.25, 0.08, 1.2, 0.16, 0.08, 1], [0.9, -0.25, 0.08, 1.2, 0.16, 0.08, 1]], [color, dark], rootObj);
      }
    }
    const lbl = labelSprite(a.label);
    lbl.position.set(0, { forklift: 3.0, kombi: 7.8, agv: 1.6, ept: 2.4 }[a.kind] || 2.3, 0);
    rootObj.add(lbl); labels.push(lbl);
    group.add(rootObj);
    return { a, obj: rootObj, carriage };
  }

  // Ruchome ładunki: 3 InstancedMesh (podstawa palety, ładunek palety, karton) → 3 draw calle.
  function buildMovers(items) {
    const pallets = items.filter(i => i.kind === 'pallet'), cartons = items.filter(i => i.kind === 'carton');
    const containers = items.filter(i => i.kind === 'container');
    const mk = (n, color) => {
      const m = new THREE.InstancedMesh(UNIT_BOX, new THREE.MeshStandardMaterial({ color, roughness: 0.75 }), Math.max(1, n));
      m.castShadow = true; m.frustumCulled = false; group.add(m); return m;
    };
    return { pallets, cartons, containers, base: mk(pallets.length, WOOD), load: mk(pallets.length, LOAD),
             box: mk(cartons.length, 0xb08850), cont: mk(containers.length, 0x1e40af) };
  }

  function setInstance(mesh, i, visible, x, y, z, heading, sx, sy, sz) {
    if (!visible) { _m.makeScale(0, 0, 0); mesh.setMatrixAt(i, _m); return; }
    _q.setFromAxisAngle(_up, -heading * DEG);
    _m.compose(_p.set(x, y, z), _q, _s.set(sx, sy, sz));
    mesh.setMatrixAt(i, _m);
  }

  function buildStock(pallets) {
    const withLoad = pallets.filter(p => p.h > 0.16);
    const base = new THREE.InstancedMesh(UNIT_BOX, new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.8 }), Math.max(1, pallets.length));
    const load = new THREE.InstancedMesh(UNIT_BOX, new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.7 }), Math.max(1, withLoad.length));
    const wood = new THREE.Color(WOOD), blocked = new THREE.Color('#dc2626');
    pallets.forEach((p, i) => {
      const bh = Math.min(0.144, p.h);
      setInstance(base, i, true, p.x, p.z + bh / 2, p.y, p.heading, p.d, bh, p.w);
      base.setColorAt(i, p.state === 'blocked_empty' ? blocked : wood);   // pusta, a zablokowana
    });
    withLoad.forEach((p, i) => {
      const bh = 0.144, lh = p.h - bh;
      setInstance(load, i, true, p.x, p.z + bh + lh / 2, p.y, p.heading, p.d - 0.04, lh, p.w - 0.04);
    });
    base.count = pallets.length; load.count = withLoad.length;
    group.add(base, load);
    const maxPicks = pallets.reduce((m, p) => Math.max(m, p.picks || 0), 1);
    return { pallets, withLoad, base, load, maxPicks };
  }

  function recolor() {
    if (!stock) return;
    const mode = ui.colorBy.value, seen = new Map(), c = new THREE.Color();
    stock.withLoad.forEach((p, i) => {
      const [hex, label] = palletColor(p, mode, stock.maxPicks);
      stock.load.setColorAt(i, c.set(hex)); seen.set(label, hex);
    });
    stock.pallets.filter(p => p.state === 'blocked_empty').forEach(p => seen.set(palletColor(p, mode)[1], '#dc2626'));
    if (stock.load.instanceColor) stock.load.instanceColor.needsUpdate = true;
    renderLegend(seen);
    requestRender();
  }

  function renderLegend(palletEntries) {
    const chip = (hex, text) => `<span class="flow-chip"><i style="background:${hex}"></i>${text}</span>`;
    const colors = data.flow_colors || {};
    const kinds = [...new Set(data.flows.map(f => f.kind))];
    let html = kinds.map(k => chip(colors[k] || '#6b7280', FLOW_LABELS[k] || k)).join('');
    if (palletEntries && palletEntries.size && ui.showPallets.checked) {
      html += '<span class="flow-legend__sep"></span>' + [...palletEntries].map(([l, h]) => chip(h, l)).join('');
    }
    ui.legend.innerHTML = html;
  }

  function build(sc) {
    clear();
    data = sc; group = new THREE.Group(); group.name = 'PalViz przepływy';
    scene.add(group);
    agents = sc.agents.map(buildAgent);
    movers = buildMovers(sc.items);
    // Trasy: jedna wstęga na rodzaj przepływu (3 draw calle niezależnie od liczby tras).
    const byKind = {};
    sc.flows.forEach(f => (byKind[f.kind] = byKind[f.kind] || []).push(f.points));
    flowMeshes = Object.entries(byKind).map(([kind, lines]) => {
      const mesh = new THREE.Mesh(ribbon(lines, 0.16, FLOW_Y[kind] || 0.04),
        new THREE.MeshBasicMaterial({ color: (sc.flow_colors || {})[kind] || '#6b7280', transparent: true,
                                      opacity: 0.55, depthWrite: false, side: THREE.DoubleSide }));
      mesh.renderOrder = 2; group.add(mesh); return mesh;
    });
    // Przenośniki teleskopowe (kontener → paletyzacja): stała bryła pod jadącymi kartonami.
    const belt = new THREE.MeshStandardMaterial({ color: 0x9ca3af, roughness: 0.5, metalness: 0.5 });
    (sc.conveyors || []).forEach(cv => {
      for (let i = 1; i < cv.points.length; i++) {
        const [x0, z0] = cv.points[i - 1], [x1, z1] = cv.points[i], len = Math.hypot(x1 - x0, z1 - z0);
        const m = new THREE.Mesh(UNIT_BOX, belt);
        m.position.set((x0 + x1) / 2, cv.height / 2, (z0 + z1) / 2);
        m.scale.set(len, cv.height, cv.width);
        m.rotation.y = -Math.atan2(z1 - z0, x1 - x0);
        group.add(m);
      }
    });
    stock = sc.pallets && sc.pallets.length ? buildStock(sc.pallets) : null;
    duration = sc.duration || 0;
    ui.seek.max = String(duration);
    ui.player.hidden = false;
    applyVisibility(); recolor(); if (!stock) renderLegend(null);
    renderInfo();
    seek(0);
    setPlaying(true);
  }

  function renderInfo() {
    const src = data.source || {}, st = data.stock_stats;
    const people = data.agents.filter(a => a.kind === 'person' || a.kind === 'ept').length;
    const trucks = data.agents.filter(a => a.kind !== 'person' && a.kind !== 'ept').length;
    const parts = [];
    if (src.simulation) {
      const s = src.simulation, f = s.fleet || {};
      parts.push(`<b>Symulacja dnia projektowego:</b> import „${escapeHtml(s.batch || '')}”, dzień P${s.p} (${escapeHtml(s.day || '')}), ` +
        `mnożnik ×${s.mult}, flota: AGV ${f.agv}, kombi ${f.kombi}, EPT ${f.ept} — godzina ${s.hour}:00–${s.hour + 1}:00`);
      parts.push(`<b>Ruchy:</b> ${s.legs} z ${s.total} rozpoczętych w tej godzinie` +
        (s.truncated ? ` · <b>przycięto do ${s.limit}</b>` : '') +
        ' — te same przydziały i chwile startu co w tabeli symulacji; trasy alejkami i przejazdami poprzecznymi');
      ui.info.innerHTML = parts.join('<br>');
      return;
    }
    parts.push(src.picking === 'picker_activity'
      ? `<b>Kompletacja:</b> realna kolejność pobrań z importu „${escapeHtml(src.batch || '')}” — ${people} pickerów`
      : `<b>Kompletacja:</b> symulacja demo (${people} pickerów${data.agents.some(a => a.kind === 'ept') ? ' na wózkach EPT, półki K1' : ''})`);
    const wt = src.tasks;
    if (src.forklifts === 'ewm_tasks' && wt) {
      let s = `<b>Wózki:</b> ${trucks} (zasoby EWM) — zadania z importu „${escapeHtml(src.tasks_batch || '')}”, ` +
        `okno ${fmtStamp(src.window.from)} – ${fmtStamp(src.window.to)}: ${wt.animated} z ${wt.total} zadań`;
      if (wt.skipped_unmapped) s += ` · ${wt.skipped_unmapped} pominiętych (lokalizacje spoza modelu)`;
      if (wt.truncated) s += ` · <b>przycięto do limitu ${wt.limit} zadań</b> — zawęź okno, żeby zobaczyć resztę`;
      if (!wt.total) s += ' — brak potwierdzonych zadań w tym oknie, wybierz inną godzinę';
      parts.push(s);
    } else {
      parts.push(src.equipment === 'agv_kombi'
        ? `<b>Wózki:</b> ${trucks} — symulacja demo: AGV wozi palety dok ↔ czoło rzędu, wózek kombi odkłada je w regale wysokiego składowania`
        : `<b>Wózki:</b> ${trucks} — symulacja demo (wybierz import zadań EWM, żeby zobaczyć realne ruchy)`);
    }
    if (st) {
      let s = `<b>Palety na stanie:</b> ${st.pallets} (${src.snapshot ? `stan HU + snapshot „${escapeHtml(src.snapshot)}”` : 'stan HU'})`;
      if (st.unmapped) s += ` · ${st.unmapped} lokalizacji spoza modelu`;
      if (st.truncated) s += ' · obcięto do limitu palet';
      parts.push(s);
    }
    parts.push(src.forklifts === 'ewm_tasks'
      ? `<b>Czas:</b> ${fmtTime(duration)} — wózek rusza w chwili potwierdzenia zadania w EWM` +
        `${src.time_scale > 1 ? ` (postoje między zadaniami skrócone ×${src.time_scale})` : ''}, jazda wg tras i prędkości sprzętu`
      : `<b>Czas:</b> ${fmtTime(duration)} — czasy z tras i prędkości sprzętu, nie ze znaczników SAP`);
    ui.info.innerHTML = parts.join('<br>');
  }

  // ── Klatka animacji ─────────────────────────────────────────────────────────────
  function apply(time) {
    for (const g of agents) {
      const s = sampleKeyframes(g.a.keyframes, time, tmp);
      g.obj.position.set(s.x, 0, s.y);
      g.obj.rotation.y = -s.heading * DEG;
      if (g.carriage) g.carriage.position.y = s.lift;
    }
    const vis = it => time >= it.appear && (it.vanish === null || it.vanish === undefined || time < it.vanish);
    movers.pallets.forEach((it, i) => {
      const s = sampleKeyframes(it.keyframes, time, tmp), [L, W, H] = it.size, v = vis(it);
      setInstance(movers.base, i, v, s.x, s.z + 0.075, s.y, s.heading, L, 0.15, W);
      setInstance(movers.load, i, v, s.x, s.z + 0.15 + (H - 0.15) / 2, s.y, s.heading, L - 0.1, H - 0.15, W - 0.1);
    });
    movers.cartons.forEach((it, i) => {
      const s = sampleKeyframes(it.keyframes, time, tmp), [L, W, H] = it.size;
      setInstance(movers.box, i, vis(it), s.x, s.z + H / 2, s.y, s.heading, L, H, W);
    });
    movers.containers.forEach((it, i) => {
      const s = sampleKeyframes(it.keyframes, time, tmp), [L, W, H] = it.size;
      setInstance(movers.cont, i, vis(it), s.x, H / 2, s.y, s.heading, L, H, W);
    });
    movers.base.count = movers.load.count = movers.pallets.length;
    movers.box.count = movers.cartons.length;
    movers.cont.count = movers.containers.length;
    [movers.base, movers.load, movers.box, movers.cont].forEach(m => { m.instanceMatrix.needsUpdate = true; });
  }

  function seek(time) {
    t = Math.max(0, Math.min(duration, time));
    apply(t);
    ui.seek.value = String(t);
    ui.time.textContent = `${fmtTime(t)} / ${fmtTime(duration)}`;
    requestRender();
  }

  // `id` = pokolenie pętli: pauza→play w jednej klatce albo przeładowanie sceny w trakcie
  // odtwarzania nie może zostawić starej pętli rAF (dwie pętle = podwójna prędkość).
  function tick(now, id) {
    if (!playing || id !== loopId) return;
    // Limit kroku: przy niskim FPS (słabe GPU) czas nadal płynie ~realnie; po powrocie karty
    // z tła `last` jest resetowany (visibilitychange), więc nie ma skoku o całą przerwę.
    const dt = Math.min(0.5, (now - last) / 1000);
    last = now;
    seek(t + dt * Number(ui.speed.value || 1));
    if (t >= duration) { setPlaying(false); return; }
    requestAnimationFrame(ts => tick(ts, id));
  }

  function setPlaying(on) {
    playing = on && duration > 0;
    const id = ++loopId;
    ui.play.textContent = playing ? '❚❚' : '▶';
    ui.play.setAttribute('aria-label', playing ? 'Pauza' : 'Odtwórz');
    if (playing) {
      if (t >= duration) t = 0;
      last = performance.now();
      requestAnimationFrame(ts => tick(ts, id));
    }
  }

  function applyVisibility() {
    flowMeshes.forEach(m => { m.visible = ui.showFlows.checked; });
    if (stock) stock.base.visible = stock.load.visible = ui.showPallets.checked;
    labels.forEach(l => { l.visible = ui.showLabels.checked; });
    requestRender();
  }

  // ── Klik w paletę na stanie → karta lokalizacji ─────────────────────────────────
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  let down = null;
  canvas.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; });
  canvas.addEventListener('pointerup', e => {
    if (!down || !stock || !ui.showPallets.checked) return;
    const moved = Math.hypot(e.clientX - down[0], e.clientY - down[1]); down = null;
    if (moved > 5) return;                                    // obrót kamery, nie klik
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const hit = ray.intersectObjects([stock.load, stock.base], false)[0];
    if (!hit || hit.instanceId === undefined) { palletInfo.hidden = true; return; }
    const p = hit.object === stock.load ? stock.withLoad[hit.instanceId] : stock.pallets[hit.instanceId];
    showPallet(p);
  });

  function showPallet(p) {
    const row = (k, v) => (v === null || v === undefined || v === '' ? '' :
      `<div class="flow-pallet-info__row"><span>${k}</span><b>${escapeHtml(v)}</b></div>`);
    const state = { occupied: 'zajęta', blocked: 'zablokowana', blocked_empty: 'zablokowana (pusta)' }[p.state] || p.state;
    palletInfo.innerHTML = `<button type="button" class="flow-pallet-info__x" aria-label="Zamknij">&times;</button>
      <div class="flow-pallet-info__title">${escapeHtml(p.code)}</div>` +
      row('Stan', state) + row('SKU', p.sku) + row('Nazwa', p.name) + row('Partia', p.lot) +
      row('Ilość', p.qty ? `${p.qty} ${p.unit || ''}` : '') + row('Termin', p.expiry) +
      row('Klasa ABC', p.abc) + row('Pobrania', p.picks || '') +
      row('HU', (p.hu || []).slice(0, 4).join(', ') + ((p.hu || []).length > 4 ? '…' : ''));
    palletInfo.hidden = false;
    palletInfo.querySelector('button').onclick = () => { palletInfo.hidden = true; };
  }
  function fmtStamp(iso) {                                   // „2026-03-02T06:00” → „02.03 06:00”
    const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(iso || '');
    return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}` : escapeHtml(iso || '');
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[ch]);
  }

  // ── Ładowanie sceny z GROOVE ──────────────────────────────────────────────────────
  async function load() {
    const q = new URLSearchParams({ batch: ui.batch.value, pickers: ui.pickers.value, picks: ui.picks.value,
                                    forklifts: ui.forklifts.value });
    if (ui.stock.value === 'none') q.set('pallets', '0'); else if (ui.stock.value) q.set('snapshot', ui.stock.value);
    if (ui.wt.value) {
      q.set('wt', ui.wt.value); q.set('wt_hours', ui.wtHours.value); q.set('wt_scale', ui.wtScale.value);
      if (ui.wtFrom.value) q.set('wt_from', ui.wtFrom.value);
    }
    // Godzina z symulacji dnia projektowego (link „Animuj w 3D” z ekranu symulacji) — parametry z adresu.
    SIM_KEYS.forEach(k => { if (simParams.get(k)) q.set(k, simParams.get(k)); });
    ui.load.disabled = true;
    ui.status.textContent = 'Liczę trasy wózków i pickerów…';
    try {
      const resp = await fetch(`${url}?${q}`, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const sc = await resp.json();
      if (!sc.agents || !sc.agents.length) {
        clear(); ui.player.hidden = true;
        ui.status.textContent = sc.racks && sc.racks.length
          ? 'Brak ruchów do animacji w tym oknie — wybierz inną godzinę albo dłuższe okno.'
          : 'Brak regałów w modelu — nie ma czego animować.';
        return;
      }
      build(sc);
      ui.status.textContent = '';
    } catch (err) {
      ui.status.textContent = `Nie udało się wczytać animacji (${err.message}). Spróbuj ponownie.`;
    } finally {
      ui.load.disabled = false;
    }
  }

  if (simParams.get('sim')) load();

  // Demo vs zadania EWM: inne pola; start okna = 1. godzina wybranego importu.
  function syncSource() {
    const real = !!ui.wt.value;
    root.querySelectorAll('[data-f="demo-only"]').forEach(el => { el.hidden = real; });
    root.querySelectorAll('[data-f="wt-only"]').forEach(el => { el.hidden = !real; });
    const opt = ui.wt.selectedOptions[0];
    if (real && opt && opt.dataset.start) ui.wtFrom.value = opt.dataset.start;
  }
  ui.wt.addEventListener('change', syncSource);
  syncSource();

  document.addEventListener('visibilitychange', () => { last = performance.now(); });
  ui.load.addEventListener('click', load);
  ui.play.addEventListener('click', () => setPlaying(!playing));
  ui.seek.addEventListener('input', () => { setPlaying(false); seek(Number(ui.seek.value)); });
  [ui.showFlows, ui.showPallets, ui.showLabels].forEach(el => el.addEventListener('change', () => {
    applyVisibility(); if (el === ui.showPallets && data) recolor();
  }));
  ui.colorBy.addEventListener('change', recolor);
  return { load, seek, setPlaying, get time() { return t; }, get duration() { return duration; } };
}
