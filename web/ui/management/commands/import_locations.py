"""Import warehouse locations from a SAP-style CSV into a WarehouseLayout + Master.

CSV format (semicolon-delimited, UTF-8 BOM, decimal comma):
    Adres lokalizacji;Poziom;Typ magazynu;Wysokość [cm];Max objętość [m³];Max waga [kg]
    B0-01-100A;1;0052;235;2,1;500

Notes
-----
* The physical LEVEL is derived from the location-code suffix (the "Poziom" column
  in real exports is partially corrupted by a column shift), using the single source of
  truth ``ui.views.core.ewm_levels.letter_slot`` (zone-aware: hall A letters A–E = 1–5).
* Grid positions for the 2D editor are derived from the code: each aisle gets its
  own row band, each stack ONE column (B/C/D shelves and G/H parts stack vertically).
* Codes are 3-part (B0-01-100A) or carry a sub-shelf suffix (B0-07-300C-1).

Usage:
    python manage.py import_locations                 # bundled file, becomes active
    python manage.py import_locations path/to.csv --name "B0" --keep-others
"""
import csv
import io
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from ui.models import (
    WarehouseLayout, WarehouseLayoutCell,
    WarehouseLocationMasterBatch, WarehouseLocationMaster,
)

# Suffix → physical level for hall B — kept as a fallback/compat table; the importer uses
# ewm_levels.letter_slot (zone-aware). A/B/C/D shelves at level 1, X/G/H = 2, Y = 3,
# Z = 4, compact S–W = 1–5.
SUFFIX_LEVEL = {
    'A': 1, 'B': 1, 'C': 1, 'D': 1,
    'C-1': 1, 'C-2': 1, 'D-1': 1, 'D-2': 1,
    'S': 1, 'T': 2, 'U': 3, 'V': 4, 'W': 5,
    'G': 2, 'H': 2,
    'X': 2, 'Y': 3, 'Z': 4,
}

# Suffix → horizontal column slot within a stack footprint (for the 2D top-down grid).
# Every EWM letter is a level/shelf of ONE stack → slot 0 (halves -1/-2 share the cell).
COL_IDX = {suf: 0 for suf in SUFFIX_LEVEL}

# Wspólne źródło prawdy formatu kodu (to samo, co skaner PHV).
from ui.location_codes import LOCATION_CODE_RE as CODE_RE  # noqa: E402
from ui.views.core.ewm_levels import letter_slot  # noqa: E402

DEFAULT_CSV = Path(__file__).resolve().parents[2] / "data" / "warehouse_locations_b0.csv"
DEFAULT_MAP = Path(__file__).resolve().parents[2] / "data" / "warehouse_map_b0.xlsx"


