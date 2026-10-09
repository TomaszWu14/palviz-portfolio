"""Seeduje katalog modeli ZARIA (haiku / sonnet / opus), dostęp ról i model
domyślny. Idempotentne — można uruchamiać wielokrotnie; ceny istniejących modeli
NIE są nadpisywane bez --force (żeby nie zdeptać ręcznych korekt admina).

    python manage.py zaria_seed [--rate 4.0] [--force]

Ceny modeli u dostawcy są w USD za 1M tokenów; zapisujemy je w PLN za 1k tokenów
po kursie --rate. To wartości STARTOWE — zweryfikuj/uaktualnij w panelu admina
(/admin-panel/zaria/models/), zwłaszcza kurs i ewentualne ceny promocyjne.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand

from ui.models import ZariaModel, ZariaModelRoleAccess, ZariaConfig
from ui.roles import ALL_GROUPS, GROUP_ADMIN, GROUP_MASTER_DATA

# key = identyfikator modelu u dostawcy (Anthropic). Ceny USD za 1M tokenów
# (wej / wyj) wg cennika Anthropic (Sonnet 5: 2/10 USD — cena standardowa).
# Pełna oferta Claude API (stan 2026-08): Haiku 4.5, Sonnet 5, Opus 4.8, Fable 5.
MODELS = [
    ("claude-haiku-4-5", "Claude Haiku 4.5", Decimal("1"),  Decimal("5"),  10),
    ("claude-sonnet-5",  "Claude Sonnet 5",  Decimal("2"),  Decimal("10"), 20),
    ("claude-opus-4-8",  "Claude Opus 4.8",  Decimal("5"),  Decimal("25"), 30),
    ("claude-fable-5",   "Claude Fable 5",   Decimal("10"), Decimal("50"), 40),
]
# Model lokalny (Ollama) — pozycja A trybu porównania „lokalny vs chmura". Ceny 0
# (samohostowany). Gdy ZARIA_OLLAMA_BASE_URL nie jest ustawione, picker pokaże go
# jako „Niedostępny" (health-check) — seedujemy mimo to, żeby katalog był kompletny.
OLLAMA_MODEL = ("llama3.2:3b", "Llama 3.2 3B (lokalnie)", 50)
DEFAULT_MODEL_KEY = "claude-sonnet-5"

# Decyzja (audyt AI-002): drogie modele (Opus 4.8, Fable 5) tylko dla Administratorów
# i Master Data; pozostałe role — Haiku 4.5, Sonnet 5 i lokalny Ollama. Wyjątki per
# użytkownik nadaje się w panelu admina (nadpisania ZariaUserModelAccess).
ALL_KEYS = [m[0] for m in MODELS] + [OLLAMA_MODEL[0]]
PREMIUM_KEYS = {"claude-opus-4-8", "claude-fable-5"}
PREMIUM_GROUPS = {GROUP_ADMIN, GROUP_MASTER_DATA}
ROLE_ACCESS = {group: ALL_KEYS if group in PREMIUM_GROUPS
               else [k for k in ALL_KEYS if k not in PREMIUM_KEYS]
               for group in ALL_GROUPS}

# Przestarzałe klucze z wcześniejszych seedów/ręcznych wpisów → scalamy do
# aktualnego katalogu (rename zachowuje FK rozmów; gdy cel już istnieje —
# dezaktywacja, żeby nie było dwóch wierszy tego samego modelu w pickerze).
LEGACY_KEYS = {
    "llama3.1": "llama3.2:3b",   # VPS 8 GB RAM: 8B nie miesci sie obok PALVIZ/Redis
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
    "claude-sonnet-4-5": "claude-sonnet-5",
    "claude-opus-4-5": "claude-opus-4-8",
}


class Command(BaseCommand):
    help = "Seeduje modele ZARIA (haiku/sonnet/opus/fable), dostęp ról i model domyślny."

    def add_arguments(self, parser):
        parser.add_argument("--rate", type=Decimal, default=Decimal("4.0"),
                            help="Kurs USD→PLN dla cen startowych (domyślnie 4.0).")
        parser.add_argument("--force", action="store_true",
                            help="Nadpisz ceny i model domyślny nawet dla istniejących wpisów "
                                 "oraz odbierz rolom granty spoza ROLE_ACCESS.")

    def handle(self, *args, **opts):
        rate, force = opts["rate"], opts["force"]
        # Scal przestarzałe klucze z aktualnym katalogiem, zanim upsert utworzyłby
        # duplikaty (patrz LEGACY_KEYS).
        for old_key, new_key in LEGACY_KEYS.items():
            legacy = ZariaModel.objects.filter(key=old_key).first()
            if legacy is None:
                continue
            if ZariaModel.objects.filter(key=new_key).exists():
                legacy.is_active = False
                legacy.save(update_fields=["is_active"])
                self.stdout.write(self.style.WARNING(
                    f"  ↻ Dezaktywowano przestarzały {old_key} ({new_key} już istnieje)"))
            else:
                _names = {m[0]: m[1] for m in MODELS} | {OLLAMA_MODEL[0]: OLLAMA_MODEL[1]}
                legacy.key = new_key
                legacy.display_name = _names[new_key]
                legacy.save(update_fields=["key", "display_name"])
                self.stdout.write(self.style.WARNING(
                    f"  ↻ Zmieniono klucz {old_key} → {new_key}"))
        by_key = {}
        for key, name, in_usd, out_usd, order in MODELS:
            in_pln = (in_usd / 1000) * rate    # USD/1M → PLN/1k
            out_pln = (out_usd / 1000) * rate
            obj, created = ZariaModel.objects.get_or_create(
                key=key, defaults=dict(
                    display_name=name, provider="anthropic", is_active=True,
                    price_input_per_1k=in_pln, price_output_per_1k=out_pln, sort_order=order))
            by_key[key] = obj
            if created:
                self.stdout.write(self.style.SUCCESS(
                    f"  ✓ Utworzono model {key}  ({in_pln}/{out_pln} PLN za 1k wej/wyj)"))
            elif force:
                obj.display_name, obj.provider, obj.sort_order = name, "anthropic", order
                obj.price_input_per_1k, obj.price_output_per_1k = in_pln, out_pln
                obj.save()
                self.stdout.write(self.style.WARNING(f"  ↻ Zaktualizowano ceny {key} (--force)"))
            else:
                self.stdout.write(f"  · Model już istnieje: {key} (ceny bez zmian)")

        # Model lokalny (Ollama) — ceny 0, provider=ollama; idempotentnie jak wyżej.
        okey, oname, oorder = OLLAMA_MODEL
        obj, created = ZariaModel.objects.get_or_create(
            key=okey, defaults=dict(
                display_name=oname, provider="ollama", is_active=True,
                price_input_per_1k=Decimal("0"), price_output_per_1k=Decimal("0"),
                sort_order=oorder))
        by_key[okey] = obj
        if created:
            self.stdout.write(self.style.SUCCESS(f"  ✓ Utworzono model lokalny {okey} (Ollama)"))
        else:
            self.stdout.write(f"  · Model lokalny już istnieje: {okey}")

        # Dostęp ról (idempotentnie).
        n_access = 0
        for role, keys in ROLE_ACCESS.items():
            for key in keys:
                _, made = ZariaModelRoleAccess.objects.get_or_create(
                    model=by_key[key], group_name=role)
                n_access += int(made)
        n_revoked = 0
        if force:
            # --force: siatka z seeda jest źródłem prawdy dla modeli katalogu × 9 ról
            # (odbiera np. Fable/Opus rolom wąskim). Nie rusza modeli spoza katalogu,
            # innych nazw grup ani nadpisań per użytkownik.
            for role, keys in ROLE_ACCESS.items():
                n_revoked += ZariaModelRoleAccess.objects.filter(
                    group_name=role, model__key__in=ALL_KEYS).exclude(
                    model__key__in=keys).delete()[0]
        self.stdout.write(self.style.SUCCESS(
            f"\n  ✓ Dostęp ról: {n_access} nowych uprawnień, {n_revoked} odebranych (--force)"))

        # Model domyślny.
        config = ZariaConfig.load()
        if config.default_model is None or force:
            config.default_model = by_key[DEFAULT_MODEL_KEY]
            config.save(update_fields=["default_model", "updated_at"])
            self.stdout.write(self.style.SUCCESS(f"  ✓ Model domyślny: {DEFAULT_MODEL_KEY}"))
        else:
            self.stdout.write(f"  · Model domyślny bez zmian: {config.default_model.key}")

        self.stdout.write(self.style.SUCCESS(
            f"\nGotowe (kurs {rate} PLN/USD). Zweryfikuj ceny i ustaw ANTHROPIC_API_KEY, "
            f"a resztę limitów w /admin-panel/zaria/."))
