"""Import użytkowników z XLSX + brakujące wzory plików importu (Klienci, Stock HU,
SAP MARM).

Bezpieczeństwo haseł: kolumna `password` jest OPCJONALNA. Podane hasło jest od razu
hashowane (`set_password`) i NIGDY nie trafia do logów ani komunikatów; domyślnie
konto dostaje flagę „musi zmienić hasło przy pierwszym logowaniu"
(`UserProfile.must_change_password`), którą egzekwuje `PasswordChangeRequiredMiddleware`.
Bez hasła konto powstaje z hasłem nieużywalnym — logowanie tylko przez SSO albo po
ustawieniu hasła przez admina.
"""
from .core import (
    _admin_only, require_POST, messages, _after_import_redirect, _read_xlsx_as_dicts,
    transaction, _make_xlsx_response, _style_xlsx_header, _add_example_rows,
    _finalize_xlsx, _planner
)
from django.contrib.auth import get_user_model, views as auth_views
from django.contrib.auth.models import Group

from ..models import UserProfile, ControllerZone, UserModuleAccess
from ..roles import ALL_GROUPS
from ..platform_modules import MODULES

_USER_IMPORT_MAX_BYTES = 5 * 1024 * 1024
_USER_IMPORT_MAX_ROWS = 5000


def _split_list(val):
    """„Kontrola HU; Lider kontroli" → ['Kontrola HU', 'Lider kontroli'] (też po przecinku)."""
    s = str(val or "").strip()
    if not s:
        return []
    for sep in (";", ","):
        if sep in s:
            return [p.strip() for p in s.split(sep) if p.strip()]
    return [s]


def _flag(val, default=True):
    """1/0, tak/nie, true/false → bool. Puste = `default`."""
    s = str(val if val is not None else "").strip().lower()
    if not s:
        return default
    return s in ("1", "true", "tak", "yes", "y", "t", "x")


@_admin_only
@require_POST
def excel_import_users(request):
    """Import/aktualizacja kont: dane podstawowe, role, profil, strefy kontroli i
    nadpisania dostępu do modułów. Upsert po `username` — istniejące konto jest
    aktualizowane, nie duplikowane."""
    User = get_user_model()
    f = request.FILES.get("file")
    if not f:
        messages.error(request, "Nie wybrano pliku.")
        return _after_import_redirect(request, "ui:admin_users")
    if f.size > _USER_IMPORT_MAX_BYTES:
        messages.error(request, "Plik zbyt duży (max 5 MB).")
        return _after_import_redirect(request, "ui:admin_users")

    try:
        rows = _read_xlsx_as_dicts(f)
    except Exception as exc:
        messages.error(request, f"Błąd wczytywania pliku: {exc}")
        return _after_import_redirect(request, "ui:admin_users")
    if len(rows) > _USER_IMPORT_MAX_ROWS:
        messages.error(request, f"Zbyt dużo wierszy (max {_USER_IMPORT_MAX_ROWS}).")
        return _after_import_redirect(request, "ui:admin_users")

    groups_by_name = {g.name: g for g in Group.objects.filter(name__in=ALL_GROUPS)}
    module_keys = {m.key for m in MODULES}
    created = updated = pwd_set = 0
    errors = []

    for n, row in enumerate(rows, start=3):          # wiersz 1=nagłówki, 2=opisy
        username = str(row.get("username") or "").strip()
        if not username:
            errors.append(f"Wiersz {n}: brak username")
            continue
        try:
            with transaction.atomic():
                user, is_new = User.objects.get_or_create(username=username)
                user.first_name = str(row.get("first_name") or user.first_name or "")[:150]
                user.last_name = str(row.get("last_name") or user.last_name or "")[:150]
                email = str(row.get("email") or "").strip()
                if email:
                    user.email = email[:254]
                user.is_active = _flag(row.get("is_active"), default=True)
                user.is_staff = _flag(row.get("is_staff"), default=user.is_staff)
                user.is_superuser = _flag(row.get("is_superuser"), default=user.is_superuser)

                password = str(row.get("password") or "").strip()
                if password:
                    user.set_password(password)      # hash od razu — plaintext nigdzie nie zostaje
                    pwd_set += 1
                elif is_new:
                    user.set_unusable_password()     # konto bez hasła: SSO albo reset przez admina
                user.save()

                names = [g for g in _split_list(row.get("groups")) if g in groups_by_name]
                if names:
                    user.groups.set([groups_by_name[g] for g in names])

                prof, _ = UserProfile.objects.get_or_create(user=user)
                prof.phone = str(row.get("phone") or prof.phone or "")[:20]
                prof.department = str(row.get("department") or prof.department or "")[:80]
                section = str(row.get("section") or "").strip().upper()
                if section in ("GLS", "BUS", "GEIS", "EXPORT"):
                    prof.section = section
                # Wymuszenie zmiany hasła: domyślnie TAK, gdy import ustawił hasło.
                prof.must_change_password = _flag(row.get("must_change_password"),
                                                  default=bool(password))
                prof.save()

                zones = _split_list(row.get("control_zones"))
                if zones:
                    ControllerZone.objects.filter(user=user).delete()
                    ControllerZone.objects.bulk_create(
                        [ControllerZone(user=user, code=z[:40]) for z in zones],
                        ignore_conflicts=True)

                for field, allowed in (("modules_allow", True), ("modules_deny", False)):
                    for key in _split_list(row.get(field)):
                        if key in module_keys:
                            UserModuleAccess.objects.update_or_create(
                                user=user, module_key=key, defaults={"allowed": allowed})
            created += int(is_new)
            updated += int(not is_new)
        except Exception as exc:
            errors.append(f"Wiersz {n} ({username}): {exc}")

    msg = f"Import użytkowników: {created} nowych, {updated} zaktualizowanych"
    if pwd_set:
        msg += f", {pwd_set} z ustawionym hasłem (wymagana zmiana przy logowaniu)"
    if errors:
        msg += f". Błędy: {len(errors)} — " + "; ".join(errors[:5])
        messages.warning(request, msg)
    else:
        messages.success(request, msg + ".")
        from ..models import ImportRun
        ImportRun.record("users", rows=created + updated,
                         label=getattr(f, "name", ""), user=request.user,
                         error="; ".join(errors[:3]) if errors else "")
    return _after_import_redirect(request, "ui:admin_users")


