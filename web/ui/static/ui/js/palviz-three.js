// palviz-three.js — realistic Euro pallet + cardboard cartons (bez napisów, pełna nieprzezroczystość)
// Import ze sciezki (nie bare 'three'): stare Chrome na terminalach (MC330L,
// Android 8.1) nie wspiera <script type=importmap> i modele 3D w ogole sie nie
// renderowaly. Importmapy w szablonach zostaly jako nieszkodliwe.
import * as THREE from '../vendor/three.module.js';
import { GLTFLoader } from '../vendor/addons/loaders/GLTFLoader.js';

// ── Material palette ──────────────────────────────────────────────────────────
const C = {
  boardTop:   0xD4B896,   // light pine — top deck boards
  boardMid:   0xBE9060,   // medium wood — stringers
  boardBot:   0xC8A46A,   // warm wood — bottom boards
  cardBase:   0xE7D6B6,   // light beige kraft tone (cartons)
  edgePallet: 0x3A1A05,
  edgeCarton: 0x5C3317,
  edgeGeneric:0x374151,
};

// ── Procedural kraft-cardboard texture (cached) ───────────────────────────────
let _kraftTex = null;
function kraftTexture() {
  if (_kraftTex) return _kraftTex;
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  x.fillStyle = '#e3d2b0'; x.fillRect(0, 0, 256, 256);    // light beige base
  for (let i = 0; i < 16000; i++) {                       // subtle fibre speckle
    x.fillStyle = Math.random() > 0.5 ? 'rgba(160,130,90,0.035)' : 'rgba(248,238,214,0.05)';
    x.fillRect(Math.random() * 256, Math.random() * 256, 1.5, 1.5);
  }
  x.globalAlpha = 0.03;                                   // faint flute lines
  for (let yy = 0; yy < 256; yy += 4) { x.fillStyle = (yy / 4) % 2 ? '#b89a6a' : '#f0e3c8'; x.fillRect(0, yy, 256, 2); }
  x.globalAlpha = 1;
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  if ('SRGBColorSpace' in THREE) t.colorSpace = THREE.SRGBColorSpace;
  _kraftTex = t; return t;
}

