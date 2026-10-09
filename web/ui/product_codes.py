"""Rozwiązywanie kodów produktu do kanonicznego REF.

Ten sam produkt bywa zapisywany na wiele sposobów:
  • warianty mechaniczne — wielkość liter, separatory, sufiksy wersji/partii
    („mdom10001_v1", „mdom10001_b1" → „MDOM10001"), prefiksy dostawcy
    („nieat-dmo", „acme-dmo" → „DMO"). Te ROZWIĄZUJE reguła `normalize_code`.
  • realne zmiany nazwy — „DMO-M-100" → „DMOM10001" (różne cyfry). Tych nie da
    się wyliczyć; trzymamy je w tabeli `ProductAlias`.

`resolve_product_code(raw)` łączy oba: dokładny kod → alias → znormalizowany kod
→ znormalizowany alias. Zwraca `Product` albo `None`.

ponytail: porównujemy znormalizowane WEJŚCIE z kodem produktu „jak jest" (zakładamy,
że kanoniczny kod produktu to już czysta forma, np. MDOM10001). Jeśli same kody
produktów w bazie bywają brudne (z separatorami/prefiksem), dołożyć znormalizowaną
kolumnę na Product i po niej szukać — na razie niepotrzebne.
"""
import re

# Prefiksy dostawcy obcinane z przodu kodu (rozszerzalne). Dopasowanie tylko gdy po
# prefiksie jest separator, żeby nie zjeść przedrostka prawdziwego kodu.
VENDOR_PREFIXES = ("NIEAT", "ACME")

_PREFIX_RE = re.compile(r"^(?:" + "|".join(VENDOR_PREFIXES) + r")[-_/.\s]+")
# Sufiks wersji/partii: separator + V lub B + cyfry na końcu (np. _V1, -B2, /v10).
_SUFFIX_RE = re.compile(r"[-_/.\s]+[VB]\d+$", re.IGNORECASE)
_SEP_RE = re.compile(r"[-_/.\s]+")


def normalize_code(raw):
    """Kanoniczna forma kodu: wielkość liter, prefiks dostawcy, sufiks wersji/partii,
    separatory. „nieat-DMO" → „DMO"; „mdom10001_v1" → „MDOM10001"; „DMO-M" → „DMOM"."""
    s = (raw or "").strip().upper()
    if not s:
        return ""
    s = _PREFIX_RE.sub("", s)
    s = _SUFFIX_RE.sub("", s)
    s = _SEP_RE.sub("", s)
    return s


def resolve_product_code(raw):
    """Kanoniczny `Product` dla dowolnego zapisu kodu, albo `None`. Kolejność od
    najtańszego/najpewniejszego: dokładny kod → alias → znormalizowany kod → alias
    po normalizacji. Pierwsze trafienie wygrywa."""
    from .models import Product, ProductAlias
    if not raw:
        return None
    p = Product.objects.filter(code__iexact=raw).first()
    if p:
        return p
    a = (ProductAlias.objects.filter(alias_code__iexact=raw)
         .select_related("product").first())
    if a:
        return a.product
    norm = normalize_code(raw)
    if not norm or norm.upper() == raw.upper():
        return None                     # normalizacja nic nie zmieniła → już próbowaliśmy
    p = Product.objects.filter(code__iexact=norm).first()
    if p:
        return p
    a = (ProductAlias.objects.filter(alias_code__iexact=norm)
         .select_related("product").first())
    return a.product if a else None
