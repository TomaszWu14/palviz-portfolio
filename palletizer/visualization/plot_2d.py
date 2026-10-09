from typing import Sequence
import os
import matplotlib.pyplot as plt

from palletizer.domain import PalletType
from palletizer.services import Placement2D


def export_layer_2d_png(pallet: PalletType, placements: Sequence[Placement2D], out_path: str) -> str:
    _d = os.path.dirname(out_path)
    if _d:
        os.makedirs(_d, exist_ok=True)

    fig, ax = plt.subplots(figsize=(9, 6))
    ax.set_xlim(0, pallet.length_cm)
    ax.set_ylim(0, pallet.width_cm)
    ax.set_aspect("equal", adjustable="box")

    # obrys palety
    ax.add_patch(plt.Rectangle((0, 0), pallet.length_cm, pallet.width_cm, fill=False, linewidth=2))

    for p in placements:
        ax.add_patch(plt.Rectangle((p.x, p.y), p.dx, p.dy, fill=False))

    ax.set_title("Warstwa - ułożenie kartonów (2D)")
    ax.set_xlabel("Długość [cm]")
    ax.set_ylabel("Szerokość [cm]")
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)

    return out_path
