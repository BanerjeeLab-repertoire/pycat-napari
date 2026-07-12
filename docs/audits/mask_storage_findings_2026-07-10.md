# PyCAT mask storage — what the measurements actually say

**TL;DR: PyCAT is writing masks *completely uncompressed*. Turning on compression
is a one-line change that recovers ~100–160× on masks — which is essentially
everything the elaborate storage architecture promised, at zero complexity and
zero reproducibility risk. The clever schemes (RLE, keyframe deltas) are not worth
building; one of them is actively *worse* than plain compression.**

---

## The root cause

Every mask/stack save path in `file_io.py` omits compression:

```python
tifffile.imwrite(out_path, _to_uint16(arr), description=...)   # no compression=
tw.write(..., contiguous=True, ...)      # contiguous=True FORCES uncompressed
np.save(f"{name}.npy", ...)              # uncompressed
```

A 1024×1024 uint16 label mask is therefore saved as a **2.1 MB** file that
compresses to **13 kB**.

## Measured results (realistic masks, 1024×1024)

| Mask type | raw | zlib | best "clever" scheme | clever scheme's real gain |
|---|---|---|---|---|
| Cell labels (Cellpose-like) | 2.00 MB | **176×** | per-object bbox+bitpack: 272× | 1.5× — *but plain lzma gets 344×, beating it* |
| Condensate labels (800 puncta) | 2.00 MB | **123×** | per-object: 224× | 1.8× (lzma alone: 179×) |
| Binary threshold | 1.00 MB | **39×** | bit-packed: 47× | 1.2× |
| Fibril (thin filaments) | 1.00 MB | **36×** | bit-packed: 44× | 1.2× |
| Time-series masks (50 frames) | 25.0 MB | **117×** | keyframes+deltas: 126× | **1.08× — nothing** |

Ragged/realistic cell labels (irregular boundaries, eroded edges): still **103×**
with zlib, **132×** with lzma.

## What this overturns

1. **RLE is *worse* than plain compression** (28× vs 39× on binary masks). The
   generic compressor already finds the runs, and RLE's explicit
   `(row, start, length)` int32 triples add overhead. The document's centerpiece
   recommendation loses to `compression='zlib'`.

2. **Keyframes + XOR deltas buys ~8%.** Not worth the corruption surface, the
   reconstruction complexity, or the code. Modern compressors already exploit the
   redundancy.

3. **Per-object bbox + local mask** is a genuine 1.5–1.8× improvement over zlib —
   but *plain lzma beats it* on cell masks. Its real value is not compression, it's
   the **workflow** benefit (load only objects intersecting a viewport). Build it
   for that reason if ever, not for size.

4. **The "save the recipe" idea remains the risky one.** Across machines, Cellpose
   versions, GPU/CPU, and multi-year manuscript timelines, bit-identical
   reconstruction will fail. Recipes are good for *provenance and cache eviction*,
   never for canonical results. (PyCAT already has the provenance half:
   `batch_processor.record(step, params)`.)

## Costs of just turning compression on

| | uncompressed | zlib |
|---|---|---|
| 1024² uint16 mask | 2.097 MB | **0.013 MB (163×)** |
| write time | 2.2 ms | 9.0 ms (+7 ms) |
| read time | 1.6 ms | 2.8 ms (+1 ms) |
| lossless | yes | **yes** |
| PyCAT `description` tag preserved | yes | **yes** |

7 ms per mask. That is the entire price.

**Images compress far less** (noise 1.3×, smooth image 1.5×) — as expected, and
fine: images are the *source* data, masks are the derived bulk.

## The trap I hit (worth knowing)

Writing compressed pages one-at-a-time (dropping `contiguous=True`) **loses the
series structure**: `tifffile.imread` returns `(256,256)` instead of `(5,256,256)`.
The pixels are fine, but the stack collapses. Worse, writing the whole stack with
`imwrite(stack, compression='zlib')` yields axes **`QYX`** — the exact
undeclared-axis case that makes PyCAT prompt "is this T or Z?" on its *own* saved
files (the 1.5.351 bug).

**Correct call:**
```python
tifffile.imwrite(path, stack, compression='zlib',
                 metadata={'axes': 'TYX'},      # or 'ZYX' — declare it!
                 description=_pycat_tag('mask'))
```
This gives axes `TYX`, lossless round-trip, and the same 163×.

## Recommendation

**Do now (one-line-ish, ~100× win, no risk):**
- Add `compression='zlib'` to every mask/label/stack write.
- Replace the per-frame `contiguous=True` stack writer with a single
  `imwrite(stack, compression=..., metadata={'axes': ...})` call so the axis is
  declared and the series survives.
- Use `np.savez_compressed` instead of `np.save` for the fallback path.

**Don't build (measured, not worth it):**
- RLE (loses to zlib), keyframe+XOR deltas (8%).

**Maybe later, for workflow not size:**
- Per-object bbox + local mask, *if* partial/viewport loading becomes a real need.

**Never:**
- Recipe-only storage for canonical results.
