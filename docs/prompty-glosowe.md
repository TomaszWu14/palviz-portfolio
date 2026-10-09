# Prompty głosowe do Claude Code (Wispr Flow) — ściąga

Cel: dyktować z telefonu dobre, oszczędne tokenowo polecenia, **bez znajomości nazw
plików**. Kontekst o kodzie dociąga graphify — Ty podajesz tylko CO, GDZIE (ekran) i
NAPRAW/ZDIAGNOZUJ.

## 1. Słownik Wispr Flow (custom dictionary)

Dodaj te słowa w apce Wispr Flow na telefonie (Settings → Dictionary / Vocabulary),
żeby transkrypcja ich nie przekręcała:

```
GROOVE
ZARIA
PalViz
paletyzacja
paleta
shipment
SKU
Kontrola HU
Handling Unit
Data Center
Magazyn 3D
graphify
Coolify
```

## 2. Szablon promptu (wklej jako snippet w Wispr Flow)

```
[NAPRAW / ZDIAGNOZUJ] problem: <co się dzieje>
na ekranie: <polska nazwa ekranu>
oczekuję: <jak ma być>
```

Zasada: nie podajesz plików. Ekran nazywasz po ludzku — Claude znajdzie kod grafem.

## 3. Nazwy ekranów (slot "na ekranie")

Paletyzacja · Wycena przesyłek · Magazyn 3D + heatmapa · Kontrola HU ·
Zadania i powiadomienia · Baza klientów · Data Center · widok 3D shipmentu ·
kalkulator palet · ZARIA (czat)

## 4. Przykłady dobrych promptów głosowych

- „ZDIAGNOZUJ problem: w widoku 3D shipmentu kartony jednego SKU rozjeżdżają się na
  różne palety. Oczekuję: jeden SKU trzyma się jednej palety. Potem napraw."
- „NAPRAW problem: kartony w widoku 3D mają być czerwone. Oczekuję: czerwone pudełka."
- „ZDIAGNOZUJ problem: wycena przesyłki nie liczy dystansu. Oczekuję: realny dystans z map."

## 5. Skróty które warto powiedzieć

- „**użyj graphify**" — wymusza szybki tor orientacji (mniej tokenów) przy pytaniach
  "jak działa / gdzie jest".
- „**zdiagnozuj, nie zmieniaj jeszcze kodu**" — najpierw przyczyna, potem poprawka.
