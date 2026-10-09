# core/ — shared kernel split into layers (base ◅ figures/xlsx/helpers ◅ packing).
# Re-exported wholesale by core/__init__.py so `from .core import *` is unchanged.
from .base import (
    CartonVariant, _CARTON_FC, _CARTON_LAYER_PALETTES, _FACE_I, _FACE_J, _FACE_K,
    _INNER_PACK_FC, _PALLET_BOARD_FC, _PALLET_STRINGER_FC, _UNIT_FC, go,
)

def _carton_fc(layer: int = 0) -> list:
    """Cardboard face colours, cycling per layer for visual depth."""
    bot, top, fb, lr = _CARTON_LAYER_PALETTES[layer % len(_CARTON_LAYER_PALETTES)]
    return [bot, bot, top, top, fb, fb, fb, fb, lr, lr, lr, lr]

def _svg_thumbnail(pallet_meta: dict, layout: dict, w=220, h=140) -> str:
    L = pallet_meta["length_cm"] or 1
    W = pallet_meta["width_cm"] or 1
    sx = w / L
    sy = h / W
    rects = []
    for p in layout["placements"][:200]:
        x, y = p["x"] * sx, p["y"] * sy
        dx, dy = p["dx"] * sx, p["dy"] * sy
        fill = "#dbeafe" if p.get("rotated") else "#eff6ff"
        rects.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{dx:.1f}" height="{dy:.1f}" fill="{fill}" stroke="#3b82f6" stroke-width="0.5"/>')
    cog = layout.get("cog", {})
    cog_svg = f'<circle cx="{cog["x"]*sx:.1f}" cy="{cog["y"]*sy:.1f}" r="4" fill="#ef4444" opacity="0.8"/>' if cog else ""
    best = '<text x="3" y="11" font-size="9" fill="#1a56db" font-weight="bold">★</text>' if layout.get("is_best") else ""
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg">'
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="#f8fafc" stroke="#1e40af" stroke-width="1.5"/>'
            f'{"".join(rects)}{cog_svg}{best}</svg>')

