from typing import Sequence
import os
import plotly.graph_objects as go

from palletizer.domain import PalletType
from palletizer.services import Placement2D


# Unit box face triangulation (12 triangles over 8 corner vertices). Reused for every
# carton; the indices are shifted by a running vertex offset so all boxes are merged into
# a SINGLE Mesh3d trace instead of one trace per box — far smaller HTML and faster render.
_FACE_I = (0, 0, 4, 4, 0, 1, 2, 3, 4, 5, 6, 7)
_FACE_J = (1, 2, 5, 6, 4, 5, 6, 7, 3, 2, 1, 0)
_FACE_K = (2, 3, 6, 7, 1, 6, 7, 4, 0, 1, 2, 3)


def _append_box(xs, ys, zs, ii, jj, kk, x, y, z, dx, dy, dz):
    base = len(xs)
    xs.extend((x, x+dx, x+dx, x,    x,    x+dx, x+dx, x))
    ys.extend((y, y,    y+dy, y+dy, y,    y,    y+dy, y+dy))
    zs.extend((z, z,    z,    z,    z+dz, z+dz, z+dz, z+dz))
    ii.extend(base + v for v in _FACE_I)
    jj.extend(base + v for v in _FACE_J)
    kk.extend(base + v for v in _FACE_K)


def export_pallet_3d_html(
    pallet: PalletType,
    layer_placements: Sequence[Placement2D],
    carton_h_cm: int,
    layers_to_render: int,
    out_path: str
) -> str:
    _d = os.path.dirname(out_path)
    if _d:
        os.makedirs(_d, exist_ok=True)

    # Safety cap: jeśli za dużo obiektów, przytnij warstwy
    max_boxes = 250
    per_layer = len(layer_placements)
    if per_layer <= 0:
        layers_to_render = 0
    else:
        layers_to_render = max(1, min(layers_to_render, max(1, max_boxes // per_layer)))

    # Build one merged mesh for all cartons.
    xs, ys, zs, ii, jj, kk = [], [], [], [], [], []
    for zlayer in range(layers_to_render):
        z = zlayer * carton_h_cm
        for p in layer_placements:
            _append_box(xs, ys, zs, ii, jj, kk, p.x, p.y, z, p.dx, p.dy, carton_h_cm)

    fig = go.Figure()
    if xs:
        fig.add_trace(go.Mesh3d(
            x=xs, y=ys, z=zs, i=ii, j=jj, k=kk,
            opacity=0.85, flatshading=True
        ))

    fig.update_layout(
        title=f"Paleta 3D (render warstw: {layers_to_render})",
        scene=dict(
            xaxis_title="Długość [cm]",
            yaxis_title="Szerokość [cm]",
            zaxis_title="Wysokość [cm]",
            xaxis=dict(range=[0, pallet.length_cm]),
            yaxis=dict(range=[0, pallet.width_cm]),
            zaxis=dict(range=[0, pallet.max_height_cm]),
            aspectmode="data"
        ),
        margin=dict(l=0, r=0, b=0, t=40)
    )

    # Bez fig.show(): zawsze zapis do HTML
    fig.write_html(
        out_path,
        include_plotlyjs="directory",
        full_html=True,
        auto_open=False
    )
    return out_path
