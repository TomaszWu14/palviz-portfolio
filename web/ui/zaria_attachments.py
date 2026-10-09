"""Załączniki do czatu ZARIA — bez nowych zależności.

PDF i obrazy idą do Claude **natywnie** (bloki base64 image/document). Excel czytamy
openpyxl (już w repo), Word (.docx) rozpakowujemy stdlib (zip + XML `<w:t>`) — nie
potrzeba python-docx. Treść plików trafia **wyłącznie do zapytania do modelu**; do bazy
zapisujemy tylko marker z nazwami (RODO — rozmowy nie opuszczają organizacji).

Modele bez vision (Ollama/OpenAI-compat) dostają tylko wyekstrahowany tekst; obraz/PDF
są pomijane z ostrzeżeniem.
"""
import base64
import io
import re
import zipfile

MAX_BYTES = 10 * 1024 * 1024          # 10 MB / plik
MAX_XLSX_ROWS = 200                   # limit, żeby nie wysłać megaarkusza do modelu

# Dostawcy czytający obrazy/PDF natywnie (bloki base64). Reszta dostaje tylko tekst.
# Jedno źródło prawdy — dopisz klucz, gdy dojdzie kolejny model z vision.
VISION_PROVIDERS = {"anthropic"}

_IMG_MEDIA = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp",
}


def _ext(name):
    return (name.rsplit(".", 1)[-1].lower() if "." in name else "")


def _xlsx_text(data):
    """Zwraca arkusz jako tekst (tab-separated), przycięty do MAX_XLSX_ROWS wierszy."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        out.append(f"# Arkusz: {ws.title}")
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= MAX_XLSX_ROWS:
                out.append(f"… (obcięto, pokazano {MAX_XLSX_ROWS} wierszy)")
                break
            out.append("\t".join("" if c is None else str(c) for c in row))
    return "\n".join(out)


def _docx_text(data):
    """Tekst z .docx bez python-docx: zip → word/document.xml → treść węzłów <w:t>,
    akapity rozdzielone znakiem nowej linii."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = xml.replace("</w:p>", "\n")
    parts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml, re.S)
    txt = "".join(parts)
    # odkoduj podstawowe encje XML
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'")):
        txt = txt.replace(a, b)
    return txt.strip()


def build_payload(files, provider):
    """(media_blocks, extracted_text, names, warnings) z listy UploadedFile.

    media_blocks — bloki Anthropic (image/document) dla ostatniej wiadomości user
                   (puste dla dostawców bez vision);
    extracted_text — tekst z Excela/Worda do doklejenia do promptu;
    names — nazwy przyjętych plików (do markera zapisywanego w bazie);
    warnings — komunikaty o pominiętych plikach.
    """
    media_blocks, texts, names, warnings = [], [], [], []
    vision = provider in VISION_PROVIDERS
    for f in files:
        data = f.read()
        if not data:
            continue
        if len(data) > MAX_BYTES:
            warnings.append(f"{f.name}: przekracza 10 MB — pominięto")
            continue
        ext = _ext(f.name)
        if ext in _IMG_MEDIA:
            if vision:
                media_blocks.append({"type": "image", "source": {
                    "type": "base64", "media_type": _IMG_MEDIA[ext],
                    "data": base64.standard_b64encode(data).decode("ascii")}})
                names.append(f.name)
            else:
                warnings.append(f"{f.name}: obraz pominięty (wybrany model nie czyta obrazów)")
        elif ext == "pdf":
            if vision:
                media_blocks.append({"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf",
                    "data": base64.standard_b64encode(data).decode("ascii")}})
                names.append(f.name)
            else:
                warnings.append(f"{f.name}: PDF pominięty (wybrany model nie czyta PDF)")
        elif ext in ("xlsx", "xls"):
            try:
                texts.append(f"[Excel: {f.name}]\n{_xlsx_text(data)}")
                names.append(f.name)
            except Exception:
                warnings.append(f"{f.name}: nie udało się odczytać arkusza")
        elif ext == "docx":
            try:
                texts.append(f"[Word: {f.name}]\n{_docx_text(data)}")
                names.append(f.name)
            except Exception:
                warnings.append(f"{f.name}: nie udało się odczytać dokumentu")
        else:
            warnings.append(f"{f.name}: nieobsługiwany format")
    return media_blocks, "\n\n".join(texts), names, warnings


def last_message_content(text, media_blocks, extracted_text):
    """Zbuduj treść ostatniej wiadomości user do wysyłki do modelu.

    Anthropic (są media_blocks) → lista bloków [media…, text]. W innym razie zwykły string
    (prompt + wyekstrahowany tekst)."""
    full_text = text
    if extracted_text:
        full_text = f"{text}\n\n{extracted_text}" if text else extracted_text
    if media_blocks:
        return media_blocks + [{"type": "text", "text": full_text or "Załączony plik."}]
    return full_text
