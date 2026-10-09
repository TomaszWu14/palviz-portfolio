"""
Konwerter eksportu SAP MARM → format importu PalViz
====================================================

Wejście: plik CSV/Excel z SAP (SE16, MM60 lub podobny) z tabelą MARM
         Kolumny: Materiał, Alternatywna jednostka miary, Mianownik, Licznik,
                  Szerokość, Wysokość, Długość, Jednostka wymiaru,
                  Waga brutto, Jednostka wagi, Kod EAN/UPC

Wyjście: plik CSV gotowy do importu w PalViz → Planner → Produkty → Importuj CSV (pełny)

Użycie:
    python sap_marm_to_palviz.py plik_sap.csv           # wejście CSV
    python sap_marm_to_palviz.py plik_sap.xlsx          # wejście Excel
    python sap_marm_to_palviz.py plik_sap.xlsx --arkusz "Sheet1"
    python sap_marm_to_palviz.py plik_sap.csv --wyjscie moj_import.csv
    python sap_marm_to_palviz.py plik_sap.csv --jednostki JU=sztuka,KAR=karton,PAZ=paleta
"""

import sys
import argparse
import csv
from pathlib import Path


# ── Mapowanie kolumn SAP (nazwy mogą się różnić w zależności od wersji) ────────

COL_ALIASES = {
    "material":   ["Materiał", "Material", "MATNR", "Materiał "],
    "unit":       ["Alternatywna jednostka miary", "Alt unit", "MEINH", "Jedn. miary"],
    "numerator":  ["Licznik", "Numerator", "UMREZ"],
    "denominator":["Mianownik", "Denominator", "UMREN"],
    "width":      ["Szerokość", "Width", "BREIT"],
    "height":     ["Wysokość", "Height", "HOEHE"],
    "length":     ["Długość", "Length", "LAENG"],
    "dim_unit":   ["Jednostka wymiaru", "Dim. unit", "MEABM"],
    "weight":     ["Waga brutto", "Gross weight", "BRGEW"],
    "weight_unit":["Jednostka wagi", "Weight unit", "GEWEI"],
    "volume":     ["Objętość", "Objetosc", "Volume", "VOLUM"],
    "volume_unit":["Jednostka objętości", "Jedn. objętości", "Volume unit", "VOLEH"],
    "ean":        ["Kod EAN/UPC", "EAN/UPC", "EAN11"],
}


def _vol_to_m3(value, unit, default=""):
    """SAP volume (often CD3 = dm³) → m³. Empty/zero → default."""
    v = _val_float(value, 0.0)
    if v <= 0:
        return default
    u = (str(unit) or "").strip().upper()
    if u in ("M3", "M³", "CBM", "MTQ"):
        factor = 1.0
    elif u in ("CM3", "CM³", "CCM", "ML"):
        factor = 1e-6
    else:                       # CD3 / DM3 / L / litry — SAP default is dm³
        factor = 0.001
    return round(v * factor, 6)

# Jakie wartości w kolumnie "jednostka" oznaczają dany poziom
# Możesz nadpisać przez --jednostki JU=szt,KAR=karton,PAZ=pal
DEFAULT_UNIT_MAP = {
    "base":  ["JU", "ST", "SZT", "PC", "PCS", "EA"],   # sztuka bazowa
    "piece": ["OP", "OPA", "OPK", "PCE"],               # opakowanie handlowe (=1 szt)
    "carton":["KAR", "KRT", "CTN", "CS", "BOX"],        # karton
    "pallet":["PAZ", "PAL", "PLT", "PL"],               # paleta
}

# Domyślne wartości instrukcji jeśli PAZ nie ma wymiarów
DEFAULT_MAX_HEIGHT_CM = 215
DEFAULT_MAX_WEIGHT_KG = 1000
DEFAULT_PALLET_CODE   = "EU"


def _find_col(df_cols, aliases):
    """Znajdź kolumnę po liście aliasów (case-insensitive)."""
    cols_lower = {c.strip().lower(): c for c in df_cols}
    for alias in aliases:
        match = cols_lower.get(alias.strip().lower())
        if match:
            return match
    return None


def _resolve_columns(df_cols):
    resolved = {}
    for key, aliases in COL_ALIASES.items():
        col = _find_col(df_cols, aliases)
        if col:
            resolved[key] = col
    return resolved


