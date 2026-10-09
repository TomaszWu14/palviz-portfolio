# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import _FACE_I, _FACE_J, _FACE_K, _PALLET_BOARD_FC, _RACK_FC, _SALES_FC, go
from .figures_draw import _add_box_mesh, _add_pallet_realistic, _dim_line_3d, _extend_wire  # noqa: F401

def _fig_location_2d_front(loc, carton_w: int, carton_h: int, fit: dict):
    """2D front-view (width × height) — full carton grid across the full slot face."""
    W = loc.width_cm
    H = loc.total_height_cm
    ph = loc.pallet_height_cm
    mm = loc.manipulation_margin_cm
    uh = loc.usable_height_cm
    with_pallet = fit.get("with_pallet", True)
    avail_h = fit.get("avail_h", uh)
    carton_base = ph if with_pallet else 0

    # Grid counts from fit
    n_y     = fit.get("n_y", max(1, int(W // carton_w)) if carton_w else 1)
    n_layers = fit.get("n_layers", max(1, int(avail_h // carton_h)) if carton_h else 1)
    fits_ok = fit.get("fits_h", True) and fit.get("fits_w", True)

    fig = go.Figure()
    # Slot outline
    fig.add_shape(type="rect", x0=0, y0=0, x1=W, y1=H,
                  line=dict(color="#1e3a8a", width=2), fillcolor="rgba(0,0,0,0)")
    # Pallet zone
    if with_pallet and ph > 0:
        fig.add_shape(type="rect", x0=0, y0=0, x1=W, y1=ph,
                      fillcolor="rgba(156,163,175,0.35)", line=dict(width=0))
    # Manipulation margin
    if mm > 0:
        fig.add_shape(type="rect", x0=0, y0=H - mm, x1=W, y1=H,
                      fillcolor="rgba(251,146,60,0.30)", line=dict(width=0))
    # Available zone (green tint)
    fig.add_shape(type="rect", x0=0, y0=carton_base, x1=W, y1=carton_base + avail_h,
                  fillcolor="rgba(52,211,153,0.08)", line=dict(width=0))

    # ── Draw full carton grid ────────────────────────────────────────────────
    # Defensive cap: the grid is physically bounded by a single slot's geometry, but a
    # pathological tiny-carton / huge-slot combo could emit thousands of shapes. Past the
    # cap the drawn grid is truncated (the true count stays in the title). fill/border are
    # constant per call, so hoist them out of the loop.
    _GRID_CAP = 600
    fill = "rgba(59,130,246,0.22)" if fits_ok else "rgba(239,68,68,0.18)"
    border = "#3b82f6" if fits_ok else "#ef4444"
    drawn = 0
    for ly in range(n_layers):
        if drawn >= _GRID_CAP:
            break
        for iy in range(n_y):
            if drawn >= _GRID_CAP:
                break
            cx0 = iy * carton_w
            cy0 = carton_base + ly * carton_h
            cx1 = min(cx0 + carton_w, W)
            cy1 = min(cy0 + carton_h, H - mm)
            fig.add_shape(type="rect", x0=cx0, y0=cy0, x1=cx1, y1=cy1,
                          fillcolor=fill, line=dict(color=border, width=1))
            drawn += 1

    annotations = []
    if with_pallet and ph > 0:
        annotations.append(dict(x=W / 2, y=ph / 2, text=f"Paleta {ph} cm",
                                showarrow=False, font=dict(size=11, color="#374151")))
    annotations += [
        dict(x=W / 2, y=H - mm / 2, text=f"Margines {mm} cm",
             showarrow=False, font=dict(size=11, color="#92400e")),
        dict(x=-6, y=carton_base + avail_h / 2, text=f"{avail_h} cm",
             showarrow=False, font=dict(size=11, color="#065f46"), xanchor="right"),
        dict(x=W / 2, y=H + 10, text=f"Szer. {W} cm",
             showarrow=False, font=dict(size=11, color="#1e3a8a")),
    ]

    mode = "z paletą" if with_pallet else "luzem"
    fig.update_layout(
        title=f"Rzut z przodu ({mode}) — {n_y}×{n_layers} kartonów = {n_y * n_layers} szt",
        annotations=annotations,
        margin=dict(l=55, r=20, t=50, b=30), height=400,
        paper_bgcolor="#ffffff", plot_bgcolor="#f8fafc",
        xaxis=dict(range=[-18, W + 14], showgrid=False, zeroline=False, showticklabels=False),
        yaxis=dict(range=[-8, H + 26], showgrid=False, zeroline=False, title="H [cm]",
                   scaleanchor="x", scaleratio=1))
    return fig

def _fig_location_3d(loc, carton_l, carton_w, carton_h, unit_weight, pcs_per_carton, carton_tare,
                     with_pallet=True):
    """3D view: realistic warehouse rack + optional pallet + cartons."""
    PALLET_L, PALLET_W = 120, 80
    PALLET_H = loc.pallet_height_cm if with_pallet else 0
    manip = loc.manipulation_margin_cm
    LW = loc.depth_cm        # x axis (depth)
    WW = loc.width_cm        # y axis (clearance width)
    HW = loc.total_height_cm  # z axis
    usable_h = loc.usable_height_cm
    avail_h = usable_h + (loc.pallet_height_cm if not with_pallet else 0)

    total_vol = round(WW * LW * HW / 1_000_000, 3)
    usable_vol = round(WW * LW * usable_h / 1_000_000, 3)
    avail_vol = round(WW * LW * avail_h / 1_000_000, 3)

    fig = go.Figure()
    wire_x, wire_y, wire_z = [], [], []

    # ── Rack structural elements (orange steel) ────────────────────────────
    bm_h = 6    # beam height
    bm_d = 9    # beam depth (x direction)

    # Single top horizontal beam (upper level support) — at HW - manip
    bz_top = HW - manip
    if 0 < bz_top < HW:
        for bx in [0, LW - bm_d]:
            _add_box_mesh(fig, bx, 0, bz_top, bm_d, WW, bm_h, fc=_RACK_FC)
            _extend_wire(wire_x, wire_y, wire_z, bx, 0, bz_top, bm_d, WW, bm_h)

    # Floor plate (concrete slab) — the bottom of the location
    floor_h = 3
    _add_box_mesh(fig, 0, 0, -floor_h, LW, WW, floor_h, fc=_PALLET_BOARD_FC)
    _extend_wire(wire_x, wire_y, wire_z, 0, 0, -floor_h, LW, WW, floor_h)

    # ── Manipulation margin zone (semi-transparent orange) ─────────────────
    z_manip = HW - manip
    if manip > 0:
        fig.add_trace(go.Mesh3d(
            x=[0, LW, LW, 0,  0, LW, LW, 0],
            y=[0,  0, WW, WW,  0,  0, WW, WW],
            z=[z_manip]*4 + [HW]*4,
            i=_FACE_I, j=_FACE_J, k=_FACE_K,
            color="#f97316", opacity=0.14,
            showlegend=False, showscale=False, name="Strefa manipulacji"))

    # ── Pallet ─────────────────────────────────────────────────────────────
    px = max(0.0, (LW - PALLET_L) / 2)
    py = max(0.0, (WW - PALLET_W) / 2)
    if with_pallet:
        _add_pallet_realistic(fig, PALLET_L, PALLET_W, loc.pallet_height_cm,
                              wire_x, wire_y, wire_z, ox=px, oy=py)

    # ── Cartons (all layers up to available height) ─────────────────────────
    if with_pallet:
        # Cartons sit on pallet footprint (80×120 cm)
        foot_l, foot_w = PALLET_L, PALLET_W
        fits_w = PALLET_W <= WW
        fits_d = PALLET_L <= LW
        ox, oy = px, py  # pallet-centred offset
    else:
        # Cartons directly on floor — use full location footprint
        foot_l, foot_w = LW, WW
        fits_w = carton_w <= WW
        fits_d = carton_l <= LW
        ox, oy = 0, 0
    fits_h = carton_h <= avail_h

    n_x = max(0, int(foot_l // carton_l)) if carton_l > 0 else 0
    n_y = max(0, int(foot_w // carton_w)) if carton_w > 0 else 0
    n_layers = max(0, int(avail_h // carton_h)) if carton_h > 0 else 0
    if n_x == 0 or n_y == 0 or n_layers == 0:
        # show at least one carton to help diagnose
        n_x = max(n_x, 1); n_y = max(n_y, 1); n_layers = max(n_layers, 1)
    # Bound the number of *rendered* boxes — a tiny carton in a big slot can be
    # millions of meshes (memory blowup). The true count is kept for the metric below.
    RENDER_CAP = 800
    rx, ry, rz = n_x, n_y, n_layers
    if n_x * n_y * n_layers > RENDER_CAP:
        scale = (RENDER_CAP / (n_x * n_y * n_layers)) ** (1.0 / 3.0)
        rx = max(1, int(n_x * scale)); ry = max(1, int(n_y * scale)); rz = max(1, int(n_layers * scale))
    for layer in range(rz):
        cz = PALLET_H + layer * carton_h
        for ix in range(rx):
            for iy in range(ry):
                _add_box_mesh(fig, ox + ix * carton_l, oy + iy * carton_w, cz,
                              carton_l, carton_w, carton_h)
                _extend_wire(wire_x, wire_y, wire_z, ox + ix * carton_l,
                             oy + iy * carton_w, cz, carton_l, carton_w, carton_h)

    if wire_x:
        fig.add_trace(go.Scatter3d(x=wire_x, y=wire_y, z=wire_z, mode="lines",
            line=dict(color="#374151", width=0.8), showlegend=False, hoverinfo="skip"))

    # ── Dimension lines ─────────────────────────────────────────────────────
    off = 18
    _dim_line_3d(fig, 0, WW + off, 0, LW, WW + off, 0, f"Głęb. {LW} cm")
    _dim_line_3d(fig, LW + off, 0, 0, LW + off, WW, 0, f"Szer. {WW} cm")
    _dim_line_3d(fig, LW + off, WW, 0, LW + off, WW, HW, f"H {HW} cm", color="#1a56db")
    dim_base_z = loc.pallet_height_cm if with_pallet else 0
    _dim_line_3d(fig, -(off - 6), 0, dim_base_z, -(off - 6), 0, HW - manip,
                 f"Użytk. {usable_h if with_pallet else avail_h} cm", color="#059669")

    _axis = dict(showticklabels=False, showgrid=True, gridcolor="#e5e7eb",
                 zeroline=False, showline=False, ticklen=0)
    fit_label = "✓ MIEŚCI SIĘ" if (fits_h and fits_w and fits_d) else "✗ NIE MIEŚCI"
    mode_label = "z paletą" if with_pallet else "bez palety"
    fig.update_layout(
        title=(f"{loc.name}  |  {mode_label}  |  karton {carton_l}×{carton_w}×{carton_h} cm  |  "
               f"{fit_label}  |  V użytk. = {avail_vol} m³"),
        margin=dict(l=0, r=0, t=52, b=0), height=580, paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "Głębokość", "range": [-(off - 4), LW + off + 8]},
            yaxis={**_axis, "title": "Szerokość", "range": [-4, WW + off + 8]},
            zaxis={**_axis, "title": "Wysokość", "range": [-floor_h, HW + 12]},
            aspectmode="data", bgcolor="#f8fafc",
            camera=dict(eye=dict(x=1.4, y=-1.8, z=1.0))),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    total_cartons = n_x * n_y * n_layers
    szt_total = total_cartons * (pcs_per_carton or 0) if pcs_per_carton else None
    return fig, {
        "fits_h": fits_h, "fits_w": fits_w, "fits_d": fits_d,
        "pallet_l": PALLET_L, "pallet_w": PALLET_W, "pallet_h": PALLET_H,
        "usable_h": usable_h, "avail_h": avail_h, "per_layer": n_x * n_y,
        "n_x": n_x, "n_y": n_y, "n_layers": n_layers,
        "total_cartons": total_cartons,
        "pcs_per_carton": pcs_per_carton,
        "szt_total": szt_total,
        "carton_w": carton_w, "carton_l": carton_l, "carton_h": carton_h,
        "total_vol": total_vol, "usable_vol": usable_vol, "avail_vol": avail_vol,
        "with_pallet": with_pallet,
    }

def _fig_sales_unit_3d(su_l, su_w, su_h):
    lc, wc, hc = float(su_l), float(su_w), float(su_h)
    vol = round(lc * wc * hc / 1_000_000, 5)
    fig = go.Figure()
    _add_box_mesh(fig, 0, 0, 0, lc, wc, hc, fc=_SALES_FC)
    ew_x, ew_y, ew_z = [], [], []
    _extend_wire(ew_x, ew_y, ew_z, 0, 0, 0, lc, wc, hc)
    fig.add_trace(go.Scatter3d(x=ew_x, y=ew_y, z=ew_z, mode="lines",
        line=dict(color="#374151", width=1.5), showlegend=False, hoverinfo="skip"))
    off = max(4, round(min(lc, wc) * 0.25))
    _dim_line_3d(fig, 0, -off, 0, lc, -off, 0, f"L={lc} cm", "#374151")
    _dim_line_3d(fig, lc + off, 0, 0, lc + off, wc, 0, f"W={wc} cm", "#374151")
    _dim_line_3d(fig, lc + off, wc, 0, lc + off, wc, hc, f"H={hc} cm", "#1a56db")
    _axis = dict(showticklabels=False, showgrid=True, gridcolor="#e5e7eb",
                 zeroline=False, showline=False, ticklen=0)
    fig.update_layout(
        title=f"Opak. handlowe — {lc}×{wc}×{hc} cm | V={vol} m³",
        margin=dict(l=0, r=0, t=40, b=0), height=300, paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "L", "range": [-(off+2), lc+off+4]},
            yaxis={**_axis, "title": "W", "range": [-2, wc+off+4]},
            zaxis={**_axis, "title": "H", "range": [0, hc * 1.4]},
            aspectmode="data", bgcolor="#f8fafc"))
    return fig


__all__ = ['_fig_location_2d_front', '_fig_location_3d', '_fig_sales_unit_3d']
