# ZARIA — wysyłka mailem przez Outlook: konfiguracja dla IT

> Dokument dla działu IT (F5 roadmapy ZARIA). Dwie ścieżki wysyłki: **mailto/.eml** (działa od razu,
> bez konfiguracji) oraz **Microsoft Graph** (opcjonalna, preferowana — tworzy wersję roboczą wprost
> w skrzynce użytkownika). ZARIA **nigdy nie wysyła maila sama** — przygotowuje wersję roboczą, wysyła
> człowiek. Każde przygotowanie jest logowane (`ZariaMailLog`).

## Stan obecny (bez konfiguracji IT)

Działa ścieżka **mailto/.eml**:
- „Wyślij mailem" w panelu kontekstu rozmowy → formularz (zakres, Do, DW, temat).
- „Przygotuj w Outlooku" otwiera `mailto:` z tematem i treścią (Outlook desktop/web podłapuje).
- Przy długiej treści (link > ~1800 znaków) treść w `mailto:` jest skracana, a pełna wersja dostępna
  jako **plik `.eml`** (otwiera się w Outlooku z kompletną treścią).
- Do każdej wiadomości dopisywana jest **nienegocjowalna stopka**: „Wygenerowane przez asystenta AI
  ZARIA — model X, data Y. Treść wymaga weryfikacji przed użyciem."

Ta ścieżka nie wymaga żadnej rejestracji w Entra ID.

## Ścieżka Microsoft Graph (opcjonalna, do wdrożenia przez IT)

Zaleta: wersja robocza naprawdę ląduje w folderze **Wersje robocze** skrzynki zalogowanego
użytkownika (`POST /me/messages`), z pełnym HTML i bez limitu długości; otwierana w Outlooku przez
`webLink`.

### Wymagana rejestracja aplikacji w Entra ID (Azure AD)

1. **Azure Portal → Microsoft Entra ID → App registrations → New registration.**
   - Nazwa: `GROOVE-ZARIA-Mail`.
   - Supported account types: *Single tenant* (konta tylko z tenanta ACME).
   - Redirect URI (Web): `https://groove.example.com/zaria/outlook/callback/`.
2. **API permissions → Add a permission → Microsoft Graph → Delegated permissions:**
   - `Mail.ReadWrite` (tworzenie/edycja wersji roboczych),
   - `offline_access` (odświeżanie tokenu),
   - `User.Read` (podstawowy profil).
   - Kliknąć **Grant admin consent for ACME** (zgoda administratora tenanta — inaczej użytkownicy
     zobaczą monit przy pierwszym użyciu).
3. **Certificates & secrets → New client secret** — zapisać wartość (widoczna raz).
4. Wartości do przekazania administratorowi GROOVE (zmienne środowiskowe, sekrety Coolify):
   - `ZARIA_OUTLOOK_TENANT_ID` = *Directory (tenant) ID*,
   - `ZARIA_OUTLOOK_CLIENT_ID` = *Application (client) ID*,
   - `ZARIA_OUTLOOK_CLIENT_SECRET` = wartość sekretu.

### Model uprawnień i prywatność

- Uprawnienie jest **delegowane** — ZARIA działa **w imieniu zalogowanego użytkownika**, widzi tylko
  jego skrzynkę, nie ma dostępu aplikacyjnego do cudzych maili.
- Zakres `Mail.ReadWrite` pozwala **tworzyć wersję roboczą**; wysyłkę wykonuje użytkownik ręcznie
  w Outlooku (nie nadajemy `Mail.Send`).
- Podpowiedzi odbiorców z katalogu firmowego (opcjonalnie) wymagałyby `People.Read` / `User.ReadBasic.All`
  — **domyślnie NIE nadawać**; ZARIA korzysta z historii ostatnio używanych adresów po stronie klienta.

### Bezpieczeństwo

- Token użytkownika (refresh) przechowywany po stronie serwera, szyfrowany; nigdy nie trafia do
  przeglądarki.
- Callback OAuth tylko na kanoniczną domenę produkcyjną (`https://groove.example.com`).
- Każde utworzenie wersji roboczej → wpis `ZariaMailLog` (kto, kiedy, do kogo, wątek, kanał=`graph`) —
  to potencjalna droga wypływu danych na zewnątrz firmy, więc podlega audytowi w panelu admina ZARIA.

## Checklist wdrożenia Graph

- [ ] Rejestracja aplikacji w Entra ID (kroki 1–3)
- [ ] Admin consent dla `Mail.ReadWrite` + `offline_access`
- [ ] Ustawione sekrety `ZARIA_OUTLOOK_*` w Coolify
- [ ] Test: użytkownik loguje się przez OAuth, ZARIA tworzy wersję roboczą, `webLink` otwiera ją w Outlooku
- [ ] Weryfikacja wpisów w `ZariaMailLog`
