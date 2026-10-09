# GROOVE — plan przejścia na mikroserwisy (dokument roboczy)

> Kierunek docelowy: **jedna domena, 6–7 niezależnych aplikacji** — każda z własnym
> repozytorium, własną bazą i osobnym deployem — spięte bramą (Traefik) i wspólnym
> logowaniem (SSO). Migracja metodą **strangler fig**: wydzielamy serwis po serwisie,
> bez przepisywania wszystkiego naraz. To propozycja do dyskusji z zespołem, nie wiążący projekt.

Punkt wyjścia: ~56 000 linii kodu, 8 modułów, 50 modeli, monolit Django (`palletweb` + app `ui`).

---

## Zasada: nie „wielki przepis", tylko duszenie monolitu

Monolit zostaje na produkcji i działa. Wokół niego stawiamy bramę i SSO, a potem
**wyprowadzamy po jednym serwisie naraz** — każdy dostaje własne repo, bazę i adres.
Monolit kurczy się, aż zostaje z niego rdzeń danych wzorcowych. Na każdym etapie system
jest w całości używalny.

> **Twardy koszt pełnych mikroserwisów:** osobna baza na serwis znosi dwie rzeczy, które
> dziś masz za darmo — **jedno logowanie** (trzeba dołożyć serwer tożsamości) i **wspólne
> dane** (koniec z `JOIN`-ami — dane cudzego serwisu bierzesz przez API, ze spójnością
> ostateczną). To realny, stały narzut operacyjny. Plan minimalizuje go, wprowadzając SSO
> i dane wzorcowe jako pierwsze.

---

## Obraz docelowy

```
                         groove.example.com   (jedna domena)
                                  │
                     ┌────────────▼─────────────┐
                     │  Traefik — brama / gateway │  routing po ścieżce lub subdomenie + TLS
                     └────────────┬─────────────┘
   ┌─────────┬──────────┬─────────┼──────────┬──────────┬──────────┬─────────┐
   ▼         ▼          ▼         ▼          ▼          ▼          ▼         ▼
 portal  master-data  paletyzacja transport magazyn-3d kontrola-hu wydruk-hu zadania
  (—)     (core DB)    (własna)   (własna)  (własna)   (własna)   (własna)  (własna)
   └─────────┴──────────┴────┬────┴──────────┴──────────┴──────────┴─────────┘
                             │
        ┌────────────────────┴─────────────────────┐
        ▼                                           ▼
  Keycloak / Authentik (SSO, OIDC)           Event bus (Redis / RabbitMQ)
  jedno logowanie + role                     zdarzenia domenowe, spójność ostateczna
```

Brama routuje jedną domenę po ścieżce (`/kontrola`) lub subdomenie
(`kontrola.groove.example.com`) do niezależnych aplikacji. Keycloak daje jeden login dla
wszystkich. Każdy serwis ma własną bazę na swoje dane; dane wzorcowe (produkty, klienci,
rejestr HU) udostępnia serwis `master-data` przez API.

---

## Podział na serwisy — kto jest właścicielem których danych

Najtrudniejsza decyzja w mikroserwisach to **własność danych**. Serwis czyta cudze dane
tylko przez API właściciela — nigdy bezpośrednio z jego bazy.

| Serwis | Moduły dziś | Własna baza — co posiada | Czyta z |
|---|---|---|---|
| `master-data` | Data Center, Baza klientów | Produkty, kartony, opakowania, klienci, **HU (rejestr)** | — |
| `paletyzacja` | Paletyzacja | Kalkulacje, warianty układów | `master-data` (produkty) |
| `transport` | Wycena przesyłek | Wysyłki, przewoźnicy, wyceny, trasy | `master-data` (klienci, HU) |
| `magazyn-3d` | Magazyn 3D + heatmapa | Lokalizacje, model regałów, sloty, aktywność | `master-data` (HU, stan) |
| `kontrola-hu` | Kontrola HU | Wyniki kontroli, statusy, zdarzenia, jakość | `master-data` (HU, produkty) |
| `wydruk-hu` | Wydruk HU | Projekty numeracji, przebiegi druku | — (prawie samodzielny) |
| `zadania` | Zadania i powiadomienia | Zadania, powiadomienia, subskrypcje | zdarzenia z innych serwisów |