def parse_map_xlsx(path):
    """Read the physical-layout xlsx and return a per-stack anchor:
        {(aisle, stack): (anchor_row, anchor_col)}

    The MAP draws each stack at one column with its levels stacked across rows;
    we take the column as the X anchor and the floor (max) row as the Y anchor,
    so every code of a stack is placed exactly where the spreadsheet puts it.
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    cells = {}  # (aisle, stack) -> list of (row, col)
    for row in ws.iter_rows():
        for c in row:
            if c.value is None:
                continue
            m = CODE_RE.match(str(c.value).strip())
            if not m:
                continue
            key = (m.group(2).zfill(2), m.group(3))
            cells.setdefault(key, []).append((c.row, c.column))
    from collections import Counter
    anchors = {}
    for key, pts in cells.items():
        # most common column = the stack's X; floor row = max row (levels stack upward)
        anchor_col = Counter(p[1] for p in pts).most_common(1)[0][0]
        anchor_row = max(p[0] for p in pts)
        anchors[key] = (anchor_row, anchor_col)
    return anchors



def _num(value, default=0.0):
    """Parse a possibly comma-decimal number; return default on bad/empty input."""
    s = str(value or "").strip().replace(",", ".")
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def parse_rows(text):
    """Yield parsed dicts from raw CSV text. Skips unparseable / blank rows."""
    reader = csv.reader(io.StringIO(text), delimiter=';')
    rows = list(reader)
    for r in rows[1:]:                       # skip header
        if not r or not r[0].strip():
            continue
        code = r[0].strip().upper()
        m = CODE_RE.match(code)
        if not m:
            continue
        zone, aisle, stack, suffix = m.groups()
        suffix = suffix.upper()
        slot = letter_slot(zone, suffix)
        yield {
            "code": code,
            "zone": zone,
            "aisle": aisle.zfill(2),
            "stack": stack,
            "suffix": suffix,
            "level": slot.level if slot else SUFFIX_LEVEL.get(suffix, 1),
            "col_idx": 0 if slot else COL_IDX.get(suffix, 0),
            "wh_type": (r[2].strip() if len(r) > 2 else ""),
            "height_mm": int(round(_num(r[3] if len(r) > 3 else 0) * 10)),   # cm → mm
            "max_volume_m3": _num(r[4] if len(r) > 4 else 0),
            "max_weight_kg": _num(r[5] if len(r) > 5 else 0),
        }


def build_grid(parsed):
    """Assign grid_row (per aisle band) and grid_col (per stack block + col slot)."""
    aisles = sorted({p["aisle"] for p in parsed})
    aisle_row = {a: i for i, a in enumerate(aisles)}
    max_slot = max((p["col_idx"] for p in parsed), default=0) + 1   # block width per stack
    # stacks ordered numerically within each aisle
    stacks_by_aisle = {}
    for p in parsed:
        stacks_by_aisle.setdefault(p["aisle"], set()).add(int(p["stack"]))
    stack_col = {}
    for a, stacks in stacks_by_aisle.items():
        for si, s in enumerate(sorted(stacks)):
            stack_col[(a, s)] = si * (max_slot + 1)
    for p in parsed:
        p["grid_row"] = aisle_row[p["aisle"]]
        p["grid_col"] = stack_col[(p["aisle"], int(p["stack"]))] + p["col_idx"]
    return parsed


def resolve_positions(parsed, anchors):
    """Override computed positions with the Excel plan, keeping each aisle in ONE
    coordinate space.

    A stack present in the plan uses its real (row, col). A stack missing from the
    plan but in a partially-mapped aisle is placed next to the nearest mapped stack
    of the SAME aisle (offset by stack-number difference), so an aisle never mixes
    plan-space (rows up to ~400) with the schematic grid. Aisles with no mapped
    stack at all keep the computed schematic positions from build_grid().

    Returns the number of stacks positioned from / relative to the plan.
    """
    from collections import defaultdict
    stacks_by_aisle = defaultdict(set)
    for p in parsed:
        stacks_by_aisle[p["aisle"]].add(int(p["stack"]))

    stack_anchor = {}   # (aisle, stack:int) -> (row, col) in plan space
    for aisle, stacks in stacks_by_aisle.items():
        mapped = {s: anchors[(aisle, str(s))] for s in stacks if (aisle, str(s)) in anchors}
        if not mapped:
            continue                      # whole aisle unmapped → computed fallback
        mapped_nums = sorted(mapped)
        for s in stacks:
            if s in mapped:
                stack_anchor[(aisle, s)] = mapped[s]
            else:
                nearest = min(mapped_nums, key=lambda ms: abs(ms - s))
                r, c = mapped[nearest]
                stack_anchor[(aisle, s)] = (r, c + (s - nearest))

    placed = 0
    for p in parsed:
        a = stack_anchor.get((p["aisle"], int(p["stack"])))
        if a:
            p["grid_row"], p["grid_col"] = a[0], a[1] + p["col_idx"]
            placed += 1
    return placed


class Command(BaseCommand):
    help = "Import warehouse locations from a SAP-style CSV into a WarehouseLayout + Master."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(DEFAULT_CSV),
                            help="CSV path (default: bundled warehouse_locations_b0.csv)")
        parser.add_argument("--name", default="B0 — import lokalizacji",
                            help="Layout/master name")
        parser.add_argument("--map", dest="map_path", default=str(DEFAULT_MAP),
                            help="Physical-layout xlsx for exact positions (default: bundled). "
                                 "Pass '' to disable and use the computed schematic grid.")
        parser.add_argument("--keep-others", action="store_true",
                            help="Do not deactivate existing layouts/masters")
        parser.add_argument("--width-mm", type=int, default=800)
        parser.add_argument("--depth-mm", type=int, default=1100)

    @transaction.atomic
    def handle(self, *args, **opts):
        path = Path(opts["path"])
        if not path.exists():
            self.stderr.write(self.style.ERROR(f"Plik nie istnieje: {path}"))
            return
        text = path.read_text(encoding="utf-8-sig")
        parsed = list(parse_rows(text))
        if not parsed:
            self.stderr.write(self.style.ERROR("Brak parsowalnych lokalizacji w pliku."))
            return

        # Drop duplicate codes explicitly — bulk_create(ignore_conflicts=True) would
        # otherwise swallow them silently and under-report the count.
        seen, deduped = set(), []
        for p in parsed:
            if p["code"] in seen:
                continue
            seen.add(p["code"])
            deduped.append(p)
        dups = len(parsed) - len(deduped)
        if dups:
            self.stdout.write(self.style.WARNING(f"Pominięto {dups} zdublowanych kodów (pierwsze wystąpienie zachowane)."))
        parsed = deduped

        build_grid(parsed)   # computed fallback positions (used for unmapped aisles)

        # Exact spreadsheet positions ("map dokładnie jak w Excelu") where available,
        # keeping every aisle in a single coordinate space (no plan/schematic mix).
        mapped = 0
        map_path = opts.get("map_path")
        if map_path and Path(map_path).exists():
            mapped = resolve_positions(parsed, parse_map_xlsx(map_path))

        name = opts["name"]
        # Re-runnable: drop any previous import with the same name first.
        WarehouseLayout.objects.filter(name=name).delete()
        WarehouseLocationMasterBatch.objects.filter(name=name).delete()

        layout = WarehouseLayout.objects.create(name=name)
        batch = WarehouseLocationMasterBatch.objects.create(name=name)
        if not opts["keep_others"]:
            WarehouseLayout.objects.exclude(pk=layout.pk).update(is_active=False)
            WarehouseLocationMasterBatch.objects.exclude(pk=batch.pk).update(is_active=False)
        layout.is_active = True
        batch.is_active = True
        layout.save()
        batch.save()

        cells = [
            WarehouseLayoutCell(layout=layout, location_code=p["code"],
                                grid_row=p["grid_row"], grid_col=p["grid_col"], level=p["level"])
            for p in parsed
        ]
        masters = [
            WarehouseLocationMaster(
                batch=batch, location_code=p["code"], level=p["level"],
                warehouse_type=p["wh_type"], height_mm=p["height_mm"],
                width_mm=opts["width_mm"], depth_mm=opts["depth_mm"],
                max_volume_m3=p["max_volume_m3"], max_weight_kg=p["max_weight_kg"],
            )
            for p in parsed
        ]
        WarehouseLayoutCell.objects.bulk_create(cells, batch_size=2000, ignore_conflicts=True)
        WarehouseLocationMaster.objects.bulk_create(masters, batch_size=2000, ignore_conflicts=True)

        layout.location_count = layout.cells.count()
        layout.save(update_fields=["location_count"])
        batch.location_count = batch.locations.count()
        batch.save(update_fields=["location_count"])

        aisles = len({p["aisle"] for p in parsed})
        pos_note = (f" Pozycje z Excela: {mapped}/{len(parsed)} (reszta wyliczona)."
                    if mapped else " Pozycje wyliczone (schemat).")
        self.stdout.write(self.style.SUCCESS(
            f"Zaimportowano {layout.location_count} lokalizacji "
            f"({aisles} alej) do layoutu «{name}» (aktywny). "
            f"Master: {batch.location_count}.{pos_note}"
        ))
