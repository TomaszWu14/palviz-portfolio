"""Parser eksportu zadań magazynowych EWM (WT) z monitora magazynu (/SCWM/MON) → CSV/XLSX.

Czysty Python (bez ORM): aliasy nagłówków PL/EN/techniczne SAP, liczby z przecinkiem
dziesiętnym, daty SAP (osobno data i czas albo znacznik), mapowanie rodzaju procesu
magazynowego na rodzaj ruchu. Plik czytany strumieniowo (openpyxl read_only / csv) —
zapis partiami robi `ewm_tasks_import`.
"""
import csv
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Pole → aliasy nagłówka po normalizacji (`norm_header`: małe litery, bez ogonków i kropek).
FIELD_ALIASES = {
    "task_no": ["zadanie magazynowe", "zad mag", "zadanie mag", "nr zadania magazynowego",
                "nr zadania", "nr wt", "wt", "warehouse task", "whse task", "tanum"],
    "process_type": ["rodzaj procesu magazynowego", "rodz pr mag", "rodzprmag", "rodzaj proc mag",
                     "rodzaj procesu", "proces magazynowy", "warehouse process type",
                     "whse process type", "wpt", "procty"],
    "category": ["kategoria procesu magazynowego", "kategoria procesu", "kat proc mag",
                 "warehouse process category", "whse process category", "process category", "trart"],
    "activity": ["dzialanie", "czynnosc", "activity", "act type"],
    "src_location": ["zrodlowe miejsce skladowania", "zr msc sklad", "zrodl msc sklad",
                     "zrodlowe msc skladowania", "miejsce zrodlowe", "lokalizacja zrodlowa",
                     "source storage bin", "source bin", "vlpla"],
    "dst_location": ["docelowe miejsce skladowania", "doc msc sklad", "docel msc sklad",
                     "docelowe msc skladowania", "miejsce docelowe", "lokalizacja docelowa",
                     "destination storage bin", "dest storage bin", "destination bin", "nlpla"],
    "material": ["produkt", "material", "indeks", "product", "matnr"],
    "lot": ["partia", "batch", "charg"],
    "qty": ["ilosc docelowa w ajm", "ilosc docelowa", "ilosc w ajm", "ilosc rzeczywista", "ilosc",
            "source target qty in aun", "target quantity", "quantity", "vsola", "vsolm"],
    "unit": ["ajm", "jm", "jednostka miary", "jednostka", "alternatywna jednostka miary",
             "podst jm", "unit", "alternative unit of measure", "base unit of measure", "altme", "meins"],
    "src_hu": ["hu zrodlowa", "zrodlowa hu", "zr hu", "zrodlowa jednostka manipulacyjna",
               "source handling unit", "source hu", "vlenr"],
    "dst_hu": ["hu docelowa", "docelowa hu", "doc hu", "docelowa jednostka manipulacyjna",
               "destination handling unit", "destination hu", "dest hu", "nlenr"],
    "document": ["dokument", "nr dokumentu", "dostawa", "dokument referencyjny", "document",
                 "document number", "delivery", "docno", "refdocno"],
    # data albo pełny znacznik (SAP: osobno „Utworzono dn.” i „Utworzono o”, albo timestamp)
    "created_date": ["utworzono dn", "data utworzenia", "utworzono", "znacznik czasu utworzenia",
                     "created on", "creation date", "created at timestamp", "creation timestamp"],
    "created_time": ["utworzono o", "czas utworzenia", "godzina utworzenia", "created at", "creation time"],
    "confirmed_date": ["potwierdzono dn", "data potwierdzenia", "data potw", "potwierdzono",
                       "znacznik czasu potwierdzenia", "confirmation date", "confirmed on",
                       "confirmation timestamp", "confirmed at timestamp"],
    "confirmed_time": ["potwierdzono o", "czas potwierdzenia", "czas potw", "godzina potwierdzenia",
                       "confirmation time", "confirmed at"],
    "user": ["potwierdzone przez", "potwierdzil", "potw przez", "uzytkownik", "nazwa uzytkownika",
             "confirmed by", "user", "user name", "wykonawca", "processor"],
    "resource": ["zasob", "resource", "rsrc", "wozek"],
    "queue": ["kolejka", "queue"],
    "status": ["status zadania magazynowego", "status zadania", "status", "warehouse task status", "tostat"],
}

