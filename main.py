import argparse
import os
import pandas as pd

from palletizer.config import get_pallet_preset
from palletizer.domain import PalletType, Dimensions
from palletizer.io import load_cartons_from_csv, read_cartons_from_cli
from palletizer.services import PalletCalculator
from palletizer.visualization import export_layer_2d_png, export_pallet_3d_html


def build_pallet(code: str, max_height: int | None, length: int | None, width: int | None) -> PalletType:
    p = get_pallet_preset(code)

    L = int(length) if length is not None else p.length_cm
    W = int(width) if width is not None else p.width_cm
    H = int(max_height) if max_height is not None else p.default_max_height_cm

    return PalletType(
        code=p.code,
        dims=Dimensions(l_cm=L, w_cm=W, h_cm=H),
        max_weight_kg=p.max_weight_kg,
    ).validate()


def parse_args():
    ap = argparse.ArgumentParser(description="Paletyzacja PRO (129x80) - warianty ułożenia")
    ap.add_argument("--mode", choices=["csv", "cli"], default="csv", help="Wejście danych: csv lub cli")
    ap.add_argument("--input", default="data/produkty.csv", help="Ścieżka do CSV (dla mode=csv)")
    ap.add_argument("--pallet", default="Z129", help="Typ palety: Z129/EU")
    ap.add_argument("--max-height", type=int, default=None, help="Max wysokość palety [cm] (nadpisuje preset)")
    ap.add_argument("--length", type=int, default=None, help="Długość palety [cm] (nadpisuje preset)")
    ap.add_argument("--width", type=int, default=None, help="Szerokość palety [cm] (nadpisuje preset)")
    ap.add_argument("--export-viz", type=int, default=1, help="1=eksport 2D PNG + 3D HTML (bez fig.show), 0=bez")
    ap.add_argument("--render-layers", type=int, default=3, help="Ile warstw renderować w 3D (HTML) (cap auto)")
    return ap.parse_args()


def main():
    args = parse_args()
    pallet = build_pallet(args.pallet, args.max_height, args.length, args.width)

    if args.mode == "csv":
        cartons = load_cartons_from_csv(args.input)
    else:
        cartons = read_cartons_from_cli()

    os.makedirs("output", exist_ok=True)

    results_rows = []
    options_rows = []

    print(f"✅ Paleta: {pallet.code} | {pallet.length_cm}x{pallet.width_cm} | maxH={pallet.max_height_cm} | maxW={pallet.max_weight_kg}kg")
    print(f"✅ Pozycji: {len(cartons)}\n")

    for c in cartons:
        try:
            res = PalletCalculator.calculate(c, pallet)
        except ValueError as e:
            print(f"⚠️  Pominięto {c.id}: {e}\n")
            continue

        print(f"=== {res.carton_id} ===")
        print(f"Karton: {res.carton_dims_cm} cm | szt/karton={res.pieces_per_carton} | waga szt={res.unit_weight_kg} kg | tare={res.carton_tare_kg} kg")
        print(f"Zapotrzebowanie: {res.demand_pieces} szt -> {res.cartons_needed} kartonów (ceil)")
        print(f"Warstwy: height={res.max_layers_by_height}, weight={res.max_layers_by_weight} -> użyte={res.layers_used}")
        print(f"Najlepszy wariant: {res.best_layout.name} | kartonów/warstwa={res.best_layout.cartons_per_layer} | util={res.best_layout.utilization_percent}%")
        print(f"Kartonów/paleta={res.cartons_per_pallet} | pełne palety={res.pallets_full} | reszta kartonów={res.remainder_cartons} | reszta szt (szac)={res.remainder_pieces_est}")
        print(f"Wysokość użyta={res.height_used_cm} cm | waga/paleta={res.weight_per_pallet_kg} kg")
        print("Warianty (TOP):")
        for o in res.all_layouts[:5]:
            print(f" - {o.name}: {o.cartons_per_layer}/warstwa, util={o.utilization_percent}%")
        print()

        results_rows.append({
            "CARTON_ID": res.carton_id,
            "PALLET": res.pallet_code,
            "PALLET_DIMS_CM": res.pallet_dims_cm,
            "CARTON_DIMS_CM": res.carton_dims_cm,
            "DEMAND_PIECES": res.demand_pieces,
            "PIECES_PER_CARTON": res.pieces_per_carton,
            "CARTONS_NEEDED": res.cartons_needed,
            "CARTON_WEIGHT_KG": res.carton_weight_kg,
            "LAYERS_USED": res.layers_used,
            "BEST_LAYOUT": res.best_layout.name,
            "CARTONS_PER_LAYER": res.best_layout.cartons_per_layer,
            "CARTONS_PER_PALLET": res.cartons_per_pallet,
            "PALLETS_FULL": res.pallets_full,
            "REMAINDER_CARTONS": res.remainder_cartons,
            "REMAINDER_PIECES_EST": res.remainder_pieces_est,
            "HEIGHT_USED_CM": res.height_used_cm,
            "WEIGHT_PER_PALLET_KG": res.weight_per_pallet_kg,
        })

        for o in res.all_layouts:
            options_rows.append({
                "CARTON_ID": res.carton_id,
                "PALLET": res.pallet_code,
                "LAYOUT": o.name,
                "CARTONS_PER_LAYER": o.cartons_per_layer,
                "UTILIZATION_PERCENT": o.utilization_percent,
                "PLACEMENTS_COUNT": len(o.placements),
            })

        if int(args.export_viz) == 1:
            safe_id = res.carton_id.replace(":", "_").replace("/", "_")
            png_path = f"output/layer_{safe_id}.png"
            html_path = f"output/pallet3d_{safe_id}.html"

            export_layer_2d_png(pallet, res.best_layout.placements, png_path)
            export_pallet_3d_html(
                pallet=pallet,
                layer_placements=res.best_layout.placements,
                carton_h_cm=int(c.dims.h_cm),
                layers_to_render=int(args.render_layers),
                out_path=html_path
            )

    pd.DataFrame(results_rows).to_csv("output/wynik.csv", index=False)
    pd.DataFrame(options_rows).to_csv("output/warianty.csv", index=False)

    print("✅ Zapisano: output/wynik.csv")
    print("✅ Zapisano: output/warianty.csv")
    if int(args.export_viz) == 1:
        print("✅ Zapisano wizualizacje: output/layer_*.png oraz output/pallet3d_*.html (bez fig.show)")


if __name__ == "__main__":
    main()
