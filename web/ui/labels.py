"""Barcode / QR label generation (python-barcode + qrcode).

Produces PNG bytes for logistics labels — SSCC/EAN/Code128 barcodes and QR codes for
handling units and locations. Pure-Python (Pillow under the hood, already a dependency).
"""
import io


def barcode_png(value: str, kind: str = "code128", with_text: bool = True) -> bytes:
    """Render a 1D barcode (code128 / ean13 / gs1 / code39) to PNG bytes. ``with_text``
    controls the human-readable number printed under the bars."""
    import barcode
    from barcode.writer import ImageWriter

    name = {"code128": "code128", "ean13": "ean13", "ean": "ean13",
            "gs1": "gs1_128", "gs1_128": "gs1_128", "code39": "code39"}.get(kind.lower(), "code128")
    cls = barcode.get_barcode_class(name)
    buf = io.BytesIO()
    cls(value, writer=ImageWriter()).write(buf, options={"write_text": with_text})
    return buf.getvalue()


def qr_png(value: str, box_size: int = 8, border: int = 2) -> bytes:
    """Render a QR code to PNG bytes."""
    import qrcode

    img = qrcode.make(value, box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Zebra ZPL label generation (HU print module) ────────────────────────────────
# ZPL is emitted as plain text and rendered by the printer itself (native ^BC/^B3
# barcode commands — no image rasterization), so a 10 000-label batch is a small text
# stream the printer prints at full speed. Default label: 100×150 mm at 203 dpi.

def _mm_to_dots(mm: float, dpi: int) -> int:
    return int(round(mm / 25.4 * dpi))


def zpl_label(number_str: str, name_text: str = "", symbology: str = "code128",
              width_mm: float = 150, height_mm: float = 100, dpi: int = 203) -> str:
    """ZPL for one **landscape** HU label (default 150×100 mm @ 203 dpi): the linear
    barcode(number) with the number under it and the big project name/number, plus a small
    **QR code in each of the four corners** encoding the same number — a redundant scan
    target if the linear barcode gets damaged. The central content is kept clear of the
    corner QR columns. ``symbology`` is 'code128' or 'code39'."""
    pw = _mm_to_dots(width_mm, dpi)          # print width (dots) — the long, horizontal side
    ll = _mm_to_dots(height_mm, dpi)         # label length (dots) — the short side
    margin = _mm_to_dots(4, dpi)
    qr_box = _mm_to_dots(13, dpi)            # reserved corner box for the fallback QR
    bar_h = _mm_to_dots(17, dpi)             # barcode height

    # Interpretation line OFF (the "N" after the height) — the number is printed once, big,
    # below the barcode; no small duplicate under the bars. ^BY3 = wider bars.
    if symbology.lower() == "code39":
        barcode = f"^BY3^B3N,N,{bar_h},N,N^FD{number_str}^FS"
    else:
        barcode = f"^BY3^BCN,{bar_h},N,N,N^FD{number_str}^FS"

    parts = [
        "^XA",
        f"^PW{pw}", f"^LL{ll}",
        "^CI28",                              # UTF-8 (Polish characters in names)
    ]
    # A fallback QR in BOTH top corners — readable/scannable from either side of the pallet.
    parts.append(f"^FO{margin},{margin}^BQN,2,4^FDHA,{number_str}^FS")
    parts.append(f"^FO{pw - margin - qr_box},{margin}^BQN,2,4^FDHA,{number_str}^FS")

    # Barcode near the top, between the two corner QRs.
    inner_x = margin + qr_box + _mm_to_dots(5, dpi)
    inner_fb = pw - 2 * inner_x
    y_bar = _mm_to_dots(5, dpi)
    parts.append(f"^FO{inner_x},{y_bar}{barcode}")

    # A single, centred number; for named projects (not POLSKA/EXPORT) the name + number
    # are printed white on a black band. The name/number sit BELOW the top-corner QRs, so
    # they use nearly the full label width (no QR beside them) — room for the big font.
    y = y_bar + bar_h + _mm_to_dots(9, dpi)
    txt_x = margin + _mm_to_dots(3, dpi)
    txt_fb = pw - 2 * txt_x
    if name_text:
        band_y = y - _mm_to_dots(3, dpi)
        band_h = ll - margin - band_y
        parts.append(f"^FO{margin},{band_y}^GB{pw - 2 * margin},{band_h},{band_h},B^FS")
        parts.append(f"^FO{txt_x},{y}^FR^A0N,92,92^FB{txt_fb},1,0,C^FD{name_text}^FS")
        parts.append(f"^FO{txt_x},{y + _mm_to_dots(13, dpi)}^FR^A0N,159,159^FB{txt_fb},1,0,C^FD{number_str}^FS")
    else:
        parts.append(f"^FO{txt_x},{y}^A0N,159,159^FB{txt_fb},1,0,C^FD{number_str}^FS")
    parts.append("^XZ\n")
    return "".join(parts)


# Nagłówki etykiety logistycznej per język klienta (mini-wywiad 2026-07-30).
_LOGI_T = {
    "pl": {"title": "ETYKIETA LOGISTYCZNA", "recipient": "Odbiorca", "contents": "Zawartość",
           "lot": "LOT", "exp": "Ważność", "notes": "Wymagania", "pos": "poz.", "pallet": "PALETA"},
    "en": {"title": "LOGISTICS LABEL", "recipient": "Consignee", "contents": "Contents",
           "lot": "LOT", "exp": "Expiry", "notes": "Requirements", "pos": "items", "pallet": "PALLET"},
    "de": {"title": "LOGISTIKETIKETT", "recipient": "Empfänger", "contents": "Inhalt",
           "lot": "LOT", "exp": "MHD", "notes": "Anforderungen", "pos": "Pos.", "pallet": "PALETTE"},
}


def zpl_logistics_label(hu, customer=None, width_mm: float = 150, height_mm: float = 100,
                        dpi: int = 203) -> str:
    """Etykieta logistyczna 150×100 (landscape, Zebra 203 dpi): zawartość palety
    (indeks, nazwa, ilości we wszystkich jednostkach, LOT/EXP), dane odbiorcy z
    WYSYŁKI, notatki wymagań i stały tekst klienta. Celowo BEZ kodów kreskowych
    (decyzja z mini-wywiadu). Język nagłówków wg klienta (pl/en/de)."""
    t = _LOGI_T.get((customer.label_language if customer else "pl") or "pl", _LOGI_T["pl"])
    pw = _mm_to_dots(width_mm, dpi)
    ll = _mm_to_dots(height_mm, dpi)
    m = _mm_to_dots(4, dpi)
    fb = pw - 2 * m
    sh = hu.shipment
    dest = " ".join(x for x in (sh.destination_postal, sh.destination_city,
                                sh.destination_country) if x)

    parts = ["^XA", f"^PW{pw}", f"^LL{ll}", "^CI28"]
    y = m
    parts.append(f"^FO{m},{y}^A0N,34,34^FB{fb},1,0,L^FD{t['title']}  ·  HU {hu.ref}^FS")
    y += _mm_to_dots(6, dpi)
    parts.append(f"^FO{m},{y}^GB{fb},2,2,B^FS")
    y += _mm_to_dots(2, dpi)
    # „Paleta X z Y" — nr tej palety spośród wszystkich w dostawie (kluczowe dla eksportu,
    # gdzie liczy się kompletność ładunku). Y = liczba HU w wysyłce.
    total_pal = sh.handling_units.count() if sh else 0
    parts.append(f"^FO{m},{y}^A0N,30,30^FB{fb},1,0,L^FD{t['pallet']} {hu.seq} / {total_pal or 1}^FS")
    y += _mm_to_dots(5, dpi)
    parts.append(f"^FO{m},{y}^A0N,28,28^FB{fb},1,0,L^FD{t['recipient']}: {sh.recipient_name or '-'}^FS")
    y += _mm_to_dots(4.5, dpi)
    if dest:
        parts.append(f"^FO{m},{y}^A0N,26,26^FB{fb},1,0,L^FD{dest}^FS")
        y += _mm_to_dots(4.5, dpi)
    # Wydruk eksportowy (Fala 3): nazwa klienta + kraj oraz numer WZ z dostawy.
    country = sh.destination_country or (customer.country if customer else "")
    if customer:
        parts.append(f"^FO{m},{y}^A0N,26,26^FB{fb},1,0,L^FD{customer.name}"
                     + (f" · {country}" if country else "") + "^FS")
        y += _mm_to_dots(4.5, dpi)
    if sh.wz_number:
        parts.append(f"^FO{m},{y}^A0N,28,28^FB{fb},1,0,L^FDWZ: {sh.wz_number}^FS")
        y += _mm_to_dots(4.5, dpi)
    parts.append(f"^FO{m},{y}^GB{fb},2,2,B^FS")
    y += _mm_to_dots(2.5, dpi)

    items = list(hu.items.all())
    show_lot = customer.label_show_lot_exp if customer else True
    parts.append(f"^FO{m},{y}^A0N,26,26^FB{fb},1,0,L^FD{t['contents']} ({len(items)} {t['pos']}):^FS")
    y += _mm_to_dots(4.5, dpi)
    # ponytail: max ~8 wierszy pozycji mieści się na 100 mm — nadmiar zbiorczo w ostatniej linii.
    max_rows = 8
    for it in items[:max_rows]:
        qty = f"{(it.base_qty or 0):g} {it.base_unit or 'OP'}"
        if it.alt_qty:
            qty += f" / {it.alt_qty:g} {it.alt_unit or 'KAR'}"
        line = f"{it.ref_code}  {(it.description or '')[:26]}  {qty}"
        if show_lot and (it.lot or it.expiry):
            line += f"  {t['lot']}:{it.lot or '-'}"
            if it.expiry:
                line += f" {t['exp']}:{it.expiry:%d.%m.%Y}"
        parts.append(f"^FO{m},{y}^A0N,22,22^FB{fb},1,0,L^FD{line}^FS")
        y += _mm_to_dots(3.6, dpi)
    if len(items) > max_rows:
        parts.append(f"^FO{m},{y}^A0N,22,22^FB{fb},1,0,L^FD… +{len(items) - max_rows}^FS")
        y += _mm_to_dots(3.6, dpi)

    footer = []
    if customer and customer.label_show_requirements and customer.requirements_notes:
        footer.append(f"{t['notes']}: {customer.requirements_notes[:120]}")
    if customer and customer.label_extra_text:
        footer.append(customer.label_extra_text)
    if footer:
        fy = ll - m - _mm_to_dots(4.5, dpi) * len(footer)
        parts.append(f"^FO{m},{fy - _mm_to_dots(2, dpi)}^GB{fb},2,2,B^FS")
        for i, line in enumerate(footer):
            parts.append(f"^FO{m},{fy + i * _mm_to_dots(4.5, dpi)}^A0N,24,24^FB{fb},1,0,L^FD{line}^FS")
    parts.append("^XZ\n")
    return "".join(parts)


def iter_zpl_labels(project, numbers, symbology="code128",
                    width_mm=150, height_mm=100, dpi=203):
    """Yield the ZPL string for each number in ``numbers`` (a range/iterable of ints),
    formatted through the project. Generator → the print view can stream 10 000+ labels
    without building the whole payload in memory."""
    name = project.label_text or ""
    for n in numbers:
        yield zpl_label(project.format_number(n), name, symbology,
                        width_mm, height_mm, dpi)