FIELD_LABELS = {
    "task_no": "Nr WT", "process_type": "Rodzaj procesu mag.", "category": "Kategoria procesu",
    "activity": "Działanie", "src_location": "Lokalizacja źródłowa", "dst_location": "Lokalizacja docelowa",
    "material": "Materiał", "lot": "Partia", "qty": "Ilość", "unit": "JM", "src_hu": "HU źródłowa",
    "dst_hu": "HU docelowa", "document": "Dokument", "created_date": "Utworzono (data)",
    "created_time": "Utworzono (czas)", "confirmed_date": "Potwierdzono (data)",
    "confirmed_time": "Potwierdzono (czas)", "user": "Użytkownik", "resource": "Zasób (wózek)",
    "queue": "Kolejka", "status": "Status",
}

KIND_LABELS = {"putaway": "Przyjęcie / odłożenie", "replenishment": "Uzupełnienie",
               "picking": "Kompletacja", "outbound": "Wydanie / załadunek", "move": "Przesunięcie"}

# Rodzaje procesu (słownik EWM ACME), których pierwsza cyfra kłamie albo nie jest cyfrą.
PROCESS_KINDS = {
    "2020": "picking", "3010": "replenishment", "3011": "replenishment", "3012": "replenishment",
    "301D": "replenishment", "3100": "replenishment", "3060": "putaway", "3065": "putaway",
    "3070": "outbound", "3071": "outbound", "37BG": "outbound", "9010": "putaway",
    "X010": "putaway", "X020": "outbound", "FTCU": "putaway", "FTPD": "putaway",
    "OFTC": "picking", "OFTP": "picking", "OMDX": "picking",
    "KTSI": "putaway", "KTRI": "putaway", "KTSO": "picking", "KTRO": "picking",
}
ACTIVITY_KINDS = {"PTWY": "putaway", "PICK": "picking", "REPL": "replenishment",
                  "INTL": "move", "STCH": "move", "INVE": "move"}
# Słowa w nadpisaniach mapowania („2010 = wydanie”) → rodzaj.
KIND_WORDS = [("przyj", "putaway"), ("odloz", "putaway"), ("umieszcz", "putaway"), ("putaway", "putaway"),
              ("uzupel", "replenishment"), ("repl", "replenishment"),
              ("komplet", "picking"), ("pobran", "picking"), ("pick", "picking"),
              ("wydan", "outbound"), ("zaladun", "outbound"), ("outbound", "outbound"),
              ("przesun", "move"), ("transfer", "move"), ("move", "move")]

DATE_FORMATS = ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y.%m.%d", "%Y%m%d")
TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%H%M%S")
DT_FORMATS = tuple(f"{d} {t}" for d in DATE_FORMATS for t in TIME_FORMATS[:2]) + (
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y%m%d%H%M%S")
MAX_ERRORS = 50


def norm_header(h):
    s = unicodedata.normalize("NFKD", str(h or "").replace("ł", "l").replace("Ł", "L"))
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def map_columns(headers):
    """{pole: indeks kolumny}. Najpierw dokładne aliasy, potem nagłówek zaczynający się aliasem
    („Źródłowe miejsce składowania (nr)”); każda kolumna trafia do co najwyżej jednego pola."""
    norm = [norm_header(h) for h in headers]
    cols, used = {}, set()
    for exact in (True, False):
        for field, aliases in FIELD_ALIASES.items():
            if field in cols:
                continue
            for alias in aliases:
                hit = next((i for i, h in enumerate(norm) if i not in used and h and (
                    h == alias if exact else len(alias) >= 5 and h.startswith(alias + " "))), None)
                if hit is not None:
                    cols[field] = hit
                    used.add(hit)
                    break
    return cols


def missing_required(cols):
    out = []
    if "src_location" not in cols and "dst_location" not in cols:
        out.append("lokalizacja źródłowa lub docelowa")
    if "confirmed_date" not in cols and "confirmed_time" not in cols:
        out.append("data/znacznik potwierdzenia")
    return out


# ─── wartości ────────────────────────────────────────────────────────────────

def parse_number(v):
    """„1.234,5” / „1,234.5” / „1 234,5” / „5-” (minus SAP na końcu) / liczba → float; ValueError."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if not s:
        return None
    neg = s.endswith("-")
    s = s.rstrip("-")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    val = float(s)
    return -val if neg else val


def _parse_dt(v):
    """→ (datetime naiwny, czy_ma_czas) albo None; ValueError przy nieczytelnym tekście."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):                            # Excel: komórka daty = północ
        return v, v.time() != time()
    if isinstance(v, date):
        return datetime.combine(v, time()), False
    s = str(v).strip()
    if re.fullmatch(r"\d{14}[,.]\d+", s):                 # SAP TIMESTAMPL z ułamkiem sekundy
        s = s[:14]
    if not s or set(s) <= {"0", " ", ".", ":", "-"}:       # pusty znacznik SAP (00.00.0000)
        return None
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(s, fmt), True
        except ValueError:
            pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt), False
        except ValueError:
            pass
    raise ValueError(f"nieczytelna data „{s}”")


