from typing import List, Tuple, Dict, Any  # noqa: F401

from palletizer.domain import CartonVariant, PalletType
from .maxrects import _maxrects_layer  # noqa: F401  (kompat: testy importuja stad)
from .layer_geometry import Placement2D, LayoutOption, PalletizationResult  # noqa: F401
from .layer_patterns import (
    _area_utilization, _uniform_grid, _mixed_stripe_length, _mixed_stripe_width,
    _column_packed, _brick_pattern, _fill_region, _block_split, _pinwheel,
)


class PalletCalculator:

    @staticmethod
    def _assert_fits(carton: CartonVariant, pallet: PalletType) -> None:
        """Raise a clear error for cartons that cannot be palletized at all.

        Without this, an oversized/overweight carton silently yields 0 layers →
        0 cartons/pallet and the whole demand is reported as "remainder".
        """
        cl, cw, ch = carton.dims.l_cm, carton.dims.w_cm, carton.dims.h_cm

        if ch > pallet.max_height_cm:
            raise ValueError(
                f"Karton {carton.id} jest wyższy ({ch} cm) niż dozwolona wysokość "
                f"ładunku na palecie {pallet.code} ({pallet.max_height_cm} cm) — "
                f"nie zmieści się ani jedna warstwa."
            )

        fits_normal = cl <= pallet.length_cm and cw <= pallet.width_cm
        fits_rotated = carton.allow_rotation and cw <= pallet.length_cm and cl <= pallet.width_cm
        if not (fits_normal or fits_rotated):
            raise ValueError(
                f"Podstawa kartonu {carton.id} ({cl}×{cw} cm) nie mieści się na "
                f"palecie {pallet.code} ({pallet.length_cm}×{pallet.width_cm} cm)."
            )

        if pallet.max_weight_kg > 0 and carton.carton_weight_kg > pallet.max_weight_kg:
            raise ValueError(
                f"Pojedynczy karton {carton.id} ({round(carton.carton_weight_kg, 1)} kg) "
                f"przekracza dopuszczalną masę palety {pallet.code} "
                f"({pallet.max_weight_kg} kg)."
            )


    # Aliasy zgodnosci: wzorce warstw zyja w layer_patterns (czysty ruch kodu);
    # PalletCalculator._X(...) dziala jak dawniej.
    _area_utilization = staticmethod(_area_utilization)
    _uniform_grid = staticmethod(_uniform_grid)
    _mixed_stripe_length = staticmethod(_mixed_stripe_length)
    _mixed_stripe_width = staticmethod(_mixed_stripe_width)
    _column_packed = staticmethod(_column_packed)
    _brick_pattern = staticmethod(_brick_pattern)
    _fill_region = staticmethod(_fill_region)
    _block_split = staticmethod(_block_split)
    _pinwheel = staticmethod(_pinwheel)

    # ── generate_layout_options: podział na generatory rodzin wariantów (CODE-001) ──
    # Każdy _add_* dopisuje warianty w tej samej kolejności co dawniej; ranking
    # i deduplikacja w _rank_distinct. Zachowanie 1:1 (patrz test charakteryzujący).

    @staticmethod
    def _append_option(options: List[LayoutOption], pallet: PalletType, name: str,
                       count: int, placements, dx, dy) -> None:
        """Dopisz wariant, jeśli ma > 0 kartonów (wykorzystanie liczone z dx×dy)."""
        if count > 0:
            options.append(LayoutOption(
                name=name,
                cartons_per_layer=count,
                utilization_percent=PalletCalculator._area_utilization(pallet, count, dx, dy),
                placements=placements,
            ))

    @staticmethod
    def _add_uniform(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        l, w = carton.dims.l_cm, carton.dims.w_cm
        # Option U1: uniform (LxW)
        c1, p1 = PalletCalculator._uniform_grid(pallet, dx=l, dy=w, rotated=False)
        PalletCalculator._append_option(options, pallet, "U1_uniform_LxW", c1, p1, l, w)
        # Option U2: uniform rotated (WxL)
        if can_rotate:
            c2, p2 = PalletCalculator._uniform_grid(pallet, dx=w, dy=l, rotated=True)
            PalletCalculator._append_option(options, pallet, "U2_uniform_WxL", c2, p2, w, l)

    @staticmethod
    def _add_mixed_stripes(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        """Mixed stripes (tylko jeśli rotacja ma sens): baza LxW i odwrotnie WxL."""
        if not can_rotate:
            return
        l, w = carton.dims.l_cm, carton.dims.w_cm
        jobs = [
            ("M1_mixed_stripe_length_base_LxW", PalletCalculator._mixed_stripe_length, l, w),
            ("M2_mixed_stripe_width_base_LxW", PalletCalculator._mixed_stripe_width, l, w),
            ("M3_mixed_stripe_length_base_WxL", PalletCalculator._mixed_stripe_length, w, l),
            ("M4_mixed_stripe_width_base_WxL", PalletCalculator._mixed_stripe_width, w, l),
        ]
        for name, fn, bdx, bdy in jobs:
            cm, pm = fn(pallet, base_dx=bdx, base_dy=bdy, rot_dx=bdy, rot_dy=bdx)
            PalletCalculator._append_option(options, pallet, name, cm, pm, bdx, bdy)

    @staticmethod
    def _maxrects_jobs(carton: CartonVariant, can_rotate: bool):
        """(nazwa, seed_l, seed_w, allow_rotation, heurystyka) dla wariantów R1–R9."""
        l, w = carton.dims.l_cm, carton.dims.w_cm
        rot = carton.allow_rotation
        jobs = [
            ("R1_maxrects_optimal", l, w, rot, "bssf"),
            ("R2_maxrects_no_rotation", l, w, False, "bssf"),   # clean grid-like packing
        ]
        if can_rotate:                                          # seed from the rotated orientation
            jobs.append(("R3_maxrects_rotated_seed", w, l, True, "bssf"))
        jobs += [
            ("R4_maxrects_longside", l, w, rot, "blsf"),
            ("R5_maxrects_areafit", l, w, rot, "baf"),
            ("R6_maxrects_bottomleft", l, w, rot, "bl"),
        ]
        if can_rotate:                                          # same heuristics, rotated seed
            jobs += [
                ("R7_maxrects_longside_rot", w, l, rot, "blsf"),
                ("R8_maxrects_areafit_rot", w, l, rot, "baf"),
                ("R9_maxrects_bottomleft_rot", w, l, rot, "bl"),
            ]
        return jobs

    @staticmethod
    def _add_maxrects(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        """MaxRects (often beats grid + stripe approaches) — kilka heurystyk i seedów."""
        l, w = carton.dims.l_cm, carton.dims.w_cm
        for name, sl, sw, allow_rot, heuristic in PalletCalculator._maxrects_jobs(carton, can_rotate):
            mr = _maxrects_layer(pallet.length_cm, pallet.width_cm, sl, sw,
                                 allow_rotation=allow_rot, heuristic=heuristic)
            placements = tuple(
                Placement2D(x=p["x"], y=p["y"], dx=p["dx"], dy=p["dy"], rotated=p["rotated"])
                for p in mr
            )
            PalletCalculator._append_option(options, pallet, name, len(mr), placements, l, w)

    @staticmethod
    def _add_brick(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        """Brick / interlocked ("na zakładkę") — both orientations."""
        l, w = carton.dims.l_cm, carton.dims.w_cm
        brick_better = (pallet.length_cm // w) * (pallet.width_cm // l) >= (pallet.length_cm // l) * (pallet.width_cm // w)
        brick_orient = (w, l, True) if (can_rotate and brick_better) else (l, w, False)
        bc, bp = PalletCalculator._brick_pattern(pallet, *brick_orient)
        PalletCalculator._append_option(options, pallet, "B1_brick_interlock", bc, bp,
                                        brick_orient[0], brick_orient[1])
        if can_rotate:                                          # brick in the other orientation
            o2 = (l, w, False) if brick_orient[2] else (w, l, True)
            bc2, bp2 = PalletCalculator._brick_pattern(pallet, *o2)
            PalletCalculator._append_option(options, pallet, "B2_brick_interlock_alt", bc2, bp2, o2[0], o2[1])

    @staticmethod
    def _add_block_splits(options, carton: CartonVariant, pallet: PalletType, jobs) -> None:
        l, w = carton.dims.l_cm, carton.dims.w_cm
        for axis, frac, tag in jobs:
            kc, kp = PalletCalculator._block_split(pallet, l, w, axis=axis, frac=frac)
            PalletCalculator._append_option(options, pallet, tag, kc, kp, l, w)

    @staticmethod
    def _add_blocks_and_pinwheel(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        """Two-block splits (base + rotated) along length/width at a few ratios + pinwheel."""
        if not can_rotate:
            return
        l, w = carton.dims.l_cm, carton.dims.w_cm
        PalletCalculator._add_block_splits(options, carton, pallet, [
            ("x", 0.5,  "K1_block_split_length"),
            ("y", 0.5,  "K2_block_split_width"),
            ("x", 0.34, "K3_block_split_length_1_3"),
            ("y", 0.34, "K4_block_split_width_1_3"),
        ])
        # 4-quadrant pinwheel / windmill
        pc, pp = PalletCalculator._pinwheel(pallet, l, w)
        PalletCalculator._append_option(options, pallet, "P1_pinwheel", pc, pp, l, w)

    @staticmethod
    def _add_column_packed(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        """Column-packed: per-column best orientation (and its transpose)."""
        l, w = carton.dims.l_cm, carton.dims.w_cm
        jobs = [("G2_column_packed", l, w, False), ("G3_row_packed", l, w, True)]
        if can_rotate:
            jobs += [("G4_column_packed_rot", w, l, False), ("G5_row_packed_rot", w, l, True)]
        for name, cl, cw, transpose in jobs:
            cc, cp = PalletCalculator._column_packed(pallet, cl, cw, carton.allow_rotation, transpose=transpose)
            PalletCalculator._append_option(options, pallet, name, cc, cp, l, w)

    @staticmethod
    def _add_extra_blocks_and_mirror(options, carton: CartonVariant, pallet: PalletType, can_rotate: bool) -> None:
        """More two-block ratios + mirrored pinwheel (extra distinct patterns)."""
        if not can_rotate:
            return
        l, w = carton.dims.l_cm, carton.dims.w_cm
        PalletCalculator._add_block_splits(options, carton, pallet, [
            ("x", 0.66, "K5_block_split_length_2_3"),
            ("y", 0.66, "K6_block_split_width_2_3"),
        ])
        # mirrored pinwheel: swap which quadrants are rotated
        PL, PW = pallet.length_cm, pallet.width_cm
        mx, my = PL // 2, PW // 2
        pl2: List[Placement2D] = []
        pc2 = (PalletCalculator._fill_region(pl2, 0, 0, mx, my, w, l, True)
               + PalletCalculator._fill_region(pl2, mx, 0, PL, my, l, w, False)
               + PalletCalculator._fill_region(pl2, 0, my, mx, PW, l, w, False)
               + PalletCalculator._fill_region(pl2, mx, my, PL, PW, w, l, True))
        PalletCalculator._append_option(options, pallet, "P2_pinwheel_mirror", pc2, tuple(pl2), l, w)

    @staticmethod
    def _rank_distinct(options: List[LayoutOption], limit: int = 15) -> List[LayoutOption]:
        """Sortuj (max kartonów, potem wykorzystanie, potem nazwa — malejąco) i zostaw
        do `limit` wizualnie RÓŻNYCH ułożeń (ta sama liczba kartonów bywa układana na
        kilka sposobów — użytkownik wybiera; niektóre kartony mają mniej wariantów)."""
        options.sort(key=lambda o: (o.cartons_per_layer, o.utilization_percent, o.name), reverse=True)
        seen = set()
        uniq: List[LayoutOption] = []
        for o in options:
            sig = tuple(sorted((p.x, p.y, p.dx, p.dy) for p in o.placements))
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(o)
            if len(uniq) >= limit:
                break
        return uniq

    def generate_layout_options(carton: CartonVariant, pallet: PalletType) -> List[LayoutOption]:
        # Brak @staticmethod to zachowanie historyczne (wołane wyłącznie przez klasę).
        can_rotate = carton.allow_rotation and carton.dims.l_cm != carton.dims.w_cm
        options: List[LayoutOption] = []
        for add in (PalletCalculator._add_uniform, PalletCalculator._add_mixed_stripes,
                    PalletCalculator._add_maxrects, PalletCalculator._add_brick,
                    PalletCalculator._add_blocks_and_pinwheel, PalletCalculator._add_column_packed,
                    PalletCalculator._add_extra_blocks_and_mirror):
            add(options, carton, pallet, can_rotate)
        return PalletCalculator._rank_distinct(options)

    @staticmethod
    def calculate(carton: CartonVariant, pallet: PalletType) -> PalletizationResult:
        carton.validate()
        pallet.validate()
        PalletCalculator._assert_fits(carton, pallet)

        layouts = PalletCalculator.generate_layout_options(carton, pallet)
        if not layouts:
            raise ValueError(f"Brak możliwego ułożenia dla {carton.id} na palecie {pallet.code}")

        best = layouts[0]

        cartons_needed = carton.cartons_needed
        carton_weight = carton.carton_weight_kg

        # Layers by height
        max_layers_height = pallet.max_height_cm // carton.dims.h_cm

        # Layers by weight. max_weight_kg == 0 means "no weight limit" (consistent with
        # _assert_fits), so only constrain when a positive limit is set — otherwise an
        # unlimited pallet would collapse to 0 layers (0 // weight == 0).
        max_layers_weight = max_layers_height
        if pallet.max_weight_kg > 0 and best.cartons_per_layer > 0 and carton_weight > 0:
            per_layer_weight = best.cartons_per_layer * carton_weight
            if per_layer_weight > 0:
                max_layers_weight = int(pallet.max_weight_kg // per_layer_weight)
        max_layers_weight = max(0, max_layers_weight)

        layers_used = min(max_layers_height, max_layers_weight)

        cartons_per_pallet = best.cartons_per_layer * layers_used if layers_used > 0 else 0

        # Weight-limited partial layer: a full geometric layer may exceed the pallet weight
        # limit even though a single carton fits (guaranteed by _assert_fits). Rather than
        # silently reporting 0 cartons/pallet, pack one weight-limited (partial) layer.
        if (cartons_per_pallet == 0 and max_layers_height >= 1
                and pallet.max_weight_kg > 0 and carton_weight > 0):
            weight_limited = int(pallet.max_weight_kg // carton_weight)
            if weight_limited > 0:
                cartons_per_pallet = min(best.cartons_per_layer, weight_limited)
                layers_used = 1

        pallets_full = 0
        remainder_cartons = cartons_needed
        if cartons_per_pallet > 0:
            pallets_full = cartons_needed // cartons_per_pallet
            remainder_cartons = cartons_needed % cartons_per_pallet

        # szacowanie „reszty w sztukach”
        pieces_on_full_pallets = pallets_full * cartons_per_pallet * carton.pieces_per_carton
        remainder_pieces_est = max(0, carton.demand_pieces - pieces_on_full_pallets)

        height_used = layers_used * carton.dims.h_cm
        weight_per_pallet = cartons_per_pallet * carton_weight if cartons_per_pallet > 0 else 0.0

        return PalletizationResult(
            carton_id=carton.id,
            pallet_code=pallet.code,
            pallet_dims_cm=f"{pallet.length_cm}x{pallet.width_cm}x{pallet.max_height_cm}",
            carton_dims_cm=f"{carton.dims.l_cm}x{carton.dims.w_cm}x{carton.dims.h_cm}",

            pieces_per_carton=carton.pieces_per_carton,
            unit_weight_kg=carton.unit_weight_kg,
            carton_tare_kg=carton.carton_tare_kg,
            carton_weight_kg=round(carton_weight, 3),

            demand_pieces=carton.demand_pieces,
            cartons_needed=cartons_needed,

            max_layers_by_height=max_layers_height,
            max_layers_by_weight=max_layers_weight,
            layers_used=layers_used,

            best_layout=best,
            all_layouts=tuple(layouts),

            cartons_per_pallet=cartons_per_pallet,
            pallets_full=pallets_full,
            remainder_cartons=remainder_cartons,
            remainder_pieces_est=remainder_pieces_est,

            height_used_cm=height_used,
            weight_per_pallet_kg=round(weight_per_pallet, 3),
        )
