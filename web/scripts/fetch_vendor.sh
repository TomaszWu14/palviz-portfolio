#!/bin/sh
# Vendor the front-end 3D libraries locally so the app never depends on third-party
# CDNs at runtime (works behind strict CSP, survives CDN outages, GDPR-friendly).
#
# Run automatically in the Docker build (before collectstatic); also run it once by
# hand for local development:  sh web/scripts/fetch_vendor.sh
#
# Pinned versions match what the templates expect:
#   three 0.163.0 (ESM, importmap)  — hierarchy / calc / saved pallet (palviz-three.js)
#   three 0.128.0 (r128 UMD global) + OrbitControls — carton designer & artwork box
set -eu

DIR="$(CDPATH= cd "$(dirname "$0")/../ui/static/ui/vendor" 2>/dev/null && pwd || true)"
if [ -z "${DIR}" ]; then
  DIR="$(dirname "$0")/../ui/static/ui/vendor"
  mkdir -p "$DIR"
fi

# SHA-256 pobieranych plików (DEP-002) — liczone z ORYGINAŁU z CDN, przed podmianami sed
# niżej. Niezgodność (podmieniony CDN/pakiet) przerywa build. Zmiana wersji = nowa suma:
#   curl -fsSL <url> | sha256sum
SUMS='
ca330dc6b8e6a0a88a0b707e40a5191110c3e86842c7c6431a7a7a2e972f08f5  three.module.js
f260591ef315aa04888152e7f121865214e33fb54727145cf4e4445058db1297  addons/controls/OrbitControls.js
cd65af67fff656b8ef464e125931ba1094f1acf3b8fd5db2face914a8f5b7599  addons/controls/TransformControls.js
e21f41b7ef2016f2984a5d27850b42dffa797a6a194a138e288b06576d765a7b  addons/environments/RoomEnvironment.js
e85a5018a689867ae6ee60211956fa59ec5260d70d2cb58316f1ddc4afae637c  addons/loaders/GLTFLoader.js
b0c64fe6f3b9907262921b73fafc4ade874c07ba6b4876e164c87a830c2c2113  addons/utils/BufferGeometryUtils.js
9274bbcec8d96168626c732b5d31c775aa8cfb7eaa0599bec0c175908a2c1ce2  three.r128.min.js
02bb4ade710f3e607329e37a21f098bc3ac70eb6e33daf8a65e79f4db785e7b2  OrbitControls.r128.js
9014598e9a20a1657473b6c74afae6ea8f9bbe7ce20b661647d8e07f076e5965  three-mesh-bvh.module.js
17a2a272ed499d7879f84015702e26257746c29635ec5127886bc8d814d881f8  camera-controls.module.js
ff934a599715bfb72534dda8aea206bf23630b56a49e1aa0d9ad0ab5dfeab985  lil-gui.module.js
449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452  htmx.min.js
57b37d7cae9a27d965fdae4adcc844245dfdc407e655aee85dcfff3a08036a3f  alpine.min.js
'

fetch() {  # url  filename(may include subdirs)
  mkdir -p "$(dirname "$DIR/$2")"
  if [ -f "$DIR/$2" ]; then
    echo "  · $2 (already present)"
  else
    echo "  ↓ $2"
    sum="$(printf '%s\n' "$SUMS" | awk -v f="$2" '$2 == f { print $1 }')"
    if [ -z "$sum" ]; then
      echo "BRAK sumy SHA-256 dla $2 w SUMS — dopisz ją." >&2
      exit 1
    fi
    # Retry z backoffem: pojedynczy TLS-hiccup do CDN wywalał CAŁY build obrazu
    # w Coolify (realny incydent 2026-09-05: "TLS connect error: unexpected eof").
    curl -fsSL --retry 5 --retry-delay 2 --retry-all-errors "$1" -o "$DIR/$2.part"
    if ! echo "$sum  $DIR/$2.part" | sha256sum -c - >/dev/null; then
      rm -f "$DIR/$2.part"
      echo "NIEZGODNA suma SHA-256 dla $2 ($1) — przerywam." >&2
      exit 1
    fi
    mv "$DIR/$2.part" "$DIR/$2"
  fi
}

