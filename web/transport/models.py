# Agregator zgodnosci (W3): pociety na submoduly, importy dzialaja bez zmian.
# (modele zyja w modulach siostrzanych; rejestruja sie przy imporcie transport.models)
from .models_quoting import *  # noqa: F401,F403
from .models_shipment import *  # noqa: F401,F403