def _val_float(v, default=0.0):
    if v is None or str(v).strip() in ("", "-", "nan"):
        return default
    try:
        return float(str(v).strip().replace(",", "."))
    except ValueError:
        return default


def _val_int(v, default=0):
    f = _val_float(v)
    return int(f) if f else default


def _classify_unit(unit_val: str, unit_map: dict) -> str:
    """Zwraca 'base', 'piece', 'carton', 'pallet' lub None."""
    u = unit_val.strip().upper()
    for role, codes in unit_map.items():
        if u in [c.upper() for c in codes]:
            return role
    return None


def convert(rows: list[dict], col: dict, unit_map: dict) -> list[dict]:
    """
    rows  — lista wierszy (dict) z pliku SAP
    col   — mapowanie klucz→nazwa kolumny
    unit_map — mapowanie roli→lista kodów jednostek
    Zwraca listę wierszy w formacie PalViz.
    """
    # Zgrupuj wiersze po materiale
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        mat = str(row.get(col.get("material", ""), "")).strip()
        if mat:
            groups[mat].append(row)

    output = []
    skipped = []

    for mat, mat_rows in groups.items():
        # Klasyfikuj wiersze
        classified = {}
        unrecognized = []
        for row in mat_rows:
            unit_raw = str(row.get(col.get("unit", ""), "")).strip()
            role = _classify_unit(unit_raw, unit_map)
            if role:
                classified[role] = row  # ostatni wygrywa jeśli duplikat
            else:
                unrecognized.append(unit_raw)

        r_piece  = classified.get("piece")
        r_carton = classified.get("carton")
        r_pallet = classified.get("pallet")
        r_base   = classified.get("base")

        if not r_carton:
            skipped.append(f"{mat}: brak wiersza KAR/kartonu — pominięto.")
            continue

        # ── Waga jednostkowa ──────────────────────────────────────────────────
        # Preferuj wagę z OP (opakowanie handlowe = 1 sztuka).
        # Jeśli brak OP, oblicz z KAR: waga_kar / licznik_kar.
        if r_piece:
            unit_weight = _val_float(r_piece.get(col.get("weight", ""), 0))
        else:
            kar_weight   = _val_float(r_carton.get(col.get("weight", ""), 0))
            kar_numerator = _val_float(r_carton.get(col.get("numerator", ""), 1)) or 1
            unit_weight  = round(kar_weight / kar_numerator, 6)

        # ── Wymiary sztuki (z OP) ─────────────────────────────────────────────
        unit_l = unit_w = unit_h = ""
        if r_piece:
            unit_l = _val_float(r_piece.get(col.get("length", ""), 0)) or ""
            unit_w = _val_float(r_piece.get(col.get("width",  ""), 0)) or ""
            unit_h = _val_float(r_piece.get(col.get("height", ""), 0)) or ""

        # ── EAN ───────────────────────────────────────────────────────────────
        product_ean = ""
        carton_ean  = ""
        if r_piece:
            product_ean = str(r_piece.get(col.get("ean", ""), "")).strip()
        if r_carton:
            carton_ean  = str(r_carton.get(col.get("ean", ""), "")).strip()

        # ── Karton ───────────────────────────────────────────────────────────
        kar_num  = _val_float(r_carton.get(col.get("numerator",   ""), 1))
        kar_den  = _val_float(r_carton.get(col.get("denominator", ""), 1)) or 1
        pcs_per_carton = max(1, round(kar_num / kar_den))

        carton_l = _val_float(r_carton.get(col.get("length", ""), 0))
        carton_w = _val_float(r_carton.get(col.get("width",  ""), 0))
        carton_h = _val_float(r_carton.get(col.get("height", ""), 0))

        # Tara = waga brutto kartonu − (waga szt × liczba szt)
        kar_gross = _val_float(r_carton.get(col.get("weight", ""), 0))
        carton_tare = max(0.0, round(kar_gross - unit_weight * pcs_per_carton, 4))

        carton_name = f"Karton {mat} {int(carton_l)}x{int(carton_w)}x{int(carton_h)}"

        # ── Objętość 1 szt/OP (wprost z MARM) — z wiersza OP; fallback: obj. kartonu/szt ──
        unit_vol_m3 = ""
        if r_piece:
            unit_vol_m3 = _vol_to_m3(r_piece.get(col.get("volume", ""), 0),
                                     r_piece.get(col.get("volume_unit", ""), ""))
        if unit_vol_m3 == "" and r_carton:
            kar_vol = _vol_to_m3(r_carton.get(col.get("volume", ""), 0),
                                 r_carton.get(col.get("volume_unit", ""), ""))
            if kar_vol:
                unit_vol_m3 = round(kar_vol / pcs_per_carton, 6)

        # ── Instrukcja — z wiersza PAZ ────────────────────────────────────────
        max_height = DEFAULT_MAX_HEIGHT_CM
        max_weight = DEFAULT_MAX_WEIGHT_KG
        demand_pcs = ""
        pallet_code = DEFAULT_PALLET_CODE

        if r_pallet:
            pal_h = _val_float(r_pallet.get(col.get("height", ""), 0))
            pal_w_gross = _val_float(r_pallet.get(col.get("weight", ""), 0))
            if pal_h > 0:
                max_height = int(pal_h)
            if pal_w_gross > 0:
                max_weight = int(pal_w_gross)
            # Zapotrzebowanie oblicz z licznika PAZ (łączna liczba szt na palecie)
            pal_num = _val_float(r_pallet.get(col.get("numerator", ""), 0))
            pal_den = _val_float(r_pallet.get(col.get("denominator", ""), 1)) or 1
            demand_pcs = max(0, round(pal_num / pal_den))

        out_row = {
            # Produkt
            "product_code":    mat,
            "product_name":    mat,
            "product_ean":     product_ean,
            "product_description": "",
            "unit_l_cm":       unit_l,
            "unit_w_cm":       unit_w,
            "unit_h_cm":       unit_h,
            # Opakowanie zbiorcze — brak w tym eksporcie (OP=1 szt)
            "inner_name":      "",
            "inner_l_cm":      "",
            "inner_w_cm":      "",
            "inner_h_cm":      "",
            "inner_units_per_pack": "",
            "inner_tare_kg":   "",
            "sales_unit_l_cm": "",
            "sales_unit_w_cm": "",
            "sales_unit_h_cm": "",
            "sales_units_per_pack": "",
            # Karton
            "carton_name":     carton_name,
            "carton_ean":      carton_ean,
            "carton_l_cm":     int(carton_l) if carton_l else "",
            "carton_w_cm":     int(carton_w) if carton_w else "",
            "carton_h_cm":     int(carton_h) if carton_h else "",
            "unit_weight_kg":  unit_weight,
            "pieces_per_carton": pcs_per_carton,
            "carton_tare_kg":  carton_tare,
            "unit_volume_m3":  unit_vol_m3,
            "packs_per_carton": "",
            # Instrukcja
            "pallet_code":     pallet_code,
            "max_height_cm":   max_height,
            "max_weight_kg":   max_weight,
            "demand_pcs":      demand_pcs,
        }
        output.append(out_row)

    if skipped:
        print(f"\nPominięte materiały ({len(skipped)}):", file=sys.stderr)
        for s in skipped[:20]:
            print(f"  {s}", file=sys.stderr)

    return output