@_admin_only
def excel_template_users(request):
    wb, ws, response = _make_xlsx_response("GROOVE_uzytkownicy_wzor.xlsx")
    ws.title = "Użytkownicy"
    roles = " | ".join(ALL_GROUPS)
    cols = [
        ("username",   "Login — unikalny, wymagany",                      18, "jkowalski"),
        ("first_name", "Imię",                                            14, "Jan"),
        ("last_name",  "Nazwisko",                                        16, "Kowalski"),
        ("email",      "E-mail (powiadomienia dopasowują po adresie)",    24, "j.kowalski@example.com"),
        ("password",   "Hasło — OPCJONALNE. Puste = konto bez hasła (SSO/reset przez admina)", 18, ""),
        ("must_change_password", "1 = wymuś zmianę przy 1. logowaniu (domyślnie 1, gdy podano hasło)", 16, 1),
        ("is_active",  "1 = konto aktywne (domyślnie 1)",                 11, 1),
        ("is_staff",   "1 = dostęp do /admin (domyślnie 0)",              11, 0),
        ("is_superuser", "1 = pełne uprawnienia (domyślnie 0)",           12, 0),
        ("groups",     f"Role po średniku. Dozwolone: {roles}",           34, "Kontrola HU; Podgląd"),
        ("phone",      "Telefon (SMS)",                                   14, "600100200"),
        ("department", "Dział (grupowanie w raporcie zużycia ZARIA)",     18, "Magazyn"),
        ("section",    "Sekcja skanera: GLS / BUS / GEIS / EXPORT",       12, "GLS"),
        ("control_zones", "Typy magazynu do kontroli, po średniku (puste = wszystkie)", 22, "92JU; 92EX"),
        ("modules_allow", "Moduły wymuszone na TAK (klucze, po średniku)", 22, "kontrola_hu"),
        ("modules_deny",  "Moduły zablokowane (klucze, po średniku)",      22, ""),
    ]
    _style_xlsx_header(ws, cols, "0E5E74")
    rows = [
        ["jkowalski", "Jan", "Kowalski", "j.kowalski@example.com", "", 1, 1, 0, 0,
         "Kontrola HU", "600100200", "Magazyn", "GLS", "92JU; 92EX", "", ""],
        ["anowak", "Anna", "Nowak", "a.nowak@example.com", "", 1, 1, 0, 0,
         "Master Data; Podgląd", "", "Master Data", "", "", "", ""],
        ["plider", "Piotr", "Lider", "p.lider@example.com", "", 1, 1, 0, 0,
         "Lider kontroli; Kontrola HU", "", "Magazyn", "BUS", "", "", "paletyzacja"],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)