def _fig_layer_2d(pallet_meta: dict, layout: dict, sku: str, layer_idx: int = 1):
    L, W = pallet_meta["length_cm"], pallet_meta["width_cm"]
    layers_used = layout.get("layers_used", 1)
    cog = layout.get("cog", {})
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=[0, L], y=[0, W], mode="markers", opacity=0, showlegend=False))
    shapes = [dict(type="rect", x0=0, y0=0, x1=L, y1=W, line=dict(width=2, color="#1e40af"), fillcolor="rgba(0,0,0,0)")]
    annotations = []
    for idx, p in enumerate(layout["placements"]):
        fill = "rgba(59,130,246,0.15)" if p.get("rotated") else "rgba(59,130,246,0.07)"
        shapes.append(dict(type="rect", x0=p["x"], y0=p["y"], x1=p["x"]+p["dx"], y1=p["y"]+p["dy"],
            line=dict(width=1, color="#3b82f6"), fillcolor=fill))
        if idx < 30:
            annotations.append(dict(x=p["x"]+p["dx"]/2, y=p["y"]+p["dy"]/2, text=sku,
                showarrow=False, font=dict(size=8, color="#1e3a8a")))
    if cog:
        fig.add_trace(go.Scatter(x=[cog["x"]], y=[cog["y"]], mode="markers+text",
            marker=dict(size=12, color="#ef4444", symbol="x"),
            text=[f"CoG ({cog['x']}, {cog['y']})"], textposition="top right",
            name=f"Środek ciężkości Δ({cog['offset_x']:+.1f}, {cog['offset_y']:+.1f})"))
        fig.add_trace(go.Scatter(x=[L/2], y=[W/2], mode="markers",
            marker=dict(size=8, color="#9ca3af", symbol="cross"), name="Środek palety"))
    fig.update_layout(
        title=f"Warstwa {layer_idx} / {layers_used} — widok z góry",
        shapes=shapes, annotations=annotations,
        margin=dict(l=10, r=10, t=40, b=10), height=340,
        paper_bgcolor="#ffffff", plot_bgcolor="#f8fafc",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    fig.update_xaxes(range=[0, L], showgrid=False, zeroline=False, title="Długość [cm]")
    fig.update_yaxes(range=[0, W], showgrid=False, zeroline=False, title="Szerokość [cm]", scaleanchor="x", scaleratio=1)
    return fig

def _add_box_mesh(fig, x, y, z, dx, dy, dz, fc=None, kind="carton"):
    if fc is None:
        fc = _CARTON_FC if kind == "carton" else _PALLET_BOARD_FC  # noqa: F821
    fig.add_trace(go.Mesh3d(
        x=[x,x+dx,x+dx,x,    x,   x+dx,x+dx,x],
        y=[y,y,   y+dy,y+dy, y,   y,   y+dy,y+dy],
        z=[z,z,   z,   z,    z+dz,z+dz,z+dz,z+dz],
        i=_FACE_I, j=_FACE_J, k=_FACE_K,
        facecolor=fc, opacity=1.0,
        showlegend=False, showscale=False, flatshading=True))

def _extend_wire(wx, wy, wz, bx, by, bz, bdx, bdy, bdz):
    pts = [
        (bx,by,bz),(bx+bdx,by,bz),None,(bx+bdx,by,bz),(bx+bdx,by+bdy,bz),None,
        (bx+bdx,by+bdy,bz),(bx,by+bdy,bz),None,(bx,by+bdy,bz),(bx,by,bz),None,
        (bx,by,bz+bdz),(bx+bdx,by,bz+bdz),None,(bx+bdx,by,bz+bdz),(bx+bdx,by+bdy,bz+bdz),None,
        (bx+bdx,by+bdy,bz+bdz),(bx,by+bdy,bz+bdz),None,(bx,by+bdy,bz+bdz),(bx,by,bz+bdz),None,
        (bx,by,bz),(bx,by,bz+bdz),None,(bx+bdx,by,bz),(bx+bdx,by,bz+bdz),None,
        (bx+bdx,by+bdy,bz),(bx+bdx,by+bdy,bz+bdz),None,(bx,by+bdy,bz),(bx,by+bdy,bz+bdz),None,
    ]
    wx.extend(p[0] if p else None for p in pts)
    wy.extend(p[1] if p else None for p in pts)
    wz.extend(p[2] if p else None for p in pts)

def _add_pallet_realistic(fig, L, W, base_h, wire_x, wire_y, wire_z, ox=0, oy=0):
    """Draw a multi-piece wooden Euro pallet. ox/oy offset the pallet origin."""
    t = max(1, round(base_h * 0.14))
    sh = max(1, base_h - 2 * t)
    str_w = max(4, round(W * 0.125))

    for sy in [0, (W - str_w) // 2, W - str_w]:
        _add_box_mesh(fig, ox, oy + sy, t, L, str_w, sh, fc=_PALLET_STRINGER_FC)
        _extend_wire(wire_x, wire_y, wire_z, ox, oy + sy, t, L, str_w, sh)

    board_w = max(4, round((W - 4 * 3) / 5))
    for i in range(5):
        ty = oy + i * (board_w + 3)
        _add_box_mesh(fig, ox, ty, base_h - t, L, board_w, t, fc=_PALLET_BOARD_FC)
        _extend_wire(wire_x, wire_y, wire_z, ox, ty, base_h - t, L, board_w, t)

    foot_l = max(6, round(L * 0.085))
    for fx in [ox, ox + (L - foot_l) // 2, ox + L - foot_l]:
        _add_box_mesh(fig, fx, oy, 0, foot_l, W, t, fc=_PALLET_BOARD_FC)
        _extend_wire(wire_x, wire_y, wire_z, fx, oy, 0, foot_l, W, t)

def _dim_line_3d(fig, x0, y0, z0, x1, y1, z1, label, color="#374151"):
    mx, my, mz = (x0+x1)/2, (y0+y1)/2, (z0+z1)/2
    fig.add_trace(go.Scatter3d(
        x=[x0, x1], y=[y0, y1], z=[z0, z1], mode="lines",
        line=dict(color=color, width=2),
        showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter3d(
        x=[mx], y=[my], z=[mz], mode="text",
        text=[f"<b>{label}</b>"],
        textfont=dict(size=10, color=color),
        showlegend=False, hoverinfo="skip"))

def _fig_pallet_3d(pallet_meta: dict, carton: CartonVariant, layout: dict, render_layers: int, total_height_used_cm: int, label: str = ""):
    L, W = pallet_meta["length_cm"], pallet_meta["width_cm"]
    Z_MAX = pallet_meta["max_height_total_cm"]
    base_h = pallet_meta["base_height_cm"]
    h_cm = carton.dims.h_cm
    cog = layout.get("cog", {})
    per_layer = len(layout["placements"])
    layers_to_render = max(1, min(int(render_layers), max(1, 220 // max(per_layer, 1)))) if per_layer else 0

    fig = go.Figure()

    # Realistic wooden pallet (boards + stringers + feet)
    wire_x, wire_y, wire_z = [], [], []
    _add_pallet_realistic(fig, L, W, base_h, wire_x, wire_y, wire_z)

    # Carton boxes — layer-based colour cycling for visual depth
    lbl_short = (label or carton.sku)[:10]
    lbl_x, lbl_y, lbl_z = [], [], []
    for zlayer in range(layers_to_render):
        z = base_h + zlayer * h_cm
        fc = _carton_fc(zlayer)
        for p in layout["placements"]:
            _add_box_mesh(fig, p["x"], p["y"], z, p["dx"], p["dy"], h_cm, fc=fc)
            _extend_wire(wire_x, wire_y, wire_z, p["x"], p["y"], z, p["dx"], p["dy"], h_cm)
            # Collect label positions (top-face centre, limit to avoid clutter)
            if len(lbl_x) < 80:
                lbl_x.append(p["x"] + p["dx"] / 2)
                lbl_y.append(p["y"] + p["dy"] / 2)
                lbl_z.append(z + h_cm * 0.52)

    # Text labels on carton faces
    if lbl_x and lbl_short:
        fig.add_trace(go.Scatter3d(
            x=lbl_x, y=lbl_y, z=lbl_z,
            mode="text",
            text=[lbl_short] * len(lbl_x),
            textfont=dict(size=7, color="#5c3317", family="monospace"),
            showlegend=False, hoverinfo="skip"))

    if wire_x:
        fig.add_trace(go.Scatter3d(
            x=wire_x, y=wire_y, z=wire_z, mode="lines",
            line=dict(color="#374151", width=1.0),
            showlegend=False, hoverinfo="skip"))

    if cog and cog.get("x") is not None:
        fig.add_trace(go.Scatter3d(
            x=[cog["x"], cog["x"]], y=[cog["y"], cog["y"]], z=[base_h, total_height_used_cm],
            mode="lines+markers", line=dict(color="#ef4444", width=5, dash="dash"),
            marker=dict(size=5, color="#ef4444"),
            name=f"CoG ({cog['x']}, {cog['y']}) Δ({cog['offset_x']:+.1f}, {cog['offset_y']:+.1f})"))

    # Dimension lines — pushed well outside the model to avoid overlap
    off = 24
    cargo_h = total_height_used_cm - base_h
    cargo_vol = round(L * W * cargo_h / 1_000_000, 3)
    _dim_line_3d(fig, 0, W + off, 0, L, W + off, 0, f"L = {L} cm")
    _dim_line_3d(fig, L + off, 0, 0, L + off, W, 0, f"W = {W} cm")
    _dim_line_3d(fig, L + off, W, 0, L + off, W, total_height_used_cm,
                 f"H = {total_height_used_cm} cm", color="#1a56db")
    if h_cm and layers_to_render:
        _dim_line_3d(fig, -(off - 6), 0, base_h, -(off - 6), 0, total_height_used_cm,
                     f"towar = {cargo_h} cm", color="#059669")

    _axis = dict(showticklabels=False, showgrid=True, gridcolor="#e5e7eb",
                 zeroline=False, showline=False, ticklen=0)
    fig.update_layout(
        title=(f"{carton.sku}  |  karton {carton.dims.l_cm}×{carton.dims.w_cm}×{h_cm} cm  |  "
               f"{per_layer} kart/warst. × {layers_to_render} warstw  |  V towaru = {cargo_vol} m³"),
        margin=dict(l=0, r=0, t=50, b=0), height=540,
        paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "L [cm]", "range": [-(off + 4), L + off + 8]},
            yaxis={**_axis, "title": "W [cm]", "range": [-4, W + off + 6]},
            zaxis={**_axis, "title": "H [cm]", "range": [0, Z_MAX]},
            aspectmode="data", bgcolor="#f8fafc"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    return fig

def _fig_carton_3d(carton: CartonVariant):
    lc, wc, hc = carton.dims.l_cm, carton.dims.w_cm, carton.dims.h_cm
    fig = go.Figure()
    _add_box_mesh(fig, 0, 0, 0, lc, wc, hc)
    ew_x, ew_y, ew_z = [], [], []
    _extend_wire(ew_x, ew_y, ew_z, 0, 0, 0, lc, wc, hc)
    fig.add_trace(go.Scatter3d(x=ew_x, y=ew_y, z=ew_z, mode="lines",
        line=dict(color="#374151", width=1.5), showlegend=False, hoverinfo="skip"))
    _axis = dict(showticklabels=False, showgrid=False,
                 zeroline=False, showline=False, ticklen=0)
    pad = max(4, round(min(lc, wc) * 0.08))
    fig.update_layout(
        margin=dict(l=0, r=0, t=4, b=0), height=340, paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "", "range": [-pad, lc + pad]},
            yaxis={**_axis, "title": "", "range": [-pad, wc + pad]},
            zaxis={**_axis, "title": "", "range": [0, hc + pad]},
            aspectmode="data", bgcolor="#f8fafc"))
    return fig

def _fig_unit_3d(unit_l: float, unit_w: float, unit_h: float):
    """3D view of a single product unit (sztuka)."""
    lc, wc, hc = float(unit_l), float(unit_w), float(unit_h)
    vol = round(lc * wc * hc / 1_000_000, 5)
    fig = go.Figure()
    _add_box_mesh(fig, 0, 0, 0, lc, wc, hc, fc=_UNIT_FC)
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
        title=f"Sztuka — {lc}×{wc}×{hc} cm | V={vol} m³",
        margin=dict(l=0, r=0, t=40, b=0), height=300, paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "L", "range": [-(off + 2), lc + off + 4]},
            yaxis={**_axis, "title": "W", "range": [-2, wc + off + 4]},
            zaxis={**_axis, "title": "H", "range": [0, hc * 1.4]},
            aspectmode="data", bgcolor="#f8fafc"))
    return fig

def _fig_inner_pack_3d(ip_l: float, ip_w: float, ip_h: float,
                        unit_l: float, unit_w: float, unit_h: float,
                        units_per_pack: int):
    """Inner pack (opakowanie zbiorcze) with arranged unit boxes inside."""
    il, iw, ih = float(ip_l), float(ip_w), float(ip_h)
    ul, uw, uh = float(unit_l), float(unit_w), float(unit_h)
    vol = round(il * iw * ih / 1_000_000, 5)
    fig = go.Figure()

    # Semi-transparent inner pack shell
    fig.add_trace(go.Mesh3d(
        x=[0, il, il, 0,  0, il, il, 0],
        y=[0, 0, iw, iw,  0,  0, iw, iw],
        z=[0,  0,  0,  0, ih, ih, ih, ih],
        i=_FACE_I, j=_FACE_J, k=_FACE_K,
        facecolor=_INNER_PACK_FC, opacity=0.20,
        showlegend=False, showscale=False, flatshading=True))
    pw_x, pw_y, pw_z = [], [], []
    _extend_wire(pw_x, pw_y, pw_z, 0, 0, 0, il, iw, ih)
    fig.add_trace(go.Scatter3d(x=pw_x, y=pw_y, z=pw_z, mode="lines",
        line=dict(color="#2563eb", width=2.0), showlegend=False, hoverinfo="skip"))

    # Arrange unit boxes inside in a grid
    n_l = max(1, int(il // ul)) if ul > 0 else 1
    n_w = max(1, int(iw // uw)) if uw > 0 else 1
    n_h = max(1, int(ih // uh)) if uh > 0 else 1
    shown = 0
    wire_x, wire_y, wire_z = [], [], []
    outer: bool = True
    for kh in range(n_h):
        for kw in range(n_w):
            for kl in range(n_l):
                if shown >= units_per_pack or shown >= 60:
                    outer = False
                    break
                _add_box_mesh(fig, kl * ul, kw * uw, kh * uh, ul, uw, uh, fc=_UNIT_FC)
                _extend_wire(wire_x, wire_y, wire_z, kl * ul, kw * uw, kh * uh, ul, uw, uh)
                shown += 1
            if not outer:
                break
        if not outer:
            break

    if wire_x:
        fig.add_trace(go.Scatter3d(x=wire_x, y=wire_y, z=wire_z, mode="lines",
            line=dict(color="#374151", width=0.8), showlegend=False, hoverinfo="skip"))

    off = max(4, round(min(il, iw) * 0.22))
    _dim_line_3d(fig, 0, -off, 0, il, -off, 0, f"L={il} cm", "#1d4ed8")
    _dim_line_3d(fig, il + off, 0, 0, il + off, iw, 0, f"W={iw} cm", "#1d4ed8")
    _dim_line_3d(fig, il + off, iw, 0, il + off, iw, ih, f"H={ih} cm", "#1d4ed8")
    _axis = dict(showticklabels=False, showgrid=True, gridcolor="#e5e7eb",
                 zeroline=False, showline=False, ticklen=0)
    fig.update_layout(
        title=f"Opak. zbiorcze — {il}×{iw}×{ih} cm | {shown}/{units_per_pack} szt | V={vol} m³",
        margin=dict(l=0, r=0, t=40, b=0), height=300, paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "L", "range": [-(off + 2), il + off + 4]},
            yaxis={**_axis, "title": "W", "range": [-2, iw + off + 4]},
            zaxis={**_axis, "title": "H", "range": [0, ih * 1.4]},
            aspectmode="data", bgcolor="#f8fafc"))
    return fig

def _fig_carton_with_packs_3d(lc: float, wc: float, hc: float,
                                pcs_per_carton: int,
                                ip_l: float, ip_w: float, ip_h: float,
                                packs_per_carton: int):
    """Carton 3D showing inner packs arranged inside (semi-transparent shell)."""
    vol = round(lc * wc * hc / 1_000_000, 4)
    fig = go.Figure()

    # Semi-transparent carton shell
    fig.add_trace(go.Mesh3d(
        x=[0, lc, lc, 0,  0, lc, lc, 0],
        y=[0,  0, wc, wc,  0,  0, wc, wc],
        z=[0,  0,  0,  0, hc, hc, hc, hc],
        i=_FACE_I, j=_FACE_J, k=_FACE_K,
        facecolor=_CARTON_FC, opacity=0.18,
        showlegend=False, showscale=False, flatshading=True))
    ew_x, ew_y, ew_z = [], [], []
    _extend_wire(ew_x, ew_y, ew_z, 0, 0, 0, lc, wc, hc)
    fig.add_trace(go.Scatter3d(x=ew_x, y=ew_y, z=ew_z, mode="lines",
        line=dict(color="#374151", width=2.0), showlegend=False, hoverinfo="skip"))

    # Arrange inner packs inside
    n_l = max(1, int(lc // ip_l)) if ip_l > 0 else 1
    n_w = max(1, int(wc // ip_w)) if ip_w > 0 else 1
    n_h = max(1, int(hc // ip_h)) if ip_h > 0 else 1
    shown = 0
    wire_x, wire_y, wire_z = [], [], []
    outer = True
    for kh in range(n_h):
        for kw in range(n_w):
            for kl in range(n_l):
                if shown >= packs_per_carton or shown >= 40:
                    outer = False
                    break
                _add_box_mesh(fig, kl * ip_l, kw * ip_w, kh * ip_h,
                               ip_l, ip_w, ip_h, fc=_INNER_PACK_FC)
                _extend_wire(wire_x, wire_y, wire_z,
                             kl * ip_l, kw * ip_w, kh * ip_h, ip_l, ip_w, ip_h)
                shown += 1
            if not outer:
                break
        if not outer:
            break

    if wire_x:
        fig.add_trace(go.Scatter3d(x=wire_x, y=wire_y, z=wire_z, mode="lines",
            line=dict(color="#374151", width=0.8), showlegend=False, hoverinfo="skip"))

    off = max(8, round(min(lc, wc) * 0.22))
    _dim_line_3d(fig, 0, -off, 0, lc, -off, 0, f"L={lc} cm", "#374151")
    _dim_line_3d(fig, lc + off, 0, 0, lc + off, wc, 0, f"W={wc} cm", "#374151")
    _dim_line_3d(fig, lc + off, wc, 0, lc + off, wc, hc, f"H={hc} cm", "#1a56db")
    _axis = dict(showticklabels=False, showgrid=True, gridcolor="#e5e7eb",
                 zeroline=False, showline=False, ticklen=0)
    fig.update_layout(
        title=f"Karton — {lc}×{wc}×{hc} cm | {shown}/{packs_per_carton} opak. | V={vol} m³",
        margin=dict(l=0, r=0, t=44, b=0), height=300, paper_bgcolor="#ffffff",
        scene=dict(
            xaxis={**_axis, "title": "L", "range": [-(off + 2), lc + off + 4]},
            yaxis={**_axis, "title": "W", "range": [-(off + 2), wc + off + 4]},
            zaxis={**_axis, "title": "H", "range": [0, hc * 1.3]},
            aspectmode="data", bgcolor="#f8fafc"))
    return fig


__all__ = [
    '_add_box_mesh', '_add_pallet_realistic', '_carton_fc', '_dim_line_3d',
    '_extend_wire', '_fig_carton_3d', '_fig_carton_with_packs_3d', '_fig_inner_pack_3d',
    '_fig_layer_2d', '_fig_pallet_3d', '_fig_unit_3d', '_svg_thumbnail',
]
