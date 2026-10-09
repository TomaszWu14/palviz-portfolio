"""Rdzeń modułu „Wymiary producenta": czyste porównanie wymiarów kartonu
(producent vs nasza master data), parsowanie komórek i etykieta dostawcy.
Bez Django ORM w części porównawczej — testowane bezpośrednio przez interfejs."""
import re


def parse_dim(cell):
    """Komórka wymiaru → float w cm albo None (tekst, '-', '/', puste)."""
    if cell is None:
        return None
    if isinstance(cell, (int, float)):
        return float(cell)
    s = str(cell).strip().replace(",", ".")
    m = re.match(r"^\d+(\.\d+)?$", s)
    return float(s) if m else None


def compare_dims(producer_lwh, ours_lwh, *, min_cm=1.0, pct=0.05):
    """(verdict, max_delta_cm). Sortuje oba tryplety malejąco i porównuje pozycyjnie
    (który bok to 'długość' jest umowne). Tolerancja per oś = max(min_cm, pct*nasz).
    Brak którejkolwiek strony → 'no_data'."""
    if not producer_lwh or not ours_lwh:
        return "no_data", None
    if any(v is None for v in producer_lwh) or any(v is None for v in ours_lwh):
        return "no_data", None
    p = sorted((float(x) for x in producer_lwh), reverse=True)
    o = sorted((float(x) for x in ours_lwh), reverse=True)
    max_delta = 0.0
    verdict = "ok"
    for pv, ov in zip(p, o):
        delta = abs(pv - ov)
        max_delta = max(max_delta, delta)
        if delta > max(min_cm, pct * ov):
            verdict = "mismatch"
    return verdict, round(max_delta, 2)


def ours_dims(product):
    """Nasze wymiary kartonu (L,W,H cm) dla produktu: instrukcja paletyzacji, fallback
    MaterialReference (mirror SAP MARM). None gdy brak. Zwraca surowe osie — porządek
    nieistotny (compare_dims i tak sortuje)."""
    if product is None:
        return None
    instr = product.latest_instruction()
    if instr and instr.carton_l and instr.carton_w and instr.carton_h:
        return (float(instr.carton_l), float(instr.carton_w), float(instr.carton_h))
    from ui.models import MaterialReference
    ref = MaterialReference.objects.filter(code__iexact=product.code).first()
    if ref and ref.length_cm and ref.width_cm and ref.height_cm:
        return (ref.length_cm, ref.width_cm, ref.height_cm)
    return None


def supplier_from_filename(name):
    """'INTCO GLOVES packaging size.xlsx' → 'INTCO GLOVES';
    'packaging size - BAIHE.xlsx' → 'BAIHE'."""
    base = re.sub(r"\.(xlsx|xls|csv)$", "", name or "", flags=re.I)
    base = re.sub(r"packaging size", "", base, flags=re.I)
    base = base.strip(" -–—_")
    return base or "—"