@_planner
def excel_template_customers(request):
    """Wzór importu klientów. Importer rozpoznaje nagłówki SAP KNA1 rozmyto — ten wzór
    pokazuje kanoniczny zestaw kolumn (wymagane: Klient + Nazwa 1)."""
    wb, ws, response = _make_xlsx_response("GROOVE_klienci_wzor.xlsx")
    ws.title = "Klienci"
    cols = [
        ("Klient",        "Numer klienta (KUNNR) — WYMAGANE, klucz aktualizacji", 14, "0000123456"),
        ("Nazwa 1",       "Nazwa klienta — WYMAGANE",                             28, "Szpital Miejski"),
        ("Nazwa 2",       "Druga linia nazwy (opcjonalnie)",                      24, "Oddział Chirurgii"),
        ("Ulica",         "Ulica i numer",                                        26, "ul. Lipowa 12"),
        ("Kod pocztowy",  "Kod pocztowy",                                         13, "26-600"),
        ("Miasto",        "Miejscowość",                                          18, "Radom"),
        ("Kraj",          "Kod kraju (2 znaki)",                                  8,  "PL"),
        ("Telefon",       "Telefon kontaktowy",                                   16, "32 100 20 30"),
        ("Kategoria",     "Kategoria klienta (opcjonalnie)",                      16, "Szpital"),
        ("Przewoźnik",    "Domyślny przewoźnik (opcjonalnie)",                    16, "GLS"),
        ("Wysokość",      "Standardowa wys. palety [cm] 50–280 (opcjonalnie)",    14, 180),
    ]
    _style_xlsx_header(ws, cols, "F59E0B")
    rows = [
        ["0000123456", "Szpital Miejski", "Oddział Chirurgii", "ul. Lipowa 12", "26-600",
         "Radom", "PL", "32 100 20 30", "Szpital", "GLS", 180],
        ["0000123457", "Pogotowie Ratunkowe", "", "ul. Przemysłowa 4", "26-600",
         "Cieszyn", "PL", "", "Pogotowie", "BUS", 200],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)


@_planner
def excel_template_stock_hu(request):
    """Wzór stocku (HU). Importer dopasowuje nagłówki rozmyto (feed PowerBI/SAP) —
    wymagane są tylko pickHU + REF, reszta kolumn jest opcjonalna."""
    wb, ws, response = _make_xlsx_response("GROOVE_stock_hu_wzor.xlsx")
    ws.title = "Stock HU"
    cols = [
        ("Jednostka obsługi", "pickHU / SSCC — WYMAGANE, grupuje pozycje w paletę", 20, "111835082"),
        ("Produkt",           "REF / kod materiału — WYMAGANE",                     18, "DMOM10001"),
        ("Dostawa",           "Nr dostawy (puste = stock magazynowy)",              14, ""),
        ("Opis",              "Nazwa materiału",                                    28, "Rękawice nitrylowe M"),
        ("Partia",            "Nr partii / serii",                                  14, "693623613N"),
        ("Termin ważności",   "Data ważności (RRRR-MM-DD)",                         15, "2030-12-30"),
        ("Ilość",             "Ilość dostępna",                                     10, 50),
        ("Jedn. miary",       "JM: OP / KAR / PAL",                                 11, "KAR"),
        ("Miejsce składowania", "Kod lokalizacji",                                  18, "23L.04"),
        ("Typ magazynu",      "Typ magazynu (np. 92JU)",                            13, "92JU"),
        ("Kod klienta",       "KUNNR odbiorcy (dla dostaw)",                        14, ""),
        ("Waga",              "Waga brutto [kg] (opcjonalnie)",                     11, 453.4),
    ]
    _style_xlsx_header(ws, cols, "10B981")
    rows = [
        ["111835082", "DMOM10001", "", "Rękawice nitrylowe M", "693623613N", "2030-12-30",
         50, "KAR", "23L.04", "92JU", "", 453.4],
        ["111835083", "DMOM10003", "", "Rękawice nitrylowe L", "693623159N", "2030-12-23",
         30, "KAR", "23L.03", "92JU", "", 280.0],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)