> **Kluczowy szew:** `HandlingUnit` tworzy dziś Transport (z wysyłki), a konsumuje Kontrola
> HU. Dlatego **rejestr HU trzyma `master-data`** (jedno źródło prawdy), a Transport i
> Kontrola operują na nim przez API + zdarzenia. Bez tego HU „rozjedzie się" między bazami.

---

## Cztery twarde problemy (czego nie kupisz za darmo)

1. **🔐 Jedno logowanie** — osobne bazy = osobne tabele użytkowników. Wspólne cookie
   przestaje działać → potrzebny `Keycloak/Authentik` (OIDC), któremu ufają wszystkie serwisy.
2. **🔗 Koniec z JOIN-ami** — nie ma zapytań między bazami. Dane cudzego serwisu bierzesz
   przez jego **API**, a nie `FK/JOIN`. Część zapytań trzeba przeprojektować.
3. **⏳ Spójność ostateczna** — „stan świata" jest rozproszony. Zmiany propagują się
   **zdarzeniami** (event bus), z opóźnieniem. Trzeba projektować pod chwilową niespójność.
4. **🛠️ Narzut operacyjny** — 6–7 deployów, 6–7 baz, gateway, SSO, event bus,
   obserwowalność. Dla małego zespołu to realny, **stały** koszt utrzymania.

---

## Plan migracji — fazami, bez przestoju

| Faza | Co | Efekt / koszt |
|---|---|---|
| **0. Wariant modułowy** ✅ *zrobione* | `GROOVE_VARIANT=hu` — ten sam kod/baza, instancja serwuje tylko wybrane moduły. Pozwala już postawić „Kontrola HU" osobno. | niezależny deploy · gotowe |
| **1. Brama + portal** | Traefik routuje domenę do usług; hub GROOVE = portal. Usługi to na razie warianty jednego obrazu nad wspólną bazą. | jedna domena · ~3–5 dni · niskie ryzyko |
| **2. SSO (Keycloak)** | Centralny login **zanim** rozjedziemy bazy użytkowników. Wszystkie warianty logują przez OIDC; role/macierz z Keycloak. | nowa usługa · ~1–2 tyg. · średnie ryzyko |
| **3. `master-data` + API** | Rdzeń (produkty, klienci, rejestr HU) zostaje w obecnej bazie i dostaje REST API. Reszta przestaje sięgać do tych tabel bezpośrednio. | API + kontrakty · ~2–3 tyg. · wysokie ryzyko |
| **4. Wyprowadzanie serwisów** | Po jednym, od najbardziej samodzielnych: `wydruk-hu` → `kontrola-hu` → `zadania`. Każdy: własne repo + baza, czyta core przez API. | ~1–2 tyg./serwis · własność zespołów |
| **5. Event bus** | Redis/RabbitMQ do zdarzeń domenowych — serwisy reagują asynchronicznie zamiast odpytywać się nawzajem. | infrastruktura · ~1 tydz. |
| **6. Domknięcie** | Wyprowadzenie `transport`, `magazyn-3d`, `paletyzacja` + obserwowalność per serwis (Sentry/OTel już w kodzie). Monolit znika. | iteracyjnie · pełne mikroserwisy |

---

## Rekomendacja — co zrobić najpierw

- **Fazy 1–2 to fundament** — brama + SSO. Bez SSO nie ma sensu rozdzielać baz (login się
  rozsypie). Zrób je zanim ruszysz dane.
- **Nie rób 7 baz naraz.** Wyprowadź najpierw jeden, najbardziej samodzielny serwis
  (`wydruk-hu`) — „próba generalna" całego wzorca na niskim ryzyku.
- **Rejestr HU i dane wzorcowe = jedno źródło prawdy** w `master-data`. To najczęstsze
  miejsce, gdzie mikroserwisy się wykrwawiają — priorytet.
- **Zważ koszt.** Dla małego zespołu pełne mikroserwisy to duży, stały narzut. Jeśli głównym
  celem jest niezależny deploy i skalowanie — **wariant + wspólna baza + brama** daje ~80%
  korzyści za ~20% wysiłku.

---

*Faza 0 (`GROOVE_VARIANT`) jest już w mainie. Reszta to propozycja kolejności do ustalenia z zespołem.*
