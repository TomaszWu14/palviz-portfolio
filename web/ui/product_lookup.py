"""Jedno źródło prawdy: zeskanowany/wpisany ciąg → Product.

Operator na palecie może zeskanować DOWOLNY kod (REF, EAN sztuki, EAN kartonu,
EAN opak. zbiorczego). Wcześniej pełną kaskadę miał tylko ekran PHV; inne ekrany
(carton_opt, zgłoszenia, wyszukiwarka magazynu) reimplementowały 1–2 gałęzie, więc
ten sam kod rozwiązywał się na jednym ekranie, a 404 na drugim. Ten moduł trzyma
kaskadę w jednym miejscu — testowalnym bez HTTP.
"""
from django.db.models import Q

from .models import Product


def resolve_ref(q, *, active_only=False):
    """Rozwiąż `q` do Product wg kaskady skanera, albo None.

    Kolejność (jak w skanerze PHV — skan kodu kreskowego nie może kolidować z
    czysto numerycznym REF-em):
      1. EAN sztuki, gdy `q` wygląda na kod kreskowy (8/13 cyfr) — najpierw
      2. REF (kod produktu, case-insensitive)
      3. EAN sztuki (dla nie-8/13)
      4. EAN kartonu — join przez AKTYWNĄ instrukcję paletyzacji
      5. EAN opak. zbiorczego (OPZ / inner-pack), też przez carton.inner_pack

    Brak FK Carton/InnerPack→Product, stąd join przez `instructions`; `.distinct()`
    bo join zwielokrotnia. `active_only=True` ogranicza do Product.is_active — dla
    wyszukiwarki magazynu, która nie ma routować do wycofanych indeksów.
    """
    q = (q or "").strip()
    if not q:
        return None
    base = Product.objects.filter(is_active=True) if active_only else Product.objects
    looks_like_ean = q.isdigit() and len(q) in (8, 13)
    return ((base.filter(ean=q).first() if looks_like_ean else None)
            or base.filter(code__iexact=q).first()
            or base.filter(ean=q).first()
            or base.filter(instructions__is_active=True,
                           instructions__carton__ean=q).distinct().first()
            or base.filter(Q(instructions__inner_pack__ean=q)
                           | Q(instructions__carton__inner_pack__ean=q),
                           instructions__is_active=True).distinct().first())
