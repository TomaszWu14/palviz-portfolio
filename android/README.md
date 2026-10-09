# GROOVE — APK na Androida (TWA)

Aplikacja na Androida = **TWA (Trusted Web Activity)** opakowujące tę samą web‑apkę
(PWA). Brak osobnego kodu — aktualizacje idą z serwera. Uruchamia się bez paska URL
(po weryfikacji Digital Asset Links), w trybie `standalone` — **dolny pasek nawigacji
systemu (wstecz / ekran główny / ostatnie) pozostaje widoczny** (nie immersive/fullscreen).

> Uwaga: tryb wyświetlania jest „wypalony” w zainstalowanej paczce APK. Aby zmiana
> `fullscreen → standalone` zadziałała, trzeba **przebudować i ponownie zainstalować APK**.

Wymagania spełnione po stronie serwera:
- manifest: `https://groove.example.com/manifest.webmanifest` (`display: standalone`, `start_url: /control/?app=1`)
- service worker: `https://groove.example.com/sw.js`
- Digital Asset Links: `https://groove.example.com/.well-known/assetlinks.json`
  (zwraca poprawny wpis po ustawieniu zmiennych `TWA_PACKAGE_NAME` i `TWA_SHA256_FINGERPRINT`)

## Opcja A — PWABuilder (najprościej, przez przeglądarkę)
1. Wejdź na https://www.pwabuilder.com i wklej `https://groove.example.com`.
2. „Package For Stores" → **Android** → pobierz paczkę (zawiera `app-release-signed.apk`,
   `app-release.aab` oraz `assetlinks.json` z **odciskiem SHA‑256**).
3. Skopiuj `sha256_cert_fingerprints` z dołączonego `assetlinks.json`.

## Opcja B — Bubblewrap (CLI; potrzebny JDK 17 + Android SDK)
```bash
npm i -g @bubblewrap/cli
bubblewrap init --manifest https://groove.example.com/manifest.webmanifest   # lub użyj android/twa-manifest.json
bubblewrap build           # tworzy app-release-signed.apk + zapisuje keystore
bubblewrap fingerprint     # pokaże odcisk SHA-256 klucza podpisu
```

## Po zbudowaniu — włącz weryfikację (żeby APK chodził bez paska URL)
Ustaw na produkcji zmienne środowiskowe i zrestartuj:
```
TWA_PACKAGE_NAME=com.example.groove
TWA_SHA256_FINGERPRINT=AB:CD:...:EF        # (można podać kilka po przecinku)
```
Sprawdź: `https://groove.example.com/.well-known/assetlinks.json` zwraca Twój wpis.

## Dystrybucja
- **Play Store**: wgraj `app-release.aab`.
- **MDM / ręcznie**: zainstaluj `app-release-signed.apk` na skanerach.

Kamera (skan QR) i skaner sprzętowy działają w TWA jak w przeglądarce.