@_planner
def excel_template_control_data(request):
    """Wzór KOMPLETU danych do kontroli HU — nagłówki 1:1 jak realny eksport SAP (20 kolumn),
    żeby magazyn wypełnił i wgrał bez rozjazdu z importerem. Kolumny (ustalenia grilla):
    co (REF+opis), partia dostawcy + ważność (weryfikowane), ilość PJM/AJM, lokalizacja,
    Dokument (dostawa; puste = błąd wsadu), KOD klienta (Odbiorca materiałów), kompletujący
    („Potwierdzone przez" — NIE „Autor"), Typ magazynu (decyduje o kontroli)."""
    wb, ws, response = _make_xlsx_response("GROOVE_dane_do_kontroli_wzor.xlsx")
    ws.title = "Dane do kontroli"
    cols = [
        ("Jednostka obsługi", "pickHU / SSCC — WYMAGANE, grupuje pozycje w paletę", 18, "11582503"),
        ("Produkt",           "REF / kod materiału — WYMAGANE",                     16, "DM15300-F"),
        ("Krótki opis produktu", "Nazwa materiału (weryfikacja wzrokowa)",          30, "Opaska gipsowa 10 cm"),
        ("Partia dostawcy",   "Nr partii producenta — WERYFIKOWANA z etykiety kartonu", 16, "193716114N"),
        ("Termin ważności",   "Data ważności (RRRR-MM-DD) — POTWIERDZANA z etykiety", 15, "2031-05-01"),
        ("Ilość PJM",         "Ilość w jednostce podstawowej (PJM)",                11, 2880),
        ("Podst. jedn. miary", "JM podstawowa: OP / SZT",                           14, "SZT"),
        ("Jednostka alternat.", "JM alternatywna: PAZ / KAR / OPZ",                 15, "PAZ"),
        ("Ilość AJM",         "Ilość w jednostce alternatywnej",                    10, 1),
        ("Zapas_HU.Miejsce składowania", "Lokalizacja pobrania",                    20, "05L.01"),
        ("Dokument",          "Nr dostawy — WYMAGANE (puste = błąd wsadu)",         13, "81828209"),
        ("Nagłówki.Odbiorca materiałów", "KOD klienta (KUNNR) — wymagania/VIP",     16, "11131116"),
        ("Nagłówki.Utworz. dnia", "Data utworzenia dokumentu (RRRR-MM-DD)",         15, "2026-08-14"),
        ("Nagłówki.Autor",    "Twórca dokumentu SAP (NIE kompletujący)",            14, "PTESTOWY"),
        ("Nagłówki.Klucz kraju/regionu", "Kraj odbiorcy (etykieta EXPORT)",         12, "BA"),
        ("Nagłówki.Status pobrania", "Zakończone / Częściowo zakończone / Nie rozpoczęte", 18, "Zakończone"),
        ("Potwierdzone przez", "Kompletujący — kogo wezwać przy niezgodności",      16, "JKOWAL"),
        ("Nagłówki.Nazwa odbiorcy 1", "Nazwa klienta (wyświetlana)",               26, "ADRIA DEMO D.O.O."),
        ("Nagłówki.Utworzono o godz.", "Godzina utworzenia (GG:MM:SS)",            13, "09:18:50"),
        ("Lokalizacje.Typ magazynu", "Typ magazynu — DECYDUJE, czy HU podlega kontroli", 16, "92EX"),
    ]
    _style_xlsx_header(ws, cols, "7C3AED")
    rows = [
        ["11582503", "DM15300-F", "Opaska gipsowa 10 cm", "193716114N",
         "2031-05-01", 2880, "SZT", "PAZ", 1, "05L.01", "81828209", "11131116",
         "2026-08-14", "PTESTOWY", "BA", "Zakończone", "JKOWAL", "ADRIA DEMO D.O.O.",
         "09:18:50", "92EX"],
        ["11582504", "DM1710121", "Kompres jałowy 17N 12W 10cmx10cm", "333620520N",
         "2030-09-21", 1200, "OP", "KAR", 60, "05L.01", "81812296", "11123259",
         "2026-08-14", "BPRZYKLAD", "MU", "Częściowo zakończone", "CTESTER",
         "ISLE DEMO LTD", "07:46:17", "WCEX"],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)