PALVIZ_COLUMNS = [
    "product_code", "product_name", "product_ean", "product_description",
    "unit_l_cm", "unit_w_cm", "unit_h_cm",
    "inner_name", "inner_l_cm", "inner_w_cm", "inner_h_cm",
    "inner_units_per_pack", "inner_tare_kg",
    "sales_unit_l_cm", "sales_unit_w_cm", "sales_unit_h_cm", "sales_units_per_pack",
    "carton_name", "carton_ean", "carton_l_cm", "carton_w_cm", "carton_h_cm",
    "unit_weight_kg", "pieces_per_carton", "carton_tare_kg", "unit_volume_m3", "packs_per_carton",
    "pallet_code", "max_height_cm", "max_weight_kg", "demand_pcs",
]


def load_file(path: Path, sheet: str = None) -> tuple[list[dict], list[str]]:
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls", ".xlsm"):
        try:
            import openpyxl
        except ImportError:
            print("Zainstaluj openpyxl: pip install openpyxl", file=sys.stderr)
            sys.exit(1)
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb[sheet] if sheet else wb.active
        rows_raw = list(ws.iter_rows(values_only=True))
        if not rows_raw:
            return [], []
        headers = [str(c).strip() if c is not None else "" for c in rows_raw[0]]
        rows = [dict(zip(headers, row)) for row in rows_raw[1:] if any(v for v in row)]
        return rows, headers
    else:
        # CSV — spróbuj różnych encodingów
        for enc in ("utf-8-sig", "cp1250", "iso-8859-2", "utf-8"):
            try:
                text = path.read_text(encoding=enc)
                # Spróbuj wykryć separator (; lub ,)
                first = text.split("\n")[0]
                sep = ";" if first.count(";") > first.count(",") else ","
                reader = csv.DictReader(text.splitlines(), delimiter=sep)
                rows = list(reader)
                headers = reader.fieldnames or []
                return rows, list(headers)
            except UnicodeDecodeError:
                continue
        print("Nie udało się wczytać pliku — sprawdź encoding.", file=sys.stderr)
        sys.exit(1)


