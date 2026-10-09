#!/usr/bin/env python
"""Gate: fail if a tracked non-exempt `.py` file exceeds the line-count limit.

Convention (`.planning/codebase/CONVENTIONS.md`, "File size convention"):
- Hard limit: 500 lines per Python file under `web/` and `palletizer/`.
- Exemptions: any path with a `migrations` or `vendor` directory component,
  and the exact file `web/palletweb/settings.py` (documented exception).

STRUCT-02 compliance decision (recorded at the time this gate was added): the
real tree is clean under the limit. Three files sit close to 500 and are
deliberately left unsplit — splitting a 495-line file to satisfy a "~500"
convention is churn with no benefit:
  - web/ui/urls.py (495)
  - web/ui/tests/test_hu_control.py (495)
  - web/wh3d/views/warehouse_map_core.py (494)

Usage:
    python web/scripts/file_size_check.py

Exit code 0 = clean, 1 = offenders found (each printed as `path:count`).
"""
from __future__ import annotations

import sys
from pathlib import Path

LIMIT = 500
EXEMPT_DIR_NAMES = {"migrations", "vendor"}
EXEMPT_EXACT_SUFFIXES = ("palletweb/settings.py",)


def _is_exempt(path: Path) -> bool:
    if EXEMPT_DIR_NAMES & set(path.parts):
        return True
    posix = path.as_posix()
    return any(posix.endswith(suffix) for suffix in EXEMPT_EXACT_SUFFIXES)


def find_oversized(roots, limit: int = LIMIT, exempt=_is_exempt):
    """Return `(relative_path, line_count)` for every non-exempt `*.py` over `limit`.

    `relative_path` is relative to the root it was found under. Each root in
    `roots` is scanned recursively; missing roots are skipped silently.
    """
    offenders = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root)
            if exempt(rel):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            count = len(text.splitlines())
            if count > limit:
                offenders.append((rel.as_posix(), count))
    return offenders


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    roots = [repo_root / "web", repo_root / "palletizer"]
    offenders = find_oversized(roots)
    for rel, count in offenders:
        print(f"{rel}:{count}")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