@_planner
def excel_template_fix(request):
    """Wzór fixów (stałych lokalizacji pickingowych). Kolumny zgodne z importerem
    (`excel_import_fix`): lokalizacja, REF, typ magazynu, min/maks, JM. Plik zwykle idzie
    wprost z eksportu SAP; wzór pokazuje wymagane kolumny (Miejsce składowania / Produkt /
    Ilość minimalna). Zasila „Zapas / min" na karcie produktu (MatInfo)."""
    wb, ws, response = _make_xlsx_response("GROOVE_fixy_wzor.xlsx")
    ws.title = "Fixy"
    cols = [
        ("Miejsce składowania", "Kod lokalizacji fixowej — WYMAGANE",   18, "B0-38-471A"),
        ("Produkt",             "REF / kod materiału — WYMAGANE",       18, "DMOM10001"),
        ("Typ magazynu",        "Typ magazynu (np. 0050)",              13, "0050"),
        ("Ilość minimalna",     "Próg uzupełnienia (min) — WYMAGANE",   14, 60),
        ("Maksymalna ilość",    "Pojemność fixa (maks)",                14, 260),
        ("JM - il. min",        "JM dla min/maks: OP / KAR / SZT",      14, "OP"),
        ("Data zmiany",         "Data ostatniej zmiany (RRRR-MM-DD)",   14, "2026-08-01"),
    ]
    _style_xlsx_header(ws, cols, "0F7D79")
    rows = [
        ["B0-38-471A", "DMOM10001", "0050", 60, 260, "OP", "2026-08-01"],
        ["B0-12-100A", "DMOM10003", "0050", 40, 180, "OP", "2026-08-01"],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)


@_planner
def excel_template_marm(request):
    """Wzór SAP MARM — plik wgrywasz zwykle wprost z SAP; ten wzór pokazuje, które
    kolumny muszą się w nim znaleźć (wymagane: material + unit)."""
    wb, ws, response = _make_xlsx_response("GROOVE_sap_marm_wzor.xlsx")
    ws.title = "SAP MARM"
    cols = [
        ("material", "Numer materiału (MATNR) — WYMAGANE",                18, "DMOM10001"),
        ("unit",     "Jednostka miary (MEINH): JP/OP/KAR/PAL — WYMAGANE", 10, "KAR"),
        ("umrez",    "Licznik przelicznika (ile JP w tej JM)",            12, 10),
        ("umren",    "Mianownik przelicznika",                            12, 1),
        ("laeng",    "Długość",                                           10, 29),
        ("breit",    "Szerokość",                                         10, 25),
        ("hoehe",    "Wysokość",                                          10, 22),
        ("meabm",    "Jednostka wymiaru (CM/MM)",                         12, "CM"),
        ("volum",    "Objętość",                                          10, 0.01595),
        ("voleh",    "Jednostka objętości (M3)",                          12, "M3"),
        ("brgew",    "Waga brutto",                                       11, 4.58),
        ("gewei",    "Jednostka wagi (KG)",                               11, "KG"),
        ("ean11",    "Kod EAN dla tej jednostki",                         16, "5900000001500"),
    ]
    _style_xlsx_header(ws, cols, "64748B")
    rows = [
        ["DMOM10001", "OP",  1,  1, 21,  12,  5.5, "CM", 0.00139, "M3", 0.458, "KG", "5900000001500"],
        ["DMOM10001", "KAR", 10, 1, 29,  25,  22,  "CM", 0.01595, "M3", 4.58,  "KG", ""],
        ["DMOM10001", "PAL", 990, 1, 120, 80, 213, "CM", 2.0448,  "M3", 453.4, "KG", ""],
    ]
    _add_example_rows(ws, cols, rows)
    return _finalize_xlsx(wb, ws, response)


class GroovePasswordChangeView(auth_views.PasswordChangeView):
    """Zmiana hasła + zdjęcie flagi `must_change_password` (konto z hasłem nadanym hurtem
    przy imporcie jest tu przekierowywane przez PasswordChangeRequiredMiddleware)."""
    template_name = "ui/auth/password_change_form.html"
    success_url = "/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        prof = getattr(self.request.user, "profile", None)
        ctx["must_change"] = bool(prof and prof.must_change_password)
        return ctx

    def form_valid(self, form):
        resp = super().form_valid(form)
        prof = getattr(self.request.user, "profile", None)
        if prof and prof.must_change_password:
            prof.must_change_password = False
            prof.save(update_fields=["must_change_password"])
        messages.success(self.request, "Hasło zostało zmienione.")
        return resp


__all__ = [
    "GroovePasswordChangeView",
    "excel_import_users",
    "excel_template_users",
    "excel_template_customers",
    "excel_template_stock_hu",
    "excel_template_control_data",
    "excel_template_fix",
    "excel_template_marm",
]
