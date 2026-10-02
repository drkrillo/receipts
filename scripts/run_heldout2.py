#!/usr/bin/env python3
"""Held-out run #2 (BENCHMARK §D.5). Frozen code, unseen cases, ONE run, no fixes.

Ground truth is the expect_derivation column below, declared before any repo was
cloned. The separate declaration file was not kept, so this set is not one a
reader can check the way set #7 can be: see notes/HELDOUT-7.md for the one with a
dated written record.

Only `derivation` is scored; observations are printed for qualitative review, and
G2 is excluded from scoring as contaminated.

Usage: python3 scripts/run_heldout2.py <clones_dir>
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from receipts import report as rep   # noqa: E402

# (id, upstream, suspect, expect_derivation, scored, note)
CASES = [
    ("P1 flybywire",   "Fly-By-Wire",   "Fly-By-Wire-fork",       True,  True,
     "POSITIVE: MIT fork with LICENSE removed"),
    ("P2 prusa→bambu", "PrusaSlicer",   "BambuStudio",            True,  True,
     "POSITIVE (partial blind spot): SFC pursuing Bambu over AGPL"),
    ("N1 emby→jellyfin", "Emby",        "jellyfin",               True,  True,
     "NEGATIVE: lawful GPL fork before Emby closed"),
    ("N2 bambu→orca",  "BambuStudio",   "OrcaSlicer",             True,  True,
     "NEGATIVE: acknowledged community fork, both AGPL"),
    ("N3 unrelated",   "jellyfin",      "OrcaSlicer",             False, True,
     "NEGATIVE control: unrelated projects"),
    ("N4 unrelated",   "Fly-By-Wire",   "Emby",                   False, True,
     "NEGATIVE control: unrelated, size-mismatched"),
    ("G1 orca→jarczak", "OrcaSlicer",   "OrcaSlicer-bambulab",    True,  True,
     "GREY: Bambu C&D vs Jarczak; repo was taken down"),
]


def main():
    root = sys.argv[1]
    wd = tempfile.mkdtemp(prefix="receipts-ho2-")
    results = []

    for cid, up, sus, ed, scored, note in CASES:
        up_p, sus_p = os.path.join(root, up), os.path.join(root, sus)
        print("=" * 78)
        print(f"{cid}   {note}")
        print("-" * 78)
        if not (os.path.isdir(up_p) and os.path.isdir(sus_p)):
            missing = [p for p in (up_p, sus_p) if not os.path.isdir(p)]
            print(f"  SKIPPED — clone missing: {', '.join(os.path.basename(m) for m in missing)}")
            results.append((cid, ed, None, scored, "clone missing"))
            continue
        try:
            r = rep.provenance(up_p, sus_p, wd)
            ad = r.derivation_likely
            print(f"  derivation: expected={ed} got={ad} (confidence: {r.confidence})")
            print(f"  shared commits={r.overlap.shared_commits:,} "
                  f"distinctive blobs={r.overlap.distinctive_shared_blobs:,}")
            lic_div = (r.upstream_license_at_divergence.spdx_id
                       if r.upstream_license_at_divergence else "n/a")
            print(f"  license: upstream@divergence={lic_div} | suspect now="
                  f"{r.suspect_license_now.spdx_id}")
            a = r.attribution
            print(f"  attribution: notice_stripped={a.notice_stripped} "
                  f"headers {a.files_compared} compared / {a.files_header_replaced} "
                  f"replaced / {a.files_header_dropped} dropped")
            print("  observations:")
            for o in r.observations:
                print(f"    • {o}")
            results.append((cid, ed, ad, scored, ""))
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR: {type(exc).__name__}: {exc}")
            traceback.print_exc(limit=2)
            results.append((cid, ed, None, scored, f"{type(exc).__name__}"))

    print("\n" + "=" * 78)
    print("SCORED (derivation only)")
    print("=" * 78)
    e = [x[1] for x in results if x[3] and x[2] is not None]
    a = [x[2] for x in results if x[3] and x[2] is not None]
    for cid, ed, ad, scored, err in results:
        mark = "SKIP" if ad is None else ("OK  " if ad == ed else "MISS")
        print(f"  {mark}  {cid:<20} expected={str(ed)[0]} got="
              f"{'—' if ad is None else str(ad)[0]}  {err}")
    tp = sum(1 for x, y in zip(e, a) if x and y)
    tn = sum(1 for x, y in zip(e, a) if not x and not y)
    fp = sum(1 for x, y in zip(e, a) if not x and y)
    fn = sum(1 for x, y in zip(e, a) if x and not y)
    pr = tp / (tp + fp) if tp + fp else 1.0
    rc = tp / (tp + fn) if tp + fn else 1.0
    print(f"\n  TP={tp} TN={tn} FP={fp} FN={fn} | precision={pr:.2f} recall={rc:.2f} "
          f"accuracy={(tp+tn)/len(e):.2f}  (n={len(e)})")


if __name__ == "__main__":
    main()