// Top face of a carton: kraft + brown packing-tape stripe down the middle + flap seam.
let _kraftTopTex = null;
function kraftTopTexture() {
  if (_kraftTopTex) return _kraftTopTex;
  const c = document.createElement('canvas'); c.width = c.height = 256;
  const x = c.getContext('2d');
  x.drawImage(kraftTexture().image, 0, 0);
  x.strokeStyle = 'rgba(92,51,23,0.35)';                  // flap seam line
  x.lineWidth = 2; x.beginPath(); x.moveTo(128, 0); x.lineTo(128, 256); x.stroke();
  const grad = x.createLinearGradient(104, 0, 152, 0);    // glossy tape stripe
  grad.addColorStop(0, 'rgba(150,96,42,0.55)');
  grad.addColorStop(0.5, 'rgba(196,140,72,0.75)');
  grad.addColorStop(1, 'rgba(150,96,42,0.55)');
  x.fillStyle = grad; x.fillRect(104, 0, 48, 256);
  x.fillStyle = 'rgba(255,235,200,0.25)';                 // tape highlight
  x.fillRect(112, 0, 6, 256);
  const t = new THREE.CanvasTexture(c);
  if ('SRGBColorSpace' in THREE) t.colorSpace = THREE.SRGBColorSpace;
  _kraftTopTex = t; return t;
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function makeEdges(geo, color, opacity = 0.5) {
  const mat = new THREE.LineBasicMaterial({ color, opacity, transparent: true });
  return new THREE.LineSegments(new THREE.EdgesGeometry(geo), mat);
}

function addBox(parent, bx, by, bz, mat, px, py, pz) {
  const geo = new THREE.BoxGeometry(bx, by, bz);
  const mesh = new THREE.Mesh(geo, mat);
  mesh.position.set(px, py, pz);
  mesh.castShadow = true; mesh.receiveShadow = true;
  parent.add(mesh);
  const e = makeEdges(geo, C.edgePallet);
  e.position.set(px, py, pz);
  parent.add(e);
  return mesh;
}

function woodMat(color) {
  return new THREE.MeshStandardMaterial({ color, roughness: 0.85, metalness: 0.0 });
}

// ── Euro pallet builder (autentyczna EPAL 1200×800, wierne proporcje) ─────────
// Konstrukcja od dołu (wymiary realnej EPAL, przeskalowane przez l/w/h):
//   • 3 deski DOLNE wzdłuż długości (X): 100 / 145 / 100 mm szer. (brzegi+środek),
//   • 9 klocków (3×3): 145 mm wzdłuż X, szer. = deska pod nimi,
//   • 3 deski POPRZECZNE (wzdłuż Z, pełna szerokość) na klockach — 145 mm,
//   • 5 desek GÓRNYCH wzdłuż długości (X): 145/100/145/100/145 mm na szer. 800,
//     szczeliny ~40 mm. (EPAL: 22 mm deski + 78 mm klocki = 144 mm wysokości.)
// (Oś X = długość l, Z = szerokość w, Y w górę.)
function makeEuroPallet(l, w, h) {
  const g = new THREE.Group();

  const brdH   = Math.max(1.2, h * 0.153);    // ~2.2 cm — każda warstwa desek
  const blockH = Math.max(2, h - 3 * brdH);   // ~7.8 cm klocki (h=14.4 → 22+78+22+22)

  // Szerokości desek jako ułamki realnej EPAL (na szerokości 800 mm).
  const wWide = w * (14.5 / 80), wNarrow = w * (10.0 / 80);

  // ── Dolny pokład: 3 deski wzdłuż X (brzegi wąskie 100, środek szeroki 145) ──
  const botW  = [wNarrow, wWide, wNarrow];
  const rowZ  = [-(w / 2 - wNarrow / 2), 0, w / 2 - wNarrow / 2];
  for (let i = 0; i < 3; i++) {
    addBox(g, l, brdH, botW[i], woodMat(C.boardBot), 0, brdH / 2, rowZ[i]);
  }

  // ── 9 klocków (3×3): 145 mm wzdłuż X, szerokość = deska dolna pod klockiem ──
  const blkL = l * (14.5 / 120);
  const colX = [-(l / 2 - blkL / 2), 0, l / 2 - blkL / 2];
  for (const xp of colX) {
    for (let i = 0; i < 3; i++) {
      addBox(g, blkL, blockH, botW[i], woodMat(C.boardMid),
             xp, brdH + blockH / 2, rowZ[i]);
    }
  }

  // ── 3 deski poprzeczne (wzdłuż Z, pełna szerokość w) na klockach ──
  const yCross = brdH + blockH + brdH / 2;
  for (const xp of colX) {
    addBox(g, blkL, brdH, w, woodMat(C.boardMid), xp, yCross, 0);
  }

  // ── Górny pokład: 5 desek wzdłuż X — 145/100/145/100/145, równe szczeliny ──
  const topW  = [wWide, wNarrow, wWide, wNarrow, wWide];
  const total = topW.reduce((a, b) => a + b, 0);
  const gap   = (w - total) / (topW.length - 1);     // ~4 cm realnej szczeliny
  let z = -w / 2;
  for (const bw of topW) {
    addBox(g, l, brdH, bw, woodMat(C.boardTop), 0, h - brdH / 2, z + bw / 2);
    z += bw + gap;
  }

  return g;
}

// ── Carton materials — kraft sides + taped top, subtle per-carton hue variation ──
// (deterministic jitter by index, żeby stos nie wyglądał jak jednolity klocek)
function makeCartonMats(opacity = 1.0, seed = 0) {
  const jitter = ((seed * 9301 + 49297) % 233280) / 233280;      // 0..1, deterministic
  const tone = new THREE.Color(C.cardBase).offsetHSL(0, 0.015 * (jitter - 0.5),
                                                     0.045 * (jitter - 0.5));
  const side = new THREE.MeshStandardMaterial({
    map: kraftTexture(), color: tone, roughness: 0.9, metalness: 0.0,
  });
  const top = new THREE.MeshStandardMaterial({
    map: kraftTopTexture(), color: tone, roughness: 0.82, metalness: 0.0,
  });
  const bottom = new THREE.MeshStandardMaterial({
    map: kraftTexture(), color: tone.clone().multiplyScalar(0.92), roughness: 0.95,
  });
  const mats = [side, side, top, bottom, side, side];   // +x,-x,+y,-y,+z,-z
  if (opacity < 1) mats.forEach(m => { m.opacity = opacity; m.transparent = true; });
  return mats;
}

function makeCarton(l, h, w, label = '', opacity = 1.0, seed = 0) {
  const g = new THREE.Group();
  const geo = new THREE.BoxGeometry(l, h, w);
  const mesh = new THREE.Mesh(geo, makeCartonMats(opacity, seed));
  mesh.castShadow = true; mesh.receiveShadow = true;
  g.add(mesh);
  const e = makeEdges(geo, C.edgeCarton, 0.6);
  g.add(e);
  // Bez napisów REF na kartonach (decyzja 2026-08-07: tekst na pudłach wyglądał fatalnie).
  return g;
}

// ── Generic coloured box ──────────────────────────────────────────────────────
function makeGenericBox(l, h, w, color, opacity = 1.0) {
  const g = new THREE.Group();
  const geo = new THREE.BoxGeometry(l, h, w);
  const mat = new THREE.MeshStandardMaterial({
    color: new THREE.Color(color), roughness: 0.6, metalness: 0.05,
    opacity, transparent: opacity < 1
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true; mesh.receiveShadow = true;
  g.add(mesh);
  const e = makeEdges(geo, C.edgeGeneric, 0.7);
  g.add(e);
  return g;
}

// ── Etykieta tekstowa jako sprite (zawsze zwrócona do kamery) ──────────────────
function makeTextSprite(text) {
  const fs = 48, pad = 10;
  const meas = document.createElement('canvas').getContext('2d');
  meas.font = `bold ${fs}px sans-serif`;
  const tw = Math.ceil(meas.measureText(text).width);
  const cv = document.createElement('canvas');
  cv.width = tw + pad * 2; cv.height = fs + pad * 2;
  const x = cv.getContext('2d');
  x.font = `bold ${fs}px sans-serif`;
  x.fillStyle = 'rgba(15,23,42,0.85)';                       // ciemne tło pigułki
  x.fillRect(0, 0, cv.width, cv.height);
  x.fillStyle = '#ffffff'; x.textBaseline = 'middle';
  x.fillText(text, pad, cv.height / 2);
  const tex = new THREE.CanvasTexture(cv);
  if ('SRGBColorSpace' in THREE) tex.colorSpace = THREE.SRGBColorSpace;
  const spr = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
  spr.userData.aspect = cv.width / cv.height;
  return spr;
}

// ── Pionowa miarka wysokości z etykietą (np. „215 cm (z paletą)") ──────────────
// Rysowana z lewej strony ładunku; etykieta-sprite zawsze czytelna. floorY = podłoga,
// height = wysokość obiektu w cm, spanX = połowa największego wymiaru poziomego.
function makeHeightRuler(floorY, height, spanX, label) {
  const g = new THREE.Group();
  const xr = -spanX - spanX * 0.28;                          // linia po lewej od bryły
  const top = floorY + height;
  const mat = new THREE.LineBasicMaterial({ color: 0x0f172a });
  const line = (a, b) => g.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints([a, b]), mat));
  line(new THREE.Vector3(xr, floorY, 0), new THREE.Vector3(xr, top, 0));   // pionowa
  const tick = Math.max(height * 0.05, 2);
  line(new THREE.Vector3(xr - tick, floorY, 0), new THREE.Vector3(xr + tick, floorY, 0));
  line(new THREE.Vector3(xr - tick, top, 0), new THREE.Vector3(xr + tick, top, 0));
  const spr = makeTextSprite(label);
  const S = Math.max(height * 0.17, 6);
  spr.scale.set(S * spr.userData.aspect, S, 1);
  spr.position.set(xr - tick - S * spr.userData.aspect * 0.55, floorY + height / 2, 0);
  g.add(spr);
  return g;
}

// ── Box z realną grafiką opakowania (CartonArtwork) — tekstura per ścianka ──────
// Sloty materiału BoxGeometry: [+x,-x,+y,-y,+z,-z]. Mapowanie ścianek z edytora:
//   right=+x(0), left=-x(1), top=+y(2), bottom=-y(3), front=+z(4), back=-z(5)
const _ART_SLOT = { right: 0, left: 1, top: 2, bottom: 3, front: 4, back: 5 };

// Rozdzielczość tekstury ścianki. Sztywne 512 px dawało teksturę ~20× większą niż
// potrzeba dla kafla 120 px na skanerze PHV (6 ścianek × 512² RGBA ≈ 6 MB VRAM na bryłę),
// więc bazę liczymy z realnego rozmiaru canvasu; clamp trzyma jakość na desktopie.
const _ART_TEX_MIN = 96, _ART_TEX_MAX = 512;

function _texBase(canvas) {
  // Bez mnożnika „na zapas": px to już piksele urządzenia, a bryła zajmuje ułamek
  // kadru i jest oglądana pod kątem. Mnożnik 1.5 windował kafel PHV (120 px × dpr 2)
  // do 360 i przez to `_artSrc` nigdy nie sięgał po miniaturę — czyli po wariant
  // zrobiony dokładnie pod ten ekran.
  const px = (canvas?.clientWidth || canvas?.width || 0) *
             Math.min(window.devicePixelRatio || 1, 2);
  if (!px) return _ART_TEX_MAX;
  return Math.max(_ART_TEX_MIN, Math.min(_ART_TEX_MAX, Math.round(px)));
}

function _faceCanvasSize(face, l, h, w, base) {
  // Proporcje ścianki → rozmiar canvas (dłuższy bok = base), by tekstura nie była rozciągnięta.
  let fw, fh;
  if (face === 'front' || face === 'back')      { fw = l; fh = h; }
  else if (face === 'left' || face === 'right') { fw = w; fh = h; }
  else                                          { fw = l; fh = w; }   // top/bottom
  const k = (base || _ART_TEX_MAX) / Math.max(fw, fh, 1);
  return { cw: Math.max(8, Math.round(fw * k)), ch: Math.max(8, Math.round(fh * k)) };
}

// Wariant pliku pod docelową teksturę: `thumb` (256 px) wystarcza małym canvasom,
// większe biorą `url` (display ≤1024 px). `full` (oryginał) nie jest tu używany nigdy.
function _artSrc(a, base) {
  return ((base <= 256 ? a.thumb : a.url) || a.url || a.thumb || '');
}

// Cache obrazów wspólny dla WSZYSTKICH viewerów na stronie: ta sama etykieta na kilku
// ściankach albo na kilku kartach hierarchii = jedno pobranie i jedno dekodowanie.
const _artImgCache = new Map();     // url → Promise<ImageBitmap|HTMLImageElement|null>

function _loadArtImage(url) {
  if (!url) return Promise.resolve(null);
  let p = _artImgCache.get(url);
  if (p) return p;
  p = (typeof createImageBitmap === 'function' && typeof fetch === 'function')
    ? fetch(url, { credentials: 'same-origin' })
        .then(r => (r.ok ? r.blob() : Promise.reject(new Error(r.status))))
        .then(b => createImageBitmap(b))
        .catch(() => _loadArtImageEl(url))     // np. starsze Chrome na terminalach
    : _loadArtImageEl(url);
  _artImgCache.set(url, p);
  return p;
}

function _loadArtImageEl(url) {
  return new Promise(res => {
    const img = new Image();
    img.onload = () => res(img);
    img.onerror = () => res(null);             // brak pliku → zostaje sam kolor bazowy
    img.src = url;
  });
}

function _drawArtwork(ctx, cw, ch, img, a) {
  if (a.kind === 'print') {                         // nadruk „cover" na całą ściankę
    const s = Math.max(cw / img.width, ch / img.height);
    const dw = img.width * s, dh = img.height * s;
    ctx.drawImage(img, (cw - dw) / 2, (ch - dh) / 2, dw, dh);
    return;
  }
  // etykieta: prostokąt x%,y%,w%,h% z obrotem wokół środka (wiernie wg edytora)
  const rw = (a.w / 100) * cw, rh = (a.h / 100) * ch;
  ctx.save();
  ctx.translate((a.x / 100) * cw + rw / 2, (a.y / 100) * ch + rh / 2);
  ctx.rotate((a.rot || 0) * Math.PI / 180);
  ctx.drawImage(img, -rw / 2, -rh / 2, rw, rh);
  ctx.restore();
}

// ponytail: bez korekty lustrzanego UV per ścianka — dla miniatury 3D nieistotne;
// gdyby przeszkadzało, dodać flip X na back/left/bottom.
function makeArtworkBox(l, h, w, baseColor, artwork, onReady, texBase) {
  const g = new THREE.Group();
  const geo = new THREE.BoxGeometry(l, h, w);
  const base = () => new THREE.MeshStandardMaterial({
    color: new THREE.Color(baseColor), roughness: 0.6, metalness: 0.05 });
  const mats = [base(), base(), base(), base(), base(), base()];
  const mesh = new THREE.Mesh(geo, mats);
  mesh.castShadow = true; mesh.receiveShadow = true;
  g.add(mesh);
  g.add(makeEdges(geo, C.edgeGeneric, 0.7));

  const tb = texBase || _ART_TEX_MAX;
  const byFace = {};
  for (const a of artwork) {
    if (a && _artSrc(a, tb) && (a.face in _ART_SLOT)) (byFace[a.face] = byFace[a.face] || []).push(a);
  }
  for (const face in byFace) {
    const arts = byFace[face].slice().sort((p, q) => (p.z || 0) - (q.z || 0));
    const { cw, ch } = _faceCanvasSize(face, l, h, w, tb);
    const cv = document.createElement('canvas'); cv.width = cw; cv.height = ch;
    const ctx = cv.getContext('2d');
    const imgs = new Array(arts.length);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace; tex.anisotropy = 4;
    const m = mats[_ART_SLOT[face]];
    m.map = tex; m.color.set('#ffffff'); m.needsUpdate = true;     // biały tint = wierne kolory

    // Kompozycja progresywna: rysujemy to, co już przyszło, zamiast czekać na komplet
    // (jeden wolny plik blokował całą ściankę). Sklejanie w rAF, więc N doładowań
    // z rzędu to jedno przemalowanie canvasu.
    let queued = false;
    const compose = () => {
      queued = false;
      ctx.clearRect(0, 0, cw, ch);
      ctx.fillStyle = baseColor; ctx.fillRect(0, 0, cw, ch);
      for (let i = 0; i < arts.length; i++) if (imgs[i]) _drawArtwork(ctx, cw, ch, imgs[i], arts[i]);
      tex.needsUpdate = true;
      if (onReady) onReady();
    };
    const scheduleCompose = () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(compose);
    };
    compose();                                                     // najpierw sam kolor bazowy
    arts.forEach((a, i) => {
      _loadArtImage(_artSrc(a, tb)).then(img => {
        if (!img) return;                                          // brak pliku → zostaje kolor
        imgs[i] = img;
        scheduleCompose();
      });
    });
  }
  return g;
}