TJS=0.163.0   # ESM three for importmap pages (unified version)
echo "Vendoring front-end libraries into $DIR"
# three.js — ESM (importmap) + its addons used across the 3D pages
fetch "https://cdn.jsdelivr.net/npm/three@${TJS}/build/three.module.js"                          "three.module.js"
fetch "https://cdn.jsdelivr.net/npm/three@${TJS}/examples/jsm/controls/OrbitControls.js"         "addons/controls/OrbitControls.js"
fetch "https://cdn.jsdelivr.net/npm/three@${TJS}/examples/jsm/controls/TransformControls.js"     "addons/controls/TransformControls.js"
# RoomEnvironment — studyjny IBL (PMREM) dla PBR renderu regałów (warehouse_model/view.html)
fetch "https://cdn.jsdelivr.net/npm/three@${TJS}/examples/jsm/environments/RoomEnvironment.js"   "addons/environments/RoomEnvironment.js"
sed -i.bak "s#} from 'three';#} from '../../three.module.js';#" "$DIR/addons/environments/RoomEnvironment.js" && rm -f "$DIR/addons/environments/RoomEnvironment.js.bak"
fetch "https://cdn.jsdelivr.net/npm/three@${TJS}/examples/jsm/loaders/GLTFLoader.js"           "addons/loaders/GLTFLoader.js"
fetch "https://cdn.jsdelivr.net/npm/three@${TJS}/examples/jsm/utils/BufferGeometryUtils.js"     "addons/utils/BufferGeometryUtils.js"
# Stare przegladarki na skanerach (MC330L, Android 8.1) nie wspieraja <script type=importmap>
# (patrz komentarz w palviz-three.js) — przepisz bare specifier 'three' na sciezke wzgledna,
# tak samo jak reszta pliku importuje three.module.js. Idempotentne (sed no-op gdy juz podmienione).
sed -i.bak "s#} from 'three';#} from '../../three.module.js';#" "$DIR/addons/loaders/GLTFLoader.js" && rm -f "$DIR/addons/loaders/GLTFLoader.js.bak"
sed -i.bak "s#} from 'three';#} from '../../three.module.js';#" "$DIR/addons/utils/BufferGeometryUtils.js" && rm -f "$DIR/addons/utils/BufferGeometryUtils.js.bak"
# three.js r128 — UMD globals (carton designer & artwork box)
fetch "https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js"                            "three.r128.min.js"
fetch "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"         "OrbitControls.r128.js"
# three.js add-on libraries (ESM, resolved via the importmap)
#   three-mesh-bvh  — accelerated raycasting for the big InstancedMesh warehouse viewer
#   camera-controls — smooth dolly-to-cursor orbit + animated camera presets (3D editor)
#   lil-gui         — live calibration panel (aisle pitch, label height, split gap)
fetch "https://cdn.jsdelivr.net/npm/three-mesh-bvh@0.7.6/build/index.module.js"                  "three-mesh-bvh.module.js"
fetch "https://cdn.jsdelivr.net/npm/camera-controls@2.9.0/dist/camera-controls.module.js"        "camera-controls.module.js"
fetch "https://cdn.jsdelivr.net/npm/lil-gui@0.19.2/dist/lil-gui.esm.js"                           "lil-gui.module.js"
# htmx
fetch "https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js"                                       "htmx.min.js"
# Alpine.js — lightweight reactivity ("sprinkles") that pairs with htmx (UMD global)
fetch "https://cdn.jsdelivr.net/npm/alpinejs@3.15.12/dist/cdn.min.js"                            "alpine.min.js"
echo "Done."