def _round_to_n(value: float, n: int) -> int:
    """Round value to nearest n."""
    return int(round(value / n) * n)


def unify_cartons(output: list[dict], zaokraglij: int, tolerancja: int) -> list[dict]:
    """
    Group cartons with similar dimensions and unify them.

    zaokraglij: round each carton's dimensions to nearest N cm, group by rounded dims.
    tolerancja: additionally group cartons where all three dims are within N cm (greedy clustering).

    Prints a report to stderr.
    Returns modified output list with unified carton_name values.
    """
    from collections import defaultdict

    # Step 1: round dims
    for row in output:
        row["_rl"] = _round_to_n(float(row["carton_l_cm"] or 0), zaokraglij)
        row["_rw"] = _round_to_n(float(row["carton_w_cm"] or 0), zaokraglij)
        row["_rh"] = _round_to_n(float(row["carton_h_cm"] or 0), zaokraglij)

    # Step 2: group by rounded dims (exact rounding groups)
    rounded_groups: dict[tuple, list] = defaultdict(list)
    for row in output:
        key = (row["_rl"], row["_rw"], row["_rh"])
        rounded_groups[key].append(row)

    # Step 3: tolerance clustering (greedy centroid proximity)
    # Build initial centroids from rounded groups
    centroids: list[tuple[float, float, float]] = []
    centroid_members: list[list] = []

    for key, members in rounded_groups.items():
        rl, rw, rh = key
        # Try to merge into existing centroid within tolerancja
        merged = False
        for i, (cl, cw, ch) in enumerate(centroids):
            if (abs(rl - cl) <= tolerancja and
                    abs(rw - cw) <= tolerancja and
                    abs(rh - ch) <= tolerancja):
                centroid_members[i].extend(members)
                # Update centroid to average of all members
                all_m = centroid_members[i]
                centroids[i] = (
                    sum(float(m["carton_l_cm"] or 0) for m in all_m) / len(all_m),
                    sum(float(m["carton_w_cm"] or 0) for m in all_m) / len(all_m),
                    sum(float(m["carton_h_cm"] or 0) for m in all_m) / len(all_m),
                )
                merged = True
                break
        if not merged:
            centroids.append((float(rl), float(rw), float(rh)))
            centroid_members.append(list(members))

    # Step 4: for each cluster, assign canonical name and averaged values, print report
    unified_count = 0
    print("\n── Raport unifikacji kartonów ──────────────────────────────", file=sys.stderr)

    for cluster_members in centroid_members:
        if not cluster_members:
            continue

        # Compute canonical rounded dims from the first member's rounded values
        # (use the cluster's rounded centroid)
        repr_row = cluster_members[0]
        canon_l = repr_row["_rl"]
        canon_w = repr_row["_rw"]
        canon_h = repr_row["_rh"]
        canon_name = f"Karton {canon_l}x{canon_w}x{canon_h}"

        # Average numeric fields
        def _avg(field):
            vals = [float(r[field]) for r in cluster_members if r.get(field) not in ("", None)]
            return round(sum(vals) / len(vals), 6) if vals else 0.0

        avg_uw = _avg("unit_weight_kg")
        avg_pcs = max(1, round(_avg("pieces_per_carton")))
        avg_tare = _avg("carton_tare_kg")

        if len(cluster_members) >= 2:
            unified_count += 1
            mat_codes = [r["product_code"] for r in cluster_members]
            print(
                f"  Grupa [{canon_name}] — {len(cluster_members)} materiałów: "
                + ", ".join(mat_codes[:10])
                + (" …" if len(mat_codes) > 10 else ""),
                file=sys.stderr,
            )

        for row in cluster_members:
            row["carton_name"] = canon_name
            row["unit_weight_kg"] = avg_uw
            row["pieces_per_carton"] = avg_pcs
            row["carton_tare_kg"] = avg_tare

    if unified_count == 0:
        print("  Brak grup do unifikacji — każdy karton ma unikalne wymiary.", file=sys.stderr)
    else:
        print(f"\n  Łącznie zunifikowanych grup: {unified_count}", file=sys.stderr)
    print("────────────────────────────────────────────────────────────", file=sys.stderr)

    # Clean up temp keys
    for row in output:
        row.pop("_rl", None)
        row.pop("_rw", None)
        row.pop("_rh", None)

    return output


