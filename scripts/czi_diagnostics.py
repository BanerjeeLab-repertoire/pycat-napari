#!/usr/bin/env python
"""**CZI reader diagnostics — a run-once tool, NOT a CI test.**

The measurements the audits asked for, alongside the seam check, so a misbehaving CZI reader is
discussable with NUMBERS: read latency under three access patterns, cache behaviour, a staleness
read-back check, and the per-boundary seam scores for a sample of frames.

    python scripts/czi_diagnostics.py /path/to/streaming.czi [--frames 24] [--channel 0]

It opens the file through PyCAT's real streaming path (``CziBioFormatsReader`` + ``_CziChannelStack``),
so the numbers reflect what the application actually does. Reusable next time a reader misbehaves.
"""
import argparse
import statistics
import sys
import time


def _stack(path, channel):
    from pycat.file_io.readers.czi_bioformats import CziBioFormatsReader, _CziChannelStack
    reader = CziBioFormatsReader(path)
    return reader, _CziChannelStack(reader, channel)


def _time_reads(stack, order):
    """Read frames in the given index order, returning per-read latencies (ms)."""
    lat = []
    for i in order:
        t0 = time.perf_counter()
        _ = stack[int(i)]
        lat.append((time.perf_counter() - t0) * 1000.0)
    return lat


def _summ(lat):
    if not lat:
        return "n/a"
    s = sorted(lat)
    return (f"median {statistics.median(s):.1f} ms · p90 {s[int(0.9 * (len(s) - 1))]:.1f} · "
            f"worst {s[-1]:.1f} · n={len(s)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="CZI reader diagnostics (run-once, not CI).")
    ap.add_argument("path", help="path to a streaming .czi file")
    ap.add_argument("--frames", type=int, default=24, help="how many frames to sample")
    ap.add_argument("--channel", type=int, default=0)
    args = ap.parse_args(argv)

    import numpy as np
    from pycat.file_io.czi_seam import column_seam_score, persistent_seam_columns

    reader, stack = _stack(args.path, args.channel)
    n = stack.shape[0]
    k = min(args.frames, n)
    print(f"# CZI diagnostics — {args.path}")
    print(f"  frames T={n}  channels C={reader.n_c}  Z={reader.n_z}  H×W={reader.H}×{reader.W}\n")

    # ── access-pattern latency ──────────────────────────────────────────────
    seq = list(range(k))
    rng = np.random.default_rng(0)
    rand = list(rng.integers(0, n, size=k))
    alt = [v for pair in zip(range(k // 2), range(n - 1, n - 1 - k // 2, -1)) for v in pair]
    print("## Read latency (a fresh reader would warm the cache differently each run)")
    print(f"  forward-sequential : {_summ(_time_reads(stack, seq))}")
    print(f"  random-frame       : {_summ(_time_reads(stack, rand))}")
    print(f"  alternating-frame  : {_summ(_time_reads(stack, alt))}")

    # ── cache behaviour (the reader's own trace accumulators) ────────────────
    hits = getattr(reader, "_tr_hits", None)
    total = len(getattr(reader, "_tr_lat", []) or [])
    csize = len(getattr(reader, "_cache", {}) or {})
    print("\n## Cache")
    if hits is not None and total:
        print(f"  hit rate {100 * hits / total:.0f}%  ({hits}/{total} reads)  |  planes cached: {csize}")
    else:
        print(f"  planes cached: {csize}  (set PYCAT_CZI_TRACE=1 for the reader's own hit-rate trace)")

    # ── staleness read-back check ────────────────────────────────────────────
    print("\n## Staleness (a stale request must not overwrite a newer one)")
    a = np.asarray(stack[0]).copy()
    _ = stack[min(n - 1, k)]                 # jump far away (may trigger prefetch/generation churn)
    a2 = np.asarray(stack[0])
    same = bool(np.array_equal(a, a2))
    print(f"  frame 0 identical after a far jump and re-read: {same}"
          + ("" if same else "  <-- STALE ASSEMBLY: a jump corrupted an earlier frame"))

    # ── seam scores ──────────────────────────────────────────────────────────
    print("\n## Seam (per-boundary z-score; a column anomalous on a MAJORITY of frames is a seam)")
    idx = list(range(0, n, max(1, n // k)))[:k]
    frames = []
    for i in idx:
        f = np.asarray(stack[i])
        frames.append(f.reshape(-1, f.shape[-1]) if f.ndim > 2 else f)
    seams = persistent_seam_columns(frames)
    worst = sorted(((max(column_seam_score(f, x) for f in frames), x)
                    for x in range(1, frames[0].shape[1])), reverse=True)[:5]
    print("  worst boundaries (max z over frames):  " +
          ", ".join(f"x={x}:{z:.1f}" for z, x in worst))
    if seams:
        print(f"  ** PERSISTENT SEAM at column(s) {seams} — the reported defect is PRESENT. **")
    else:
        print("  no persistent seam — the read path is seam-free on the sampled frames.")
    return 0 if not seams else 1


if __name__ == "__main__":
    sys.exit(main())