def _parse_time(v):
    if v is None or v == "":
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    if isinstance(v, (int, float)) and 0 <= v < 1:         # Excel: ułamek doby
        return (datetime.min + timedelta(seconds=round(v * 86400))).time()
    s = str(v).strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            pass
    parsed = _parse_dt(s)                                  # pełny znacznik w kolumnie czasu
    return parsed[0].time() if parsed else None


def parse_stamp(date_val, time_val=None, tz="Europe/Warsaw"):
    """Data (+ osobny czas, jak w eksporcie SAP) → datetime ze strefą `tz` albo None."""
    parsed = _parse_dt(date_val)
    if parsed is None:                                     # sam znacznik w kolumnie „czas”
        try:
            parsed = None if isinstance(time_val, time) else _parse_dt(time_val)
        except ValueError:
            parsed = None
        return parsed[0].replace(tzinfo=ZoneInfo(tz)) if parsed and parsed[1] else None
    dt, has_time = parsed
    t = _parse_time(time_val)
    if t is not None and not has_time:
        dt = datetime.combine(dt.date(), t)
    return dt.replace(tzinfo=ZoneInfo(tz))


def _text(v, n):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():            # Excel: nr WT / materiał jako liczba
        v = int(v)
    return str(v).strip()[:n]


# ─── mapowanie rodzaju ruchu ─────────────────────────────────────────────────

def kind_from_word(word):
    w = norm_header(word)
    return next((k for prefix, k in KIND_WORDS if w.startswith(prefix)), None)


def parse_overrides(text):
    """„2010 = wydanie” (linia na proces) → ({proces: rodzaj}, [błędy])."""
    out, errors = {}, []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        code, sep, word = line.partition("=")
        kind = kind_from_word(word) if sep else None
        if not code.strip() or kind is None:
            errors.append(f"„{line}” — oczekiwano: kod procesu = przyjęcie/uzupełnienie/"
                          "kompletacja/wydanie/przesunięcie")
            continue
        out[code.strip().upper()] = kind
    return out, errors


def map_kind(process_type="", queue="", activity="", category="", overrides=None):
    """Rodzaj procesu mag. → rodzaj ruchu. Kolejność: nadpisania → słownik wyjątków →
    pierwsza cyfra (1 przyjęcie, 2 wydanie; kolejka z „PICK” = kompletacja) → działanie
    (PTWY/PICK/REPL…) → kategoria procesu → przesunięcie."""
    pt = (process_type or "").strip().upper()
    if overrides and pt in overrides:
        return overrides[pt]
    if pt in PROCESS_KINDS:
        return PROCESS_KINDS[pt]
    act = (activity or "").strip().upper()
    lead = pt[:1] if pt[:1] in ("1", "2", "3", "4", "9") else ""
    if not lead and act in ACTIVITY_KINDS:
        return ACTIVITY_KINDS[act]
    lead = lead or (category or "").strip()[:1]
    if lead == "1":
        return "putaway"
    if lead == "2":
        return "picking" if "PICK" in (queue or "").upper() else "outbound"
    return "move"


def is_cancelled(status):
    s = norm_header(status)
    return s == "a" or s.startswith(("anul", "cancel"))


def parse_row(cells, cols, tz="Europe/Warsaw", overrides=None):
    """Wiersz → dict pól `WarehouseTask` (+ `cancelled`); None = pusty wiersz;
    ValueError z opisem po polsku = wiersz błędny."""
    if not any(c not in (None, "") and str(c).strip() for c in cells):
        return None

    def raw(field):
        i = cols.get(field)
        return cells[i] if i is not None and i < len(cells) else None

    src, dst = _text(raw("src_location"), 50).upper(), _text(raw("dst_location"), 50).upper()
    if not src and not dst:
        raise ValueError("brak lokalizacji źródłowej i docelowej")
    try:
        qty = parse_number(raw("qty"))
    except ValueError:
        raise ValueError(f"nieczytelna ilość „{raw('qty')}”") from None
    pt, queue = _text(raw("process_type"), 10).upper(), _text(raw("queue"), 40)
    return {
        "task_no": _text(raw("task_no"), 20), "process_type": pt,
        "kind": map_kind(pt, queue, _text(raw("activity"), 10), _text(raw("category"), 4), overrides),
        "src_location": src, "dst_location": dst,
        "material": _text(raw("material"), 50), "lot": _text(raw("lot"), 32),
        "qty": qty, "unit": _text(raw("unit"), 10),
        "src_hu": _text(raw("src_hu"), 40), "dst_hu": _text(raw("dst_hu"), 40),
        "document": _text(raw("document"), 35),
        "created_at": parse_stamp(raw("created_date"), raw("created_time"), tz),
        "confirmed_at": parse_stamp(raw("confirmed_date"), raw("confirmed_time"), tz),
        "user": _text(raw("user"), 40), "resource": _text(raw("resource"), 40), "queue": queue,
        "cancelled": is_cancelled(_text(raw("status"), 20)),
    }


