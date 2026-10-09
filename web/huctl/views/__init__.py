# Widoki Kontroli HU — importowane wprost w ui.urls (bez star-re-eksportu do
# ui.views: taki re-eksport w wh3d robił cykl importu przez ui.views.core).
from .hu import *  # noqa: F401,F403
from .hu_control import *  # noqa: F401,F403
from .hu_print import *  # noqa: F401,F403
from .hu_zone import *  # noqa: F401,F403
from .hu_dashboard import *  # noqa: F401,F403
from .hu_investigation import *  # noqa: F401,F403
