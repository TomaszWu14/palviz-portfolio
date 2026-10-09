"""Seed słownika: kategoria 'process_type' — rodzaje procesów magazynowych z SAP.
Grupy pomocnicze do filtrowania w UI. Idempotentne (update_or_create po (category, code))."""
from django.db import migrations

PUT = "Umieszczenie w magazynie"
ISSUE = "Wydanie z magazynu"
REPL = "Uzupełnienie"
MOVE = "Transfer / optymalizacja"
REPOST = "Przeksięgowanie / zwrot / utylizacja"
INV = "Inwentaryzacja / nadzór"
INTERNAL = "Ruch wewnętrzny"
KIT = "Zestawy / dystrybucja / przeładunek"

# (kod, opis, grupa)
PROCESS_TYPES = [
    ("1010", "Umieszczenie w magazynie", PUT),
    ("1011", "Umieszcz. w mag. z procesem mag.", PUT),
    ("1012", "Umieszcz. w mag. (z rozdziałem)", PUT),
    ("1013", "Umieszcz. w mag. z DodUsłLog proc. mag.", PUT),
    ("10Z1", "Umieszczenie w magazynie drukarka 1", PUT),
    ("10Z2", "Umieszczenie w magazynie drukarka 2", PUT),
    ("10Z3", "Umieszczenie w magazynie drukarka 3", PUT),
    ("1100", "Umieszcz. w magaz. w zaopatrz. produkcji", PUT),
    ("1CR3", "Zwrot od klienta drukarka 3", REPOST),
    ("2010", "Wydanie z magazynu", ISSUE),
    ("2020", "Pobranie dwustopniowe", ISSUE),
    ("2100", "Wydanie z magazynu dla zaopatrz. prod.", ISSUE),
    ("2192", "Wydanie z magazynu ZP92 pierwsza dostawa", ISSUE),
    ("21DC", "Wydanie z magazynu KRAJ odbiór osobisty", ISSUE),
    ("21DE", "Wydanie z magazynu KRAJ sped. Zew.", ISSUE),
    ("21EC", "Wydanie z magazynu EXPORT odbiór osobist", ISSUE),
    ("21EE", "Wydanie z magazynu EXPORT sped. Zew.", ISSUE),
    ("21G1", "Wydanie z magazynu GEIS scenariusz 1", ISSUE),
    ("21G2", "Wydanie z magazynu GEIS scenariusz 2", ISSUE),
    ("21GL", "Wydanie z magazynu GLS", ISSUE),
    ("21JU", "Wydanie z magazynu JU", ISSUE),
    ("21XX", "Wydanie z magazynu", ISSUE),
    ("2B92", "Wyd.z mag. ZP92 pierwsza dost. z partią", ISSUE),
    ("2BDC", "Wyd. z mag. KRAJ odb. osobisty z partią", ISSUE),
    ("2BDE", "Wyd. z mag. KRAJ sped. Zew. Z partią", ISSUE),
    ("2BG1", "Wyd. z mag. GEIS scenariusz 1 z partią", ISSUE),
    ("2BG2", "Wyd. z mag. GEIS scenariusz 2 z partią", ISSUE),
    ("2BGL", "Wydanie z magazynu GLS z partią", ISSUE),
    ("2BJU", "Wydanie z magazynu JU z partią", ISSUE),
    ("2BXX", "Wydanie z magazynu z partią", ISSUE),
    ("2RTV", "Wydanie z magazynu zwrot", REPOST),
    ("3010", "Uzupełnienie lokalizacji", REPL),
    ("3011", "Uzupełnienie lokalizacji 0051", REPL),
    ("301D", "Bezpośrednie uzupełnienie", REPL),
    ("3020", "Optymalizacja magazynu", MOVE),
    ("3030", "Transfer", MOVE),
    ("3040", "Przepakowanie", MOVE),
    ("3050", "Transfer pobrania HU", MOVE),
    ("3060", "Umieszczenie HU w magazynie", PUT),
    ("3065", "Rozład.: Ruch HU", MOVE),
    ("3070", "Wydanie HU z magazynu", ISSUE),
    ("3071", "Wydanie HU z magazynu pick point", ISSUE),
    ("3100", "Częściowe uzupełnienie", REPL),
    ("37BG", "Wydanie HU z magazynu", ISSUE),
    ("4010", "Przeksiegowanie", REPOST),
    ("4011", "Przeksięgowanie z zad. Mag.", REPOST),
    ("4020", "Utylizacja", REPOST),
    ("4030", "Zwrot", REPOST),
    ("4100", "Przeksięgowanie dla zaopatrz. produkcji", REPOST),
    ("9010", "Umieszcz. w mag. (dod. HU dla rozład.)", PUT),
    ("9996", "TT zadanie magazynowe utylizacja", REPOST),
    ("9997", "NO zadanie magazynowe próbki", INV),
    ("9998", "Ruch ad hoc", INTERNAL),
    ("9999", "Nadzór magazynu", INV),
    ("FTCU", "Dystr. przepł. - um. w mag. zor. na odb.", KIT),
    ("FTPD", "Dystr. przepł. - um. w mag. zor. na pr.", KIT),
    ("KTRI", "Rozłożenie zestawu, umieszcz. w magaz.", KIT),
    ("KTRO", "Rozłożenie zestawu, wydanie z magazynu", KIT),
    ("KTSI", "Tworz. zestawu - umieszcz. w magazynie", KIT),
    ("KTSO", "Tworz. zestawu - wydanie z magazynu", KIT),
    ("MDCD", "Rozdział mat.: Przeładunek kompletacyjny", KIT),
    ("OFTC", "Dystr. przepł. - pobranie zor. na odb.", KIT),
    ("OFTP", "Dystr. przepł. - pobranie zor. na pr.", KIT),
    ("OMDX", "Rozdział mat.: Przeł. kompl. - pobranie", KIT),
    ("CLSP", "Naprzemienne umieszczenie zapasów w mag.", PUT),
    ("EPIC", "Wydanie z magazynu", ISSUE),
    ("INTL", "Wewnętrzny ruch materiałowy", INTERNAL),
    ("INVE", "Inwentaryzacja", INV),
    ("NOLM", "Wewn. ruch materiałowy bez nakładu pracy", INTERNAL),
    ("PICK", "Wydanie z magazynu", ISSUE),
    ("PTWY", "Umieszczenie w magazynie", PUT),
    ("REPL", "Uzupełnienie", REPL),
    ("SPIC", "Wydanie z magazynu", ISSUE),
    ("STCH", "Przeksięgowanie", REPOST),
]


def seed(apps, schema_editor):
    DictionaryEntry = apps.get_model("ui", "DictionaryEntry")
    for code, label, group in PROCESS_TYPES:
        DictionaryEntry.objects.update_or_create(
            category="process_type", code=code,
            defaults={"label": label, "group": group})


def unseed(apps, schema_editor):
    DictionaryEntry = apps.get_model("ui", "DictionaryEntry")
    DictionaryEntry.objects.filter(category="process_type").delete()


class Migration(migrations.Migration):
    dependencies = [("ui", "0147_dictionaryentry")]
    operations = [migrations.RunPython(seed, unseed)]
