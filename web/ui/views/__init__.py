# Auto-split from the former monolithic views.py. Feature views live in
# sibling modules; shared imports/constants/helpers stay in core.py.
from .core import *  # noqa: F401,F403
from .admin import *  # noqa: F401,F403
from .calc import *  # noqa: F401,F403
from .cartons import *  # noqa: F401,F403
from .carton_opt import *  # noqa: F401,F403
from .artwork import *  # noqa: F401,F403
from .categories import *  # noqa: F401,F403
from .data_center import *  # noqa: F401,F403
from .pwa import *  # noqa: F401,F403
from .ukraine import *  # noqa: F401,F403
from .phv import *  # noqa: F401,F403
from .imports_excel import *  # noqa: F401,F403
from .imports_excel_md import *  # noqa: F401,F403
from .imports_sap_materials import *  # noqa: F401,F403
from .imports_users import *  # noqa: F401,F403
from .inner_packs import *  # noqa: F401,F403
from .instructions import *  # noqa: F401,F403
from .locations import *  # noqa: F401,F403

from .misc import *  # noqa: F401,F403
from .producer_dims import *  # noqa: F401,F403
from .products import *  # noqa: F401,F403
from .packspec import *  # noqa: F401,F403
from .reports import *  # noqa: F401,F403
from .slotting import *  # noqa: F401,F403
from .styleguide import *  # noqa: F401,F403
from .tasks import *  # noqa: F401,F403






from .warehouse_search import *  # noqa: F401,F403
from .zaria import *  # noqa: F401,F403
