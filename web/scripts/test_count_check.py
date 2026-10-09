#!/usr/bin/env python
"""Gate: fail if the collected Django test count drops below the committed floor.

Convention (TEST-02, `.planning/phases/06-testy-per-app/06-CONTEXT.md` D-06): a
permanent CI guard in "floor" shape — the CI test step's combined
`ui.tests wh3d.tests huctl.tests transport.tests` run is `tee`'d to a log file,
and this script parses that log for the final `Ran N tests` line and compares it
against the integer committed in the sibling `test_count_baseline.txt`. New tests
never require a change here; only an intentional baseline bump does (a one-line
diff to `test_count_baseline.txt`).

Does NOT re-run the test suite — parses the log from the run that already
happened (running the ~1500-test suite twice per CI job would double its
wall-clock time for no benefit).

Usage:
    python web/scripts/test_count_check.py <path-to-tee'd-test-log>

Exit code 0 = count >= baseline, 1 = regression (or the count could not be found).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RAN_RE = re.compile(r"Ran (\d+) tests?")


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python web/scripts/test_count_check.py <path-to-test-log>")
        return 1
    log_path = Path(sys.argv[1])
    log = log_path.read_text(encoding="utf-8", errors="replace")
    m = RAN_RE.search(log)
    if not m:
        print("Could not find 'Ran N tests' in test output — treat as failure.")
        return 1
    count = int(m.group(1))
    baseline_path = Path(__file__).parent / "test_count_baseline.txt"
    baseline = int(baseline_path.read_text().strip())
    if count < baseline:
        print(f"Test count regression: {count} < baseline {baseline}")
        return 1
    print(f"Test count OK: {count} >= baseline {baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
