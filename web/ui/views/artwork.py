"""Upload/rozmieszczenie grafik opakowania dla poziomów SZTUKA (Product) i OPZ
(InnerPack) — te same 4 operacje co carton_artwork_* (list/upload/update/delete),
sfaktoryzowane do generycznych helperów keyed by (owner, artwork_model). Renderer 3D
nakłada je przez ten sam kontrakt `as_dict()`."""
from .core import (
    JsonResponse, _ART_MAX_BYTES, _ART_CONTENT_TYPES, _ART_FACES, _ART_KINDS,
    make_artwork_derivatives, json, _md_role, get_object_or_404, require_POST
)
from django.db.models import Max

from ..models import Product, InnerPack, ProductArtwork, InnerPackArtwork, Carton


def _art_list(owner):
    return JsonResponse({"ok": True, "artworks": [a.as_dict() for a in owner.artworks.all()]})


def _art_upload(request, owner, art_model, owner_field):
    f = request.FILES.get("image")
    if not f:
        return JsonResponse({"ok": False, "error": "Brak pliku."}, status=400)
    if f.size > _ART_MAX_BYTES:
        return JsonResponse({"ok": False, "error": "Plik za duży (max 8 MB)."}, status=400)
    if f.content_type not in _ART_CONTENT_TYPES:
        return JsonResponse({"ok": False, "error": "Dozwolone tylko PNG / JPG."}, status=400)
    face = request.POST.get("face", "front")
    kind = request.POST.get("kind", "label")
    if face not in _ART_FACES or kind not in _ART_KINDS:
        return JsonResponse({"ok": False, "error": "Błędna ściana lub typ."}, status=400)
    if kind == "print":                     # jedna ściana = jeden nadruk (podmień)
        for old in owner.artworks.filter(face=face, kind="print"):
            _art_delete_files(old)
            old.delete()
        art = art_model(face=face, kind="print", x_pct=0, y_pct=0, w_pct=100, h_pct=100, z=0,
                        **{owner_field: owner})
    else:
        next_z = (owner.artworks.filter(face=face, kind="label")
                  .aggregate(m=Max("z")).get("m") or 0) + 1
        art = art_model(face=face, kind="label", x_pct=8, y_pct=8, w_pct=30, h_pct=20,
                        z=next_z, name=(f.name or "")[:120], **{owner_field: owner})
    art.image = f
    # Derywaty (mniejszy display + miniatura) — front nie pobiera oryginału pod kafelek.
    display, thumb, art.width_px, art.height_px = make_artwork_derivatives(f)
    art.save()
    stem = (art.image.name.rsplit("/", 1)[-1].rsplit(".", 1)[0] or "art")[:80]
    if display:
        art.image_display.save(f"{stem}_d.webp", display, save=False)
    if thumb:
        art.image_thumb.save(f"{stem}_t.webp", thumb, save=False)
    if display or thumb:
        art.save(update_fields=["image_display", "image_thumb"])
    return JsonResponse({"ok": True, "artwork": art.as_dict()})


def _art_delete_files(art):
    """Skasuj ze storage oryginał ORAZ oba derywaty (inaczej zostają sierotami)."""
    for field in ("image", "image_display", "image_thumb"):
        f = getattr(art, field, None)
        if f:
            f.delete(save=False)


def _art_update(request, art):
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return JsonResponse({"ok": False, "error": "Błędny JSON."}, status=400)

    def _clamp(v, lo, hi, default):
        try:
            return max(lo, min(hi, float(v)))
        except (ValueError, TypeError):
            return default
    if "x" in data:   art.x_pct = _clamp(data["x"], -50, 150, art.x_pct)
    if "y" in data:   art.y_pct = _clamp(data["y"], -50, 150, art.y_pct)
    if "w" in data:   art.w_pct = _clamp(data["w"], 1, 200, art.w_pct)
    if "h" in data:   art.h_pct = _clamp(data["h"], 1, 200, art.h_pct)
    if "rot" in data: art.rotation_deg = _clamp(data["rot"], -180, 180, art.rotation_deg)
    if "z" in data:
        try: art.z = int(data["z"])
        except (ValueError, TypeError): pass
    if data.get("face") in _ART_FACES and art.kind == "label":
        art.face = data["face"]
    art.save()
    return JsonResponse({"ok": True, "artwork": art.as_dict()})


