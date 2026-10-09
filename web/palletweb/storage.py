"""Magazyn statyk: WhiteNoise (kompresja + hash w nazwie) bez przepisywania map źródłowych.

Vendorowane biblioteki (three-mesh-bvh, …) mają `//# sourceMappingURL=…` do plików .map,
których nie dostarczamy — domyślny post-processing Django przerywa wtedy collectstatic.
Mapy nie są potrzebne na produkcji, więc pomijamy tylko ten wzorzec; url()/@import w CSS
nadal dostają zahashowane nazwy."""
from django.contrib.staticfiles.storage import HashedFilesMixin
from whitenoise.storage import CompressedManifestStaticFilesStorage


def _without_sourcemaps(patterns):
    out = []
    for glob, rules in patterns:
        kept = tuple(r for r in rules if "sourceMappingURL" not in (r[0] if isinstance(r, tuple) else r))
        if kept:
            out.append((glob, kept))
    return tuple(out)


class GrooveStaticStorage(CompressedManifestStaticFilesStorage):
    patterns = _without_sourcemaps(HashedFilesMixin.patterns)