// ── Main viewer class ─────────────────────────────────────────────────────────
class PalVizViewer {
  constructor(canvas, data) {
    this.canvas    = canvas;
    this.data      = data;
    this.azimuth   = Math.PI / 4;
    this.elevation = (35 * Math.PI) / 180;
    this.radius    = 200;
    this.lookAtY   = 0;
    this.autoRotate = true;
    this.animFrameId = null;
    this.destroyed   = false;

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    if ('outputColorSpace' in this.renderer && THREE.SRGBColorSpace) this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    if ('ACESFilmicToneMapping' in THREE) { this.renderer.toneMapping = THREE.ACESFilmicToneMapping; this.renderer.toneMappingExposure = 1.05; }

    this.scene  = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 10000);

    this._setupLights();
    this._addGround();
    this.buildScene();
    this.setupMouseDrag();
    this.setupResizeObserver();
    this.resize();
    this.startLoop();
  }

  _setupLights() {
    // Warm key light from upper-front-right — casts soft shadows
    const key = new THREE.DirectionalLight(0xfff2e0, 1.15);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.bias = -0.0006;
    this.scene.add(key);
    this.scene.add(key.target);
    this.key = key;
    // Cool fill from upper-back-left
    const fill = new THREE.DirectionalLight(0xcad8ff, 0.3);
    fill.position.set(-1.5, 1.5, -2);
    this.scene.add(fill);
    // Rim light od tyłu — jasna krawędź oddziela ładunek od tła (efekt „studia").
    const rim = new THREE.DirectionalLight(0xffffff, 0.35);
    rim.position.set(0.4, 2.2, -2.6);
    this.scene.add(rim);
    // Sky/ground ambient
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x8c7e6a, 0.5));
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.18));
  }

  _addGround() {
    const mat = THREE.ShadowMaterial ? new THREE.ShadowMaterial({ opacity: 0.26 })
                                     : new THREE.MeshBasicMaterial({ color: 0xeeeeee, transparent: true, opacity: 0.001 });
    const ground = new THREE.Mesh(new THREE.PlaneGeometry(8000, 8000), mat);
    ground.rotation.x = -Math.PI / 2;     // horizontal plane (Y-up scene)
    ground.receiveShadow = true;
    // Cień (przezroczysty) NIE zapisuje głębi + stała kolejność — inaczej dwie
    // przezroczyste płaszczyzny (cień + halo) przełączają kolejność przy obrocie
    // kamery i tło migocze. renderOrder wymusza deterministyczne rysowanie.
    if (THREE.ShadowMaterial) mat.depthWrite = false;
    ground.renderOrder = -2;
    this.ground = ground;
    this.scene.add(ground);
    // Miękka poświata pod ładunkiem — radialny dysk „studia", osadza scenę na tle.
    const gc = document.createElement('canvas'); gc.width = gc.height = 256;
    const gx = gc.getContext('2d');
    const grad = gx.createRadialGradient(128, 128, 10, 128, 128, 128);
    grad.addColorStop(0, 'rgba(148,163,184,0.30)');
    grad.addColorStop(0.55, 'rgba(148,163,184,0.12)');
    grad.addColorStop(1, 'rgba(148,163,184,0)');
    gx.fillStyle = grad; gx.fillRect(0, 0, 256, 256);
    const gtex = new THREE.CanvasTexture(gc);
    const halo = new THREE.Mesh(new THREE.PlaneGeometry(1, 1),
      new THREE.MeshBasicMaterial({ map: gtex, transparent: true, depthWrite: false }));
    halo.rotation.x = -Math.PI / 2;
    halo.renderOrder = -1;                 // zawsze po podłodze (patrz wyżej)
    this.halo = halo;
    this.scene.add(halo);
  }

  // Place the contact-shadow plane at floor level and size the shadow frustum to fit.
  _configShadowAndGround(floorY, span) {
    if (this.ground) this.ground.position.y = floorY;
    if (this.halo) {
      this.halo.position.y = floorY + 0.15;
      this.halo.scale.set(span * 2.6, span * 2.6, 1);
    }
    const d = Math.max(span, 1);
    // key light high to the front-right, aimed at the load centre
    this.key.position.set(d * 0.9, floorY + d * 1.7, d * 1.1);
    this.key.target.position.set(0, floorY + d * 0.3, 0);
    const sc = this.key.shadow.camera, r = d * 1.4;
    sc.left = -r; sc.right = r; sc.top = r; sc.bottom = -r;
    sc.near = 1; sc.far = d * 8; sc.updateProjectionMatrix();
  }

  buildScene() {
    const { data } = this;
    // Zrzut dzieci sceny SPRZED bryły (ground/halo z _addGround) — do bezpiecznego
    // podmienienia TYLKO wygenerowanej bryły po wczytaniu glTF, patrz _loadGlbReplace.
    const preExisting = new Set(this.scene.children);
    switch (data.type) {
      case 'pallet':         this._buildPallet(data);       break;
      case 'layer':          this._buildLayer(data);        break;
      case 'box':            this._buildBox(data);          break;
      case 'box_with_units': this._buildBoxWithUnits(data); break;
    }
    // Realny model 3D (glTF) wgrany na kartonie zastępuje wygenerowaną bryłę wyżej —
    // bryła zostaje na ekranie podczas ładowania, a po sukcesie jest podmieniana (fallback
    // przy błędzie sieci/pliku: generowana bryła zostaje, bez pustego canvasu).
    if (data.glb_url) this._loadGlbReplace(data, preExisting);
  }

  _loadGlbReplace(data, preExisting) {
    const toRemove = this.scene.children.filter(o => !preExisting.has(o));
    new GLTFLoader().load(data.glb_url, gltf => {
      if (this.destroyed) return;
      const model = gltf.scene;
      const box = new THREE.Box3().setFromObject(model);
      const size = new THREE.Vector3(); box.getSize(size);
      const center = new THREE.Vector3(); box.getCenter(center);
      // Skaluj jednolicie tak, żeby model zmieścił się w bryle kartonu (l×h×w cm) —
      // zachowuje proporcje realnego skanu zamiast rozciągać go do skrzynki.
      const scale = Math.min(data.l / (size.x || 1), data.h / (size.y || 1), data.w / (size.z || 1));
      model.scale.setScalar(scale);
      model.position.sub(center.multiplyScalar(scale));
      model.position.y += data.h / 2;   // podłoga = spód bryły, jak w _buildBox
      model.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
      toRemove.forEach(o => this.scene.remove(o));
      this.scene.add(model);
    }, undefined, err => {
      console.warn('GLB load failed, keeping generated box:', err);
    });
  }

  // Realny model 3D kartonu na palecie: wczytaj glTF RAZ, wyskaluj do bryły kartonu,
  // wyśrodkuj, potem sklonuj na każdą pozycję (klon glTF współdzieli geometrię/materiały).
  // Placeholdery (generyczne kartony) znikają po podmianie; błąd sieci → zostają.
  _loadGlbClones(url, carton, nodes) {
    new GLTFLoader().load(url, gltf => {
      if (this.destroyed) return;
      const proto = gltf.scene;
      const box = new THREE.Box3().setFromObject(proto);
      const size = new THREE.Vector3(); box.getSize(size);
      const center = new THREE.Vector3(); box.getCenter(center);
      const scale = Math.min(carton.l / (size.x || 1), carton.h / (size.y || 1),
                             carton.w / (size.z || 1));
      proto.scale.setScalar(scale);
      proto.position.sub(center.multiplyScalar(scale));   // geometryczny środek → (0,0,0)
      for (const n of nodes) {
        const m = proto.clone(true);
        if (n.rotated) m.rotation.y = Math.PI / 2;
        m.position.copy(n.box.position);                  // środek klona = środek placeholdera
        m.traverse(o => { if (o.isMesh) { o.castShadow = true; o.receiveShadow = true; } });
        this.scene.add(m);
        this.scene.remove(n.box);
      }
    }, undefined, err => {
      console.warn('GLB pallet load failed, keeping generated cartons:', err);
    });
  }

  _buildPallet(data) {
    const { pallet, carton, placements, layers, label = '' } = data;
    const { l, w, base_h } = pallet;

    // Realistic Euro pallet
    const palGrp = makeEuroPallet(l, w, base_h);
    this.scene.add(palGrp);

    const maxL = Math.min(layers || 3, 20);   // show real stack height (np. 9 warstw)
    const lbl  = String(label ?? '').substring(0, 14);

    // Priorytet renderu kartonu na palecie: realny model 3D (glb) → grafika na ściankach
    // → generyczny karton. glb i grafika budowane RAZ i klonowane na każdą pozycję
    // (współdzielona geometria/materiały → tanio). Obrót 90° dla obróconych pozycji
    // (paletyzacja używa tylko 0/90°; wykrycie: dx bliżej szerokości niż długości).
    const useGlb = !!data.carton_glb_url;
    const art = data.carton_artwork;
    const artTemplate = (!useGlb && art && art.length)
      ? makeArtworkBox(carton.l, carton.h, carton.w, data.color || C.boardTop, art)
      : null;
    const glbNodes = [];   // placeholdery do podmiany po wczytaniu glb

    for (let li = 0; li < maxL; li++) {
      const yBase = base_h + li * carton.h;
      let ci = 0;
      for (const p of placements) {
        const bL = p.dx, bW = p.dy;
        const rotated = Math.abs(bL - carton.w) < Math.abs(bL - carton.l);
        let cg;
        if (artTemplate) {
          cg = artTemplate.clone();
          if (rotated) cg.rotation.y = Math.PI / 2;
        } else {
          cg = makeCarton(bL, carton.h, bW, '', 1.0, li * 37 + (ci++));
        }
        cg.position.set(p.x + bL / 2 - l / 2, yBase + carton.h / 2, p.y + bW / 2 - w / 2);
        this.scene.add(cg);
        if (useGlb) glbNodes.push({ box: cg, rotated });
      }
    }
    if (useGlb) this._loadGlbClones(data.carton_glb_url, carton, glbNodes);

    const totalH  = base_h + maxL * carton.h;
    this.lookAtY  = totalH / 2;
    this.radius   = this._frameRadius(Math.max(l, w, totalH), 1.55);
    this.elevation = (38 * Math.PI) / 180;
    this._configShadowAndGround(0, Math.max(l, w, totalH));
    this.updateCamera();
  }

  _buildLayer(data) {
    const { pallet, placements, label = '' } = data;
    const { l, w } = pallet;

    // Thin pallet-coloured floor
    const floorGeo = new THREE.BoxGeometry(l, 2, w);
    const floorMat = new THREE.MeshPhongMaterial({ color: C.boardTop, shininess: 3 });
    const floor    = new THREE.Mesh(floorGeo, floorMat);
    floor.position.set(0, -1, 0);
    this.scene.add(floor);

    const lbl = String(label ?? '').substring(0, 14);
    let ci = 0;
    for (const p of placements) {
      const bL = p.dx, bW = p.dy;   // dx/dy already encode rotation — use directly
      const cg = makeCarton(bL, 8, bW, '', 1.0, ci++);
      cg.position.set(p.x + bL / 2 - l / 2, 4, p.y + bW / 2 - w / 2);
      this.scene.add(cg);
    }

    this.lookAtY   = 4;
    this.radius    = 1.5 * Math.max(l, w, 20);
    this.elevation = Math.PI / 2 - 0.08;
    this.azimuth   = 0;
    this.autoRotate = false;
    this._configShadowAndGround(-1, Math.max(l, w, 20));
    this.updateCamera();
  }

  _buildBox(data) {
    const { l, w, h, color, artwork } = data;
    const g = (artwork && artwork.length)
      ? makeArtworkBox(l, h, w, color, artwork, null, _texBase(this.canvas))   // realna grafika
      : makeGenericBox(l, h, w, color);
    this.scene.add(g);

    if (!data.noRuler) this.scene.add(makeHeightRuler(-h / 2, h, Math.max(l, w) / 2, Math.round(h) + ' cm'));
    this.lookAtY  = 0;
    this.radius   = this._frameRadius(Math.max(l, w, h), 1.6);
    this._configShadowAndGround(-h / 2, Math.max(l, w, h));
    this.updateCamera();
  }

  _buildBoxWithUnits(data) {
    const { l, w, h, unit_l, unit_w, unit_h, units, color, artwork } = data;

    // Karton z realną grafiką → pokazujemy SOLIDNĄ bryłę z grafiką (zamiast klatki
    // z jednostkami w środku), bo grafika reprezentuje rzeczywisty wygląd opakowania.
    if (artwork && artwork.length) {
      this.scene.add(makeArtworkBox(l, h, w, color, artwork, null, _texBase(this.canvas)));
      if (!data.noRuler) this.scene.add(makeHeightRuler(-h / 2, h, Math.max(l, w) / 2, Math.round(h) + ' cm'));
      this.lookAtY = 0;
      // Ta sama skala co poziomy bez grafiki (_frameRadius respektuje refSize) — inaczej
      // karton z grafiką jest przyklejony do kamery i wychodzi poza kadr (za duży zoom).
      this.radius  = this._frameRadius(Math.max(l, w, h), 1.6);
      this._configShadowAndGround(-h / 2, Math.max(l, w, h));
      this.updateCamera();
      return;
    }

    // Obrys zewnętrzny TYLKO krawędziami — bez mlecznej przezroczystej bryły.
    const outerGeo = new THREE.BoxGeometry(l, h, w);
    this.scene.add(makeEdges(outerGeo, C.edgeGeneric, 0.9));

    // Guard against missing/zero unit dims (l/0 → Infinity → degenerate grid).
    const cols   = unit_l > 0 ? Math.max(1, Math.floor(l / unit_l)) : 1;
    const rows   = unit_w > 0 ? Math.max(1, Math.floor(w / unit_w)) : 1;
    const stacks = unit_h > 0 ? Math.max(1, Math.floor(h / unit_h)) : 1;

    let placed = 0;
    outer2: for (let s = 0; s < stacks; s++) {
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          if (placed >= units) break outer2;
          const ug = makeGenericBox(unit_l, unit_h, unit_w, color, 1.0);
          ug.position.set(
            c * unit_l + unit_l / 2 - l / 2,
            s * unit_h + unit_h / 2 - h / 2,
            r * unit_w + unit_w / 2 - w / 2
          );
          this.scene.add(ug);
          placed++;
        }
      }
    }

    if (!data.noRuler) this.scene.add(makeHeightRuler(-h / 2, h, Math.max(l, w) / 2, Math.round(h) + ' cm'));
    this.lookAtY = 0;
    this.radius  = this._frameRadius(Math.max(l, w, h), 1.6);
    this._configShadowAndGround(-h / 2, Math.max(l, w, h));
    this.updateCamera();
  }

  // Wspólna skala między kartami hierarchii. Gdy data.refSize jest podane (największy
  // wymiar w całym zestawie kart, np. wysokość palety), kadruj wg niego → mniejsze
  // poziomy są widocznie mniejsze i we właściwej kolejności. Bez refSize: zachowanie
  // jak dawniej (fit-to-container), więc calc/inne strony bez zmian.
  // ponytail: kompresja pierwiastkowa (ALPHA=0.5) zamiast skali 1:1 — pełna prawda
  // (ALPHA→0) robi ze „sztuki" niewidoczny punkt obok palety; sqrt zachowuje kolejność
  // (paleta > karton > OPZ > sztuka) i czytelność. Podnieś ALPHA→1 dla wierniejszej skali.
  _frameRadius(objMax, kLegacy) {
    const ref = this.data && this.data.refSize;
    if (ref && ref > 0 && objMax > 0) {
      const ALPHA = 0.5, K = 1.55;
      return K * ref * Math.pow(objMax / ref, ALPHA);
    }
    // frameK: nadpisanie mnożnika kadru (tryb zoom po dwukliku podaje większy → karton
    // mieści się w całości z marginesem, zamiast wychodzić poza kadr przy fov 42°).
    return (this.data && this.data.frameK ? this.data.frameK : kLegacy) * objMax;
  }

  updateCamera() {
    if (this._minR === undefined) {        // derive zoom bounds from the initial framing
      this._minR = this.radius * 0.25;
      this._maxR = this.radius * 4;
    }
    const x = this.radius * Math.cos(this.elevation) * Math.sin(this.azimuth);
    const y = this.radius * Math.sin(this.elevation);
    const z = this.radius * Math.cos(this.elevation) * Math.cos(this.azimuth);
    this.camera.position.set(x, (this.lookAtY || 0) + y, z);
    this.camera.lookAt(0, this.lookAtY || 0, 0);
  }

  startLoop() {
    // Karta poza ekranem (długa hierarchia) albo zakładka w tle nie musi nic rysować —
    // bez tego każdy viewer kręcił własną pętlę z shadow mapą 2048² przez cały czas.
    this._visible = true;
    if ('IntersectionObserver' in window) {         // stare terminale: rysuj jak dotąd
      this._io = new IntersectionObserver(([e]) => { this._visible = e.isIntersecting; },
                                          { rootMargin: '100px' });
      this._io.observe(this.canvas);
    }

    const loop = () => {
      if (this.destroyed) return;
      this.animFrameId = requestAnimationFrame(loop);
      if (!this._visible || document.hidden) return;
      if (this.autoRotate) { this.azimuth += 0.004; this.updateCamera(); }
      this.renderer.render(this.scene, this.camera);
    };
    loop();
  }

  setupMouseDrag() {
    const canvas = this.canvas;
    let dragging = false, lastX = 0, lastY = 0;

    const onDown = (e) => {
      dragging = true; lastX = e.clientX; lastY = e.clientY; this.autoRotate = false;
    };
    const onMove = (e) => {
      if (!dragging) return;
      this.azimuth   -= (e.clientX - lastX) * 0.005;
      this.elevation  = Math.max(0.05, Math.min(Math.PI / 2 - 0.05,
                          this.elevation + (e.clientY - lastY) * 0.005));
      lastX = e.clientX; lastY = e.clientY;
      this.updateCamera();
    };
    const onUp = () => { dragging = false; };

    // Pinch-to-zoom: bez tego na dotyku (telefon/skaner) nie dało się przybliżać —
    // zoom był tylko kółkiem myszy. 2 palce sterują promieniem orbity (clamp jak wheel).
    let pinchDist = 0;
    const _dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

    const onTStart = (e) => {
      if (e.touches.length === 2) {
        dragging = false; pinchDist = _dist(e.touches); this.autoRotate = false;
      } else if (e.touches.length === 1) {
        dragging = true; lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
        this.autoRotate = false;
      }
    };
    const onTMove = (e) => {
      if (e.touches.length === 2 && pinchDist > 0) {
        const d = _dist(e.touches);
        if (d > 0) {
          const minR = (this._minR || 10), maxR = (this._maxR || 6000);
          this.radius = Math.max(minR, Math.min(maxR, this.radius * (pinchDist / d)));
          pinchDist = d;
          this.updateCamera();
        }
        e.preventDefault();
        return;
      }
      if (!dragging || e.touches.length !== 1) return;
      this.azimuth  -= (e.touches[0].clientX - lastX) * 0.005;
      this.elevation = Math.max(0.05, Math.min(Math.PI / 2 - 0.05,
                         this.elevation + (e.touches[0].clientY - lastY) * 0.005));
      lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
      this.updateCamera();
      e.preventDefault();
    };
    const onTEnd = () => { dragging = false; pinchDist = 0; };

    // Mouse-wheel zoom: scale the orbit radius (clamped) and re-aim the camera.
    const onWheel = (e) => {
      e.preventDefault();
      this.autoRotate = false;
      const factor = Math.exp((e.deltaY || 0) * 0.0012);   // smooth, direction-correct
      const minR = (this._minR || 10), maxR = (this._maxR || 6000);
      this.radius = Math.max(minR, Math.min(maxR, this.radius * factor));
      this.updateCamera();
    };

    canvas.addEventListener('mousedown', onDown);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    canvas.addEventListener('touchstart', onTStart, { passive: false });
    canvas.addEventListener('touchmove',  onTMove,  { passive: false });
    canvas.addEventListener('touchend',   onTEnd);

    this._cleanup = () => {
      canvas.removeEventListener('mousedown', onDown);
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      canvas.removeEventListener('wheel', onWheel);
      canvas.removeEventListener('touchstart', onTStart);
      canvas.removeEventListener('touchmove',  onTMove);
      canvas.removeEventListener('touchend',   onTEnd);
    };
  }

  setupResizeObserver() {
    this._ro = new ResizeObserver(() => this.resize());
    this._ro.observe(this.canvas.parentElement || this.canvas);
  }

  resize() {
    const canvas = this.canvas;
    const w = canvas.clientWidth  || canvas.parentElement?.clientWidth  || 300;
    const h = canvas.clientHeight || canvas.parentElement?.clientHeight || 220;
    if (this.renderer.domElement.width !== w || this.renderer.domElement.height !== h) {
      this.renderer.setSize(w, h, false);
      this.camera.aspect = w / h;
      this.camera.updateProjectionMatrix();
    }
  }

  destroy() {
    this.destroyed = true;
    if (this.animFrameId) cancelAnimationFrame(this.animFrameId);
    if (this._cleanup) this._cleanup();
    if (this._ro) this._ro.disconnect();
    if (this._io) this._io.disconnect();
    this.scene.traverse(obj => {
      if (obj.geometry) obj.geometry.dispose();
      if (obj.material) {
        (Array.isArray(obj.material) ? obj.material : [obj.material]).forEach(m => {
          // Each carton label is a unique CanvasTexture; dispose it too (the shared
          // cached kraft texture is left alone) — otherwise GPU textures leak on every
          // HTMX recalc rebuild.
          if (m.map && m.map !== _kraftTex && m.map !== _kraftTopTex) m.map.dispose();
          m.dispose();
        });
      }
    });
    this.renderer.dispose();
  }
}

export function renderPalVizLevel(canvas, data) {
  return new PalVizViewer(canvas, data);
}
