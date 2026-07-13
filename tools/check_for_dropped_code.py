"""
**Did this change DELETE something?**

    python tools/check_for_dropped_code.py <baseline.zip|baseline_dir>

Why this exists
---------------
Gable, after the spurious-puncta incident:

    *"how do we make sure you don't throw away good code while doing these audits — the rationale
    was even in the code and you dropped it. We need some mechanism in this workflow to track these
    drops, because for all I know every module we've validated has truncated features away."*

**The concern is exactly right, and the failure mode is real**, because of how the edits are made:
**every edit is a whole-file rewrite.** There is no diff, no merge, no three-way. If a rewrite emits
fewer lines than it read, **the difference is simply gone** — and:

* the file still **compiles**
* every test still **passes**
* the function still **exists**, just with fewer parameters

***A capability can disappear and nothing anywhere notices.***

What this tool does
-------------------
It compares a baseline against the working tree and reports **only deletions** — the direction that
a rewrite can silently take and a test suite cannot see:

* **functions that vanished**
* **parameters that vanished** *(a lost parameter is a lost capability — ``punctate_gate``
  disappearing is not a refactor)*
* **functions that shrank by more than a third** *(the signature of a truncated rewrite)*

**Additions are not reported.** Growth is not the failure mode.

Every hit is a **question, not a verdict.** Moving a function to another module is a legitimate
deletion — that is what happened to the five stack helpers in 1.5.517, and it was fine because
``file_io`` re-exports them. *The tool's job is to make sure the question gets asked.*
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tempfile
import zipfile


_SHRINK_THRESHOLD = 0.70          # a function keeping < 70% of its lines is suspicious


def _signatures(root: pathlib.Path):
    """Every function in the tree: its length, and its parameter names."""
    found = {}

    for path in root.rglob("*.py"):
        if 'src' not in path.parts or 'pycat' not in path.parts:
            continue

        try:
            tree = ast.parse(path.read_text(encoding='utf-8', errors='ignore'))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue

            key = f"{path.name}::{node.name}"
            found[key] = dict(
                lines=(node.end_lineno or node.lineno) - node.lineno,
                params=set(a.arg for a in node.args.args + node.args.kwonlyargs),
            )

    return found


def _load(source: str) -> pathlib.Path:
    """A directory, or a zip that will be unpacked into one."""
    path = pathlib.Path(source)

    if path.is_dir():
        return path

    if path.suffix == '.zip':
        temporary = pathlib.Path(tempfile.mkdtemp(prefix='pycat_baseline_'))
        with zipfile.ZipFile(path) as archive:
            archive.extractall(temporary)
        return temporary

    raise SystemExit(f"{source} is neither a directory nor a .zip")


def _high_water_mark(sources):
    """**The BEST each function has EVER been — not whatever the last snapshot contained.**

    A first version of this tool diffed the tree against the most recent zip. It reported
    **"nothing dropped"** while the punctate gate was **entirely missing** — because *the baseline
    was itself regressed.* 1.5.517 had already lost it.

    ***A tool that compares against a broken baseline reports ALL CLEAR while everything is
    gone.*** That is the same failure it exists to prevent, one level up.

    So the baseline is the **union across every snapshot**: for each function, the **largest
    parameter set** and the **longest body** it has ever had. A capability that disappeared three
    versions ago is **still missing today**, and this still says so.
    """
    best = {}

    for source in sources:
        for key, entry in _signatures(_load(source)).items():
            if key not in best:
                best[key] = dict(lines=entry['lines'], params=set(entry['params']),
                                 seen_in=[source])
                continue

            best[key]['lines'] = max(best[key]['lines'], entry['lines'])
            best[key]['params'] |= entry['params']
            best[key]['seen_in'].append(source)

    return best


def main(baseline_source: str, current_source: str = '.') -> int:
    # A comma-separated list of snapshots builds a HIGH-WATER MARK. A single one is just a diff —
    # and a diff against a regressed baseline is blind.
    sources = [s.strip() for s in baseline_source.split(',') if s.strip()]

    baseline = _high_water_mark(sources)
    current = _signatures(_load(current_source))

    if len(sources) == 1:
        print("  *** WARNING: ONE baseline. If that snapshot is itself regressed, this tool is")
        print("      BLIND to the regression. Pass every snapshot you have, comma-separated,")
        print("      and it will compare against the best each function has EVER been.\n")

    print(f"  baseline: {len(baseline)} functions   (high-water mark of {len(sources)} snapshot(s))")
    print(f"  current : {len(current)} functions\n")

    # ── 1. Functions that VANISHED ──────────────────────────────────────────────
    #
    # A function that was there and is not is either a deliberate move or a truncated rewrite.
    # **The tool cannot tell which, and it should not try.** It asks.
    vanished = sorted(k for k in set(baseline) - set(current)
                      if not k.split('::')[1].startswith('__'))

    # ── 2. Parameters that VANISHED ─────────────────────────────────────────────
    #
    # **This is the one that caught nobody.** `segment_subcellular_objects` lost `punctate_gate`,
    # `image_stats`, `punctate_gate_sigma` and `punctate_gate_abs_sigma` — four safety parameters —
    # and the code compiled, the tests passed, and spurious puncta came back.
    lost_parameters = []
    for key in sorted(set(baseline) & set(current)):
        lost = baseline[key]['params'] - current[key]['params']
        if lost:
            lost_parameters.append((key, sorted(lost)))

    # ── 3. Functions that SHRANK ────────────────────────────────────────────────
    #
    # The signature of a truncated rewrite: the function survives, its parameters survive, and its
    # BODY is a third shorter. `cell_mask_stretching` went from 146 lines to 85 and lost its gain
    # ceiling.
    shrank = []
    for key in sorted(set(baseline) & set(current)):
        was, now = baseline[key]['lines'], current[key]['lines']
        if was >= 25 and now < was * _SHRINK_THRESHOLD:
            shrank.append((key, was, now))

    if vanished:
        print("  === FUNCTIONS THAT VANISHED ===\n")
        for key in vanished:
            print(f"    {key}   ({baseline[key]['lines']} lines)")
        print()

    if lost_parameters:
        print("  === PARAMETERS THAT VANISHED ===")
        print("      (a lost parameter is a lost CAPABILITY, not a refactor)\n")
        for key, lost in lost_parameters:
            print(f"    {key}")
            print(f"        LOST: {lost}")
        print()

    if shrank:
        print(f"  === FUNCTIONS THAT SHRANK BY MORE THAN "
              f"{int((1 - _SHRINK_THRESHOLD) * 100)}% ===")
        print("      (the signature of a truncated rewrite)\n")
        for key, was, now in shrank:
            print(f"    {key}:  {was} -> {now} lines  (-{100 * (was - now) // was}%)")
        print()

    total = len(vanished) + len(lost_parameters) + len(shrank)

    print("=" * 74)
    if total == 0:
        print("  NOTHING WAS DROPPED. Every function, parameter and body survived.")
        print("=" * 74)
        return 0

    print(f"  {total} POSSIBLE DROPS. **Each is a question, not a verdict.**")
    print()
    print("  A legitimate deletion looks exactly like an accidental one. Moving a function to")
    print("  another module is fine — that is what happened to the five stack helpers in 1.5.517,")
    print("  and `file_io` re-exports them. **The tool's job is to make sure the question gets")
    print("  asked**, not to answer it.")
    print()
    print("  For each hit: was this deliberate? If it was a move, does the old import still work?")
    print("  If it was a rewrite, did the rationale in the deleted code survive somewhere?")
    print("=" * 74)
    return 1


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(
            "usage: python tools/check_for_dropped_code.py <baseline.zip|baseline_dir> [current]")
    raise SystemExit(main(*sys.argv[1:]))
