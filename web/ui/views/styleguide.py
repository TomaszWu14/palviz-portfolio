"""Żywy podgląd systemu designu GROOVE (/ui/) — wydzielony z misc.py."""
from .core import _admin_only, render


@_admin_only
def ui_styleguide(request):
    """Żywy podgląd systemu designu GROOVE (Etap 2 audytu UI): każdy komponent we
    wszystkich stanach. Punkt odniesienia przy budowie kolejnych ekranów. Narzędzie
    deweloperskie — tylko Administratorzy (decyzja Q-43, 2026-09-28)."""
    from django import forms

    class _DemoForm(forms.Form):  # tylko do podglądu komponentu pola (stany: ok / błąd / pomoc)
        name = forms.CharField(label="Nazwa produktu", initial="Karton 400×300×220")
        ean = forms.CharField(label="Kod EAN", help_text="13 cyfr, bez spacji.")
        q = forms.CharField(label="Szukaj", required=False)

    demo = _DemoForm({"name": "Karton 400×300×220", "ean": "12ab", "q": ""})
    demo.is_valid()
    demo.add_error("ean", "Kod musi mieć 13 cyfr.")
    icons = ("package truck warehouse scan-barcode clipboard-list map-pin triangle-alert "
             "circle-check trash-2 pencil download upload search filter printer settings").split()
    return render(request, "ui/styleguide.html", {"demo": demo, "sg_icons": icons, "sg_rows": range(1, 7)})


__all__ = ['ui_styleguide']