def main():
    parser = argparse.ArgumentParser(description="Konwerter SAP MARM → PalViz CSV")
    parser.add_argument("plik", help="Plik wejściowy SAP (.csv lub .xlsx)")
    parser.add_argument("--wyjscie", "-o", default=None, help="Plik wyjściowy (domyślnie: <plik>_palviz.csv)")
    parser.add_argument("--arkusz", default=None, help="Nazwa arkusza Excel (domyślnie: pierwszy)")
    parser.add_argument(
        "--jednostki", default=None,
        help="Mapowanie jednostek np. JU=base,KAR=carton,PAZ=pallet. "
             "Role: base, piece, carton, pallet"
    )
    parser.add_argument(
        "--zaokraglij", type=int, default=1, metavar="N",
        help="Zaokrąglij wymiary kartonów do najbliższego N cm i grupuj identyczne (domyślnie: 1)"
    )
    parser.add_argument(
        "--tolerancja", type=int, default=None, metavar="N",
        help="Grupuj kartony, których wszystkie wymiary różnią się o ≤ N cm (greedy clustering). "
             "Domyślnie: wartość --zaokraglij"
    )
    args = parser.parse_args()

    path = Path(args.plik)
    if not path.exists():
        print(f"Plik nie istnieje: {path}", file=sys.stderr)
        sys.exit(1)

    # Parsuj nadpisanie jednostek
    unit_map = {k: list(v) for k, v in DEFAULT_UNIT_MAP.items()}
    if args.jednostki:
        for pair in args.jednostki.split(","):
            if "=" in pair:
                code, role = pair.split("=", 1)
                role = role.strip().lower()
                code = code.strip().upper()
                if role in unit_map:
                    unit_map[role].append(code)

    zaokraglij = max(1, args.zaokraglij)
    tolerancja = max(1, args.tolerancja) if args.tolerancja is not None else zaokraglij

    print(f"Wczytuję: {path}")
    rows, headers = load_file(path, sheet=args.arkusz)
    print(f"  Wierszy: {len(rows)}, kolumn: {len(headers)}")

    col = _resolve_columns(headers)
    missing = [k for k in ["material", "unit", "weight"] if k not in col]
    if missing:
        print(f"Nie znaleziono kolumn: {missing}", file=sys.stderr)
        print(f"Dostępne kolumny: {headers}", file=sys.stderr)
        sys.exit(1)

    print("Konwertuję...")
    output = convert(rows, col, unit_map)
    print(f"  Skonwertowanych materiałów: {len(output)}")

    print(f"Unifikuję kartony (zaokrąglij={zaokraglij} cm, tolerancja={tolerancja} cm)...")
    output = unify_cartons(output, zaokraglij, tolerancja)

    out_path = Path(args.wyjscie) if args.wyjscie else path.with_name(path.stem + "_palviz.csv")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=PALVIZ_COLUMNS)
        writer.writeheader()
        writer.writerows(output)

    print(f"\nGotowe! Plik zapisany: {out_path}")
    print("Wgraj go w PalViz → Planner → Produkty → Importuj CSV (pełny)")


if __name__ == "__main__":
    main()
