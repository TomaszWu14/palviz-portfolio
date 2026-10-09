# Modele ui pocięte na moduły tematyczne (pakiet zamiast 1938-liniowego pliku).
# Importy `from ui.models import X` i `ui.models.X` (migracje!) działają bez zmian.
from .catalog import *  # noqa: F401,F403
from .packaging import *  # noqa: F401,F403
from .warehouse import *  # noqa: F401,F403
from .customers import *  # noqa: F401,F403
from .integrations import *  # noqa: F401,F403
from .carton_opt import *  # noqa: F401,F403
from .comms import *  # noqa: F401,F403
from .tasks_users import *  # noqa: F401,F403
from .zaria import *  # noqa: F401,F403
from .packspec import *  # noqa: F401,F403
from .producer_dims import *  # noqa: F401,F403
from .material_master import *  # noqa: F401,F403

# nazwy podkreślnikowe używane przez migracje/kod — star-import ich nie niesie
from .catalog import _quote_token  # noqa: F401
from .packaging import artwork_upload_to  # noqa: F401

# ── Magazyn 3D (wh3d) — W2: modele przeniesione, re-eksport dla kompatybilności ──
# (dziesiątki miejsc importuje je z ui.models; alias = zero churnu, tabele bez zmian)
from wh3d.models import (  # noqa: E402,F401
    PickerActivity, PickerActivityBatch, WarehouseAisleConfig, WarehouseHallFeature,
    WarehouseLayout, WarehouseLayoutCell, WarehouseLocationMaster,
    WarehouseLocationMasterBatch, WarehouseModel, WarehouseModelRack,
    WarehouseRackType, WarehouseSnapshot, WarehouseSnapshotRow,
)


# ── Kontrola HU (huctl) — W2: modele przeniesione, re-eksport dla kompatybilności ──
from huctl.models import (  # noqa: E402,F401
    ControlledWarehouseType, ControllerZone, EscalationRoute, GlsPackingEntry,
    HandlingUnit, HandlingUnitItem, HUControlAttempt, HUControlPhoto,
    HUPrintProject, HUPrintRun, HUQualityIssue, HUStatusEvent,
)


# ── Transport — W2: modele przeniesione, re-eksport dla kompatybilności ──
from transport.models import (  # noqa: E402,F401
    Carrier, CarrierRate, CarrierZone, DriverAssignment, QuoteRecipient,
    Shipment, ShipmentLine, ShipmentQuoteOffer, TransportKpiSnapshot,
    WarehouseReadiness,
)