def _art_delete(art):
    _art_delete_files(art)                  # usuń też pliki ze storage (oryginał + derywaty)
    art.delete()
    return JsonResponse({"ok": True})


# ── Sztuka (Product) ──────────────────────────────────────────────────────────
@_md_role
def product_artwork_list(request, pk: int):
    return _art_list(get_object_or_404(Product, pk=pk))

@require_POST
@_md_role
def product_artwork_upload(request, pk: int):
    return _art_upload(request, get_object_or_404(Product, pk=pk), ProductArtwork, "product")

@require_POST
@_md_role
def product_artwork_update(request, art_id: int):
    return _art_update(request, get_object_or_404(ProductArtwork, pk=art_id))

@require_POST
@_md_role
def product_artwork_delete(request, art_id: int):
    return _art_delete(get_object_or_404(ProductArtwork, pk=art_id))


# ── OPZ (InnerPack) ───────────────────────────────────────────────────────────
@_md_role
def inner_pack_artwork_list(request, pk: int):
    return _art_list(get_object_or_404(InnerPack, pk=pk))

@require_POST
@_md_role
def inner_pack_artwork_upload(request, pk: int):
    return _art_upload(request, get_object_or_404(InnerPack, pk=pk), InnerPackArtwork, "inner_pack")

@require_POST
@_md_role
def inner_pack_artwork_update(request, art_id: int):
    return _art_update(request, get_object_or_404(InnerPackArtwork, pk=art_id))

@require_POST
@_md_role
def inner_pack_artwork_delete(request, art_id: int):
    return _art_delete(get_object_or_404(InnerPackArtwork, pk=art_id))


def _glb_upload(request, owner):
    """Zapisz plik .glb w slocie glb_model właściciela (Carton albo Product) — AJAX
    z macierzy grafik; walidacja rozszerzenia + limit jak przy grafikach (8 MB)."""
    f = request.FILES.get("glb")
    if not f:
        return JsonResponse({"ok": False, "error": "Brak pliku."}, status=400)
    if f.size > _ART_MAX_BYTES:
        return JsonResponse({"ok": False, "error": "Plik za duży (max 8 MB)."}, status=400)
    if not (f.name or "").lower().endswith(".glb"):
        return JsonResponse({"ok": False, "error": "Dozwolony tylko plik .glb."}, status=400)
    owner.glb_model = f
    owner.save(update_fields=["glb_model"])
    return JsonResponse({"ok": True})


@require_POST
@_md_role
def carton_glb_upload(request, pk: int):
    return _glb_upload(request, get_object_or_404(Carton, pk=pk))


@require_POST
@_md_role
def product_glb_upload(request, pk: int):
    return _glb_upload(request, get_object_or_404(Product, pk=pk))


@require_POST
@_md_role
def product_ju_upload(request, pk: int):
    """Media poziomu SZTUKA (JU): jeden endpoint, format po rozszerzeniu —
    .glb → ju_glb_model, .png/.jpg → ju_image (nadruk na bryłę)."""
    product = get_object_or_404(Product, pk=pk)
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"ok": False, "error": "Brak pliku."}, status=400)
    if f.size > _ART_MAX_BYTES:
        return JsonResponse({"ok": False, "error": "Plik za duży (max 8 MB)."}, status=400)
    name = (f.name or "").lower()
    if name.endswith(".glb"):
        product.ju_glb_model = f
        product.save(update_fields=["ju_glb_model"])
    elif name.endswith((".png", ".jpg", ".jpeg")):
        product.ju_image = f
        product.save(update_fields=["ju_image"])
    else:
        return JsonResponse({"ok": False, "error": "Dozwolone: .glb / PNG / JPG."}, status=400)
    return JsonResponse({"ok": True})


__all__ = [
    "product_artwork_list", "product_artwork_upload",
    "product_artwork_update", "product_artwork_delete",
    "inner_pack_artwork_list", "inner_pack_artwork_upload",
    "inner_pack_artwork_update", "inner_pack_artwork_delete",
    "carton_glb_upload", "product_glb_upload", "product_ju_upload",
]