# ─── plik ────────────────────────────────────────────────────────────────────

def _encoding(head):
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        if exc.start < len(head) - 3:                      # nie tylko ucięty znak na końcu bufora
            return "cp1250"                                # eksport SAP z polskiego Windowsa
    return "utf-8-sig"


def _delimiter(text):
    first = text.splitlines()[0] if text else ""
    return max((";", "\t", ",", "|"), key=first.count)


def iter_table(path, filename=None):
    """Wiersze pliku jako listy wartości — strumieniowo, bez ładowania całości do pamięci."""
    name = (filename or str(path)).lower()
    if name.endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for row in wb.active.iter_rows(values_only=True):
                yield list(row)
        finally:
            wb.close()
        return
    if name.endswith(".xls"):
        raise ValueError("Format .xls nie jest obsługiwany — zapisz eksport jako .xlsx albo CSV.")
    with open(path, "rb") as fb:
        head = fb.read(65536)
    enc = _encoding(head)
    delim = _delimiter(head.decode(enc, errors="replace"))
    with open(path, encoding=enc, errors="replace", newline="") as fh:
        yield from csv.reader(fh, delimiter=delim)


class Scan:
    """Przebieg po pliku: nagłówek → mapowanie kolumn → wiersze sparsowane albo błędy,
    z licznikami do podglądu i raportu importu."""

    def __init__(self, path, filename=None, tz="Europe/Warsaw", overrides=None):
        self.tz, self.overrides = tz, overrides or {}
        self._rows = iter_table(path, filename)
        self.headers, self.line = [], 0
        for cells in self._rows:                           # nagłówek = 1. wiersz z ≥ 2 wartościami
            self.line += 1
            if sum(1 for c in cells if c not in (None, "") and str(c).strip()) >= 2:
                self.headers = [_text(c, 80) for c in cells]
                break
        self.cols = map_columns(self.headers)
        self.missing = missing_required(self.cols) if self.headers else ["nagłówek"]
        self.rows = self.imported = self.error_count = self.unconfirmed = self.cancelled = 0
        self.errors, self.kinds, self.processes = [], Counter(), Counter()
        self.resources, self.first, self.last = set(), None, None

    def __iter__(self):
        """Poprawne, nieanulowane zadania (dicty pól modelu)."""
        if self.missing:
            return
        for cells in self._rows:
            self.line += 1
            try:
                row = parse_row(cells, self.cols, self.tz, self.overrides)
            except ValueError as exc:
                self.rows += 1
                self.error_count += 1
                if len(self.errors) < MAX_ERRORS:
                    self.errors.append({"line": self.line, "error": str(exc)})
                continue
            if row is None:
                continue
            self.rows += 1
            if row.pop("cancelled"):
                self.cancelled += 1
                continue
            self._count(row)
            yield row

    def _count(self, row):
        self.imported += 1
        self.kinds[row["kind"]] += 1
        self.processes[(row["process_type"] or "—", row["kind"])] += 1
        if len(self.resources) < 1000:
            self.resources.add(row["resource"] or row["user"])
        at = row["confirmed_at"]
        if at is None:
            self.unconfirmed += 1
        else:
            self.first = at if self.first is None or at < self.first else self.first
            self.last = at if self.last is None or at > self.last else self.last

    def close(self):
        """Zwalnia plik (Windows nie skasuje otwartego pliku; openpyxl trzyma uchwyt)."""
        self._rows.close()

    def columns(self):
        """[(etykieta pola, nagłówek z pliku)] w kolejności pól."""
        return [(FIELD_LABELS[f], self.headers[self.cols[f]]) for f in FIELD_ALIASES if f in self.cols]

    def unmapped_headers(self):
        used = set(self.cols.values())
        return [h for i, h in enumerate(self.headers) if i not in used and h]

    def stats(self):
        return {
            "rows": self.rows, "imported": self.imported, "errors": self.error_count,
            "error_list": self.errors, "unconfirmed": self.unconfirmed, "cancelled": self.cancelled,
            "kinds": [{"kind": k, "label": KIND_LABELS[k], "count": self.kinds.get(k, 0)}
                      for k in KIND_LABELS],
            "processes": [{"process": p, "kind": k, "label": KIND_LABELS[k], "count": n}
                          for (p, k), n in self.processes.most_common(40)],
            "resources": len(self.resources - {""}),
            "columns": self.columns(), "unmapped_headers": self.unmapped_headers(),
        }
