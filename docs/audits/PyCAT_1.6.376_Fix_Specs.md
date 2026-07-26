# PyCAT — Implementation Specs for the 1.6.376 Audit Findings

*Specs for the four live science bugs (S1–S4), the two low-severity cleanups, and the
`create_layer_dropdown` redundancy — all against the tree at **1.6.376**. Every current-code snippet
below was read out of the extracted source; every fix carries the golden-master/A-B test that would
have caught the bug (the discipline the Costes/KM fixes already show).*

**Ground rules (unchanged):** each **code** change is its own version bump + PyPI push + commit; docs
fold forward; ship changed-files-only as `pycat_<VERSION>_changed.zip` with the 4-line handoff. Each
scientific fix's *test is part of the deliverable* — a fix without a test asserting the corrected
quantity is not done. S1 in particular slipped through precisely because its last rewrite changed the
columns and (apparently) its test without asserting the count-vs-area pairing.

---

## S1 — `radial_localization_profile`: count and area come from inverted regions (HIGH PRIORITY)

> **✅ FIXED, shipped 1.6.393.** Both the points and the ring area now bin on one centre-referenced field
> `r_field` (0 = centroid, 1 = outermost mask pixel); points (µm) are converted to px before indexing. The
> inverted `dist_to_edge`/`norm_dist`/`r_abs`/`r_norm` block is gone. `tests/test_radial_profile.py` (`base`, 4)
> pins the count-and-area pairing the old code inverted (central points → count + small central area + high
> density; edge points → outer bin; area monotonic centre→edge; mpx scales area not binning). No prior test used
> this function (the missing test S1 slipped through).

**File:** `toolbox/spatial_metrology_tools.py:142`
**Severity:** medium science bug, **conclusion-inverting**, ~4-line fix.
**Reproduced:** all condensates at the cell centre → the count lands in the `[0,0.2]` bin but is paired
with the outer-annulus area (9080 µm²) instead of the central-disk area (~1018), understating central
density ~9× and flipping the whole profile.

**Root cause.** Two radial coordinates with **opposite orientation** are used in the same bin:

```python
# points: 0 at centre, 1 at edge
r_abs  = sqrt((coords - centroid)**2)                 # distance FROM centroid
r_norm = r_abs / (max_dist * mpx + 1e-8)              # 0 = centre, 1 = edge

# ring area (inside the loop): 0 at EDGE, 1 at centre  ← INVERTED
norm_dist = dist_to_edge / (max_dist + 1e-8)          # distance_transform_edt: 0 at boundary, max at centre
ring_px   = ((norm_dist >= lo) & (norm_dist < hi) & cell_mask).sum()
```

So bin `[lo,hi]` counts points near the **centre** but measures the ring area near the **edge**. There
is also a secondary scale issue: `max_dist` is the *inradius* (max distance-to-edge), which is not the
same length as the centroid→boundary distance used to normalise the points — on a non-circular cell the
two normalisers differ, so even the point binning is slightly off.

**Spec.** Put points and area on **one** centre-referenced normalised radius.

1. Build a single per-pixel radial coordinate from the centroid, normalised by the per-pixel
   centre→boundary distance so `0 = centroid`, `1 = boundary` everywhere:
   ```python
   from scipy.ndimage import distance_transform_edt
   ys, xs = np.where(cell_mask)
   cy, cx = ys.mean(), xs.mean()

   # Per-pixel distance from the centroid (px), and the max such distance WITHIN the mask
   # along each pixel's own direction is expensive; use the mask's own geometry instead:
   #   r_centre[p]      = distance of pixel p from the centroid
   #   r_boundary_scale = for each pixel, the centroid-to-boundary distance in its direction.
   # A robust, cheap normaliser that matches the points and the area is the pixel's fractional
   # radial position computed from the SAME field for both. Use:
   yy, xx = np.mgrid[0:cell_mask.shape[0], 0:cell_mask.shape[1]]
   r_centre_px = np.sqrt((yy - cy)**2 + (xx - cx)**2)
   # Normalise by the maximum centroid distance reached inside the mask (the circumscribed radius):
   r_max = float(r_centre_px[cell_mask].max())
   if r_max <= 0:
       return pd.DataFrame()
   r_field = np.clip(r_centre_px / r_max, 0.0, 1.0)     # 0 = centroid, 1 = outermost mask pixel
   ```
2. Bin **both** the points and the area on `r_field` (same orientation, same normaliser):
   ```python
   # points: their normalised centre-referenced radius, sampled from the SAME field
   pr = coords[:, 0].astype(int).clip(0, cell_mask.shape[0]-1)
   pc = coords[:, 1].astype(int).clip(0, cell_mask.shape[1]-1)
   pt_r = r_field[pr, pc]                                # 0..1, centre→edge, matches the area

   bins = np.linspace(0, 1, n_bins + 1)
   rows = []
   for i in range(n_bins):
       lo, hi = bins[i], bins[i+1]
       count   = int(((pt_r >= lo) & (pt_r < hi)).sum())
       ring_px = int(((r_field >= lo) & (r_field < hi) & cell_mask).sum())   # SAME field → same region
       area_um2 = ring_px * microns_per_pixel**2
       rows.append({'r_norm_centre': lo, 'r_norm_edge': hi, 'count': count,
                    'area_um2': area_um2,
                    'density_per_um2': count/area_um2 if area_um2 > 0 else 0.0})
   return pd.DataFrame(rows)
   ```
   **Key point:** the point radius (`pt_r`) is now read from the *same* `r_field` used for the ring
   area, so a point in the central disk and the central-disk area land in the *same* bin. This removes
   both the inversion and the normaliser mismatch. (If `coords` are in µm rather than px, convert to px
   before indexing `r_field` — check the caller; the current code treats `coords` as µm via
   `coords[:,0] - cy_um`, so divide by `microns_per_pixel` first.)
3. Delete the now-unused `dist_to_edge`/`norm_dist`/`r_abs`/`r_norm` block.
4. **Units caveat on `coords`.** The old code did `dy = coords[:,0] - cy_um` — i.e. it assumed `coords`
   are in **µm**. Confirm against the caller (`spatial_metrology_ui` / batch step). Whatever the unit,
   points and the `r_field` index must be in the **same** unit; the spec above assumes px for the
   `r_field` lookup, so convert `coords` to px if they arrive in µm.

**Test (`tests/test_radial_profile.py`, new — the test that was missing):**
```python
# All points at the centre of a disk cell → count AND small central area in the FIRST bin.
N = 201; yy, xx = np.mgrid[0:N,0:N]; c = N//2
mask = (xx-c)**2 + (yy-c)**2 <= 90**2
coords = np.column_stack([np.full(50, c), np.full(50, c)]).astype(float)   # 50 points at centre
df = radial_localization_profile(coords, mask, n_bins=5, microns_per_pixel=1.0)
assert df.iloc[0]['count'] == 50                      # centre bin holds the points
assert df.iloc[0]['area_um2'] < df.iloc[-1]['area_um2']   # centre bin area SMALLER than edge bin
# density: central points over the SMALL central area, not the large outer annulus
assert df.iloc[0]['density_per_um2'] > df.iloc[-1]['density_per_um2']
# Ring at the edge: put points only in the outer shell → they must land in the LAST bin.
edge = np.column_stack([np.full(50, c), np.full(50, c+80)]).astype(float)
df2 = radial_localization_profile(edge, mask, n_bins=5, microns_per_pixel=1.0)
assert df2.iloc[-1]['count'] > df2.iloc[0]['count']
```
The first assertion (`count` in the centre bin **and** small area there) is exactly the pairing the old
code inverts and the missing test never checked.

---

## S2 — `count_molecules_pooled` skips the camera corrections the single-trace path performs

> **✅ FIXED, shipped 1.6.394.** The pooled loop now subtracts each trace's pedestal before the variance pairs,
> feeds the corrected trace to `_variance_pairs`, takes `y[fast]` from the corrected trace, and pools the
> read-variance so the shared ν fit reuses `_fit_counting_nu` (free-intercept when a floor exists) instead of
> `_slope_through_origin`. `count_molecules_single` is untouched (byte-identical pin still green).
> `tests/test_pooled_counting_pedestal.py` (`base`, 4): pedestal invariance (vs the old ~2.5× inflation), pooled
> ν tracks single, population median recovers the true count with a pedestal, per-trace pedestal reported.

**File:** `toolbox/molecular_counting_tools.py` (`count_molecules_pooled`, ~:315)
**Severity:** medium science bug — the docstring-"preferred" estimator is the biased one.

**What single does that pooled doesn't.** `count_molecules_single` applies **three** corrections that
`count_molecules_pooled` omits:
1. **Pedestal subtraction** — `_pedestal, _read_var = _estimate_pedestal_read_noise(y); y = y - _pedestal`
   *before* `_variance_pairs` (line 188). Pooled feeds raw `y` in.
2. **Read-noise floor in the ν fit** — single uses `_fit_counting_nu(x_v, y_v, bf, read_var)`, which
   fits `y = ν·x + b` with a **free intercept** when a noise floor exists (collapsing the floor into
   `b`). Pooled uses `_slope_through_origin(X, Y)` — through-origin, no floor handling.
3. The numerator is already pedestal-corrected in single (because `y` was shifted before `y[fast]`).
   Pooled's `initial_intensity = y[fast]` keeps the pedestal in.

Per the module's own numbers, an uncorrected pedestal inflates N up to ~2.5×.

**Important nuance (do NOT "fix" this):** single deliberately keeps `y[fast]` (not `y[0]`) as the
numerator — there's a long comment (lines ~205–232) documenting that `y[0]` was a *wrong* "fix" caught
by the golden-master test, because `_variance_pairs` starts at frame `fast` so ν is measured over the
same window `y[fast]` starts. Keep `y[fast]`; just make sure it's pedestal-corrected like single's.

**Spec.** Bring the three corrections into the pooled loop, mirroring single exactly.

1. In the per-trace loop, estimate and subtract the pedestal per trace (each trace has its own dark
   tail), and pool the read-variance so the ν fit can use a floor:
   ```python
   all_x, all_y = [], []
   read_vars = []
   rows = []
   for i, tr in enumerate(traces):
       y = np.asarray(tr, dtype=float)
       bf = fit_bleaching_trace(y)
       used = bool(bf['success'] and bf['r_squared'] >= r2_min)
       _pedestal, _read_var = _estimate_pedestal_read_noise(y)     # per-trace dark reference
       y_corr = y - _pedestal                                      # pedestal off BEFORE variance pairs
       if used:
           x_v, y_v = _variance_pairs(y_corr, bf, fast=fast)       # use CORRECTED trace
           if len(x_v) >= 5:
               all_x.append(x_v); all_y.append(y_v); read_vars.append(_read_var)
           else:
               used = False
       rows.append(dict(trace_index=i,
                        initial_intensity=float(y_corr[fast]) if len(y_corr) > fast else np.nan,  # corrected
                        pedestal=float(_pedestal),
                        bleach_r2=float(bf['r_squared']) if bf['success'] else np.nan,
                        used=used))
   ```
2. Fit the pooled ν with the **free-intercept-when-a-floor-exists** rule, reusing the existing
   `_fit_counting_nu` logic rather than the through-origin slope. The pooled cloud has many traces, so
   pass a representative read-variance (e.g. the median of the used traces' `read_vars`) and a
   representative `p` from the pooled bleaching fits:
   ```python
   X = np.concatenate(all_x); Y = np.concatenate(all_y)
   pooled_read_var = float(np.median(read_vars)) if read_vars else 0.0
   # Reuse the single-trace fitter's decision (free intercept iff a floor exists). If _fit_counting_nu
   # needs a `bf`-shaped dict for `p`, pass {'p': <pooled p>} — or refactor _fit_counting_nu to take p
   # directly so both call sites share one implementation (see the note below).
   nu = _fit_counting_nu(X, Y, {'p': _pooled_p}, pooled_read_var)
   ```
   If threading `bf` through is awkward, the minimal alternative is to inline the same
   free-intercept-vs-through-origin decision on the pooled cloud (fit `Y = ν·X + b` when
   `pooled_read_var > 0`, else through origin). **Prefer refactoring `_fit_counting_nu` to accept `p`
   and `read_var` as plain args** so single and pooled call the identical fitter — that also closes the
   redundancy of two ν-fit paths.
3. `df['N'] = initial_intensity / nu` unchanged (numerator is now pedestal-corrected).

**Test (`tests/test_pooled_counting_pedestal.py`, new):**
```python
# Same synthetic traces with and without an added pedestal must recover the SAME pooled N.
traces = make_clean_traces(true_N=20, n=40)          # existing fixture style
r_clean = count_molecules_pooled(traces, fast=4)
traces_ped = [t + 800.0 for t in traces]             # add a camera pedestal
r_ped = count_molecules_pooled(traces_ped, fast=4)
assert abs(r_clean['per_trace']['N'].median() - r_ped['per_trace']['N'].median()) / 20 < 0.10
# And the pooled estimate must track the single-trace estimate on the same data (they now share ν logic)
assert abs(r_ped['nu'] - count_molecules_single(traces_ped[0], fast=4)['nu']) / r_ped['nu'] < 0.2
```
Old code fails the first assertion (pedestal inflates the pooled N ~2.5×).

---

## S3 — `tortuosity_per_object`: sums all branches, raster-order endpoints (consistency defect)

**File:** `toolbox/morphological_complexity_tools.py:230` (`tortuosity_per_object`)
**Severity:** medium; disagrees with the correct `fibril_tools.py:335` on the same object.

**Two defects.**
1. `path_len = mst.sum()` sums **every** edge of the skeleton's MST — so a branched (Y/T) fibril adds
   its side-branch length into the "path", inflating tortuosity.
2. `end_to_end = ||skel_pts[-1] - skel_pts[0]||` uses the **raster-order** last/first skeleton pixels
   (row-major scan order), which are not the geodesic endpoints — wrong for any non-monotonic shape.

`fibril_tools.fibril_morphometry` does it correctly per graph edge: path length along the traced path,
end-to-end between the path's true ends.

**Spec.** Replace the MST-sum + raster-endpoint logic with a geodesic-diameter computation on the
skeleton graph: find the two farthest skeleton **endpoints** (degree-1 nodes) and take the shortest-path
length between them as the path length.

1. Build the skeleton adjacency the same way (KD-tree `query_pairs(r=1.5)`), but instead of an MST sum,
   compute geodesic distances:
   ```python
   from scipy.sparse import csr_matrix
   from scipy.sparse.csgraph import shortest_path
   import numpy as np

   n = len(skel_pts)
   # weighted adjacency (Euclidean px distance on 8-connectivity)
   ri, ci, dd = [], [], []
   for i, j in pairs:
       d = float(np.linalg.norm(skel_pts[i] - skel_pts[j]))
       ri += [i, j]; ci += [j, i]; dd += [d, d]
   adj = csr_matrix((dd, (ri, ci)), shape=(n, n))

   # endpoints = skeleton pixels with exactly one neighbour (degree 1)
   deg = np.asarray((adj > 0).sum(axis=1)).ravel()
   endpoints = np.where(deg == 1)[0]

   if endpoints.size >= 2:
       # geodesic diameter: farthest pair of endpoints along the skeleton
       D = shortest_path(adj, method='D', indices=endpoints)      # (len(endpoints) x n)
       sub = D[:, endpoints]
       sub[~np.isfinite(sub)] = -1
       a, b = np.unravel_index(np.argmax(sub), sub.shape)
       ea, eb = endpoints[a], endpoints[b]
       path_len_px   = float(sub[a, b])
       end_to_end_px = float(np.linalg.norm(skel_pts[ea] - skel_pts[eb]))
   else:
       # closed loop or degenerate: fall back to the graph diameter over all nodes
       D = shortest_path(adj, method='D')
       D[~np.isfinite(D)] = -1
       a, b = np.unravel_index(np.argmax(D), D.shape)
       path_len_px   = float(D[a, b])
       end_to_end_px = float(np.linalg.norm(skel_pts[a] - skel_pts[b]))

   path_len   = path_len_px * microns_per_pixel
   end_to_end = end_to_end_px * microns_per_pixel
   tortuosity = (path_len / end_to_end) if end_to_end > 0 else np.nan
   ```
2. This makes `tortuosity_per_object` measure the **main axis** path (geodesic between the true
   endpoints), matching `fibril_tools`' per-segment definition — branches no longer inflate it, and the
   endpoints are geodesic, not raster.
3. **Consistency follow-through (recommended):** factor the geodesic-diameter computation into one
   helper (e.g. `_skeleton_geodesic(skel_pts, pairs)`) and have `fibril_tools` and
   `morphological_complexity` both call it, so the two tortuosities are guaranteed identical. If that's
   too invasive now, at minimum add a comment cross-referencing the two so they don't drift again.

**Test (`tests/test_tortuosity_consistency.py`, new):**
```python
# An L-shaped (bent, single-branch) skeleton: known path length vs end-to-end.
# Straight rod → tortuosity ≈ 1.0.
straight = rod_mask(length=50)
assert abs(tortuosity_per_object(straight)['tortuosity'].iloc[0] - 1.0) < 0.05
# 90° bend of two equal arms L: path = 2L, end-to-end = L*sqrt(2) → tortuosity ≈ sqrt(2).
bent = L_mask(arm=30)
assert abs(tortuosity_per_object(bent)['tortuosity'].iloc[0] - np.sqrt(2)) < 0.1
# A Y-shape must NOT count the stub branch: tortuosity of the main axis, not inflated.
y = Y_mask(main=40, stub=15)
assert tortuosity_per_object(y)['tortuosity'].iloc[0] < 1.3   # old MST-sum inflates well past this
# Agreement with fibril_tools on the same bent shape:
assert abs(tortuosity_per_object(bent)['tortuosity'].iloc[0]
           - fibril_tortuosity(bent)) < 0.05
```

---

## S4 — Felzenszwalb RAG merge: similarity graph merged as if it were a distance graph (no-op)

**File:** `toolbox/segmentation/fz.py:129–140`
**Severity:** medium; the advertised merge step silently doesn't run.

**Root cause — unit mismatch between the graph and its weight callback.**
```python
g = sk.graph.rag_mean_color(img, segments_fz, mode='similarity')   # edge weight = SIMILARITY (large = alike)
threshold = (np.std(img)**2) / 2
labels = sk.graph.merge_hierarchical(segments_fz, g, thresh=threshold, ...,
                                     weight_func=_weight_mean_color)  # returns a DISTANCE (small = alike)
```
- `mode='similarity'` builds edges weighted by `exp(-Δmeancolour²/σ)` — **large = similar**.
- `merge_hierarchical` merges edges with weight **below** `thresh` — correct only for a **distance**
  graph (small = similar).
- `_weight_mean_color` (the recompute callback) returns `||Δmean_color||` — a **distance**.

So the initial graph is similarity-weighted (values ~0..1, mostly near 1 for adjacent segments) while
the threshold `std²/2` is a tiny sub-1 number and the merge direction (below-threshold) expects a
distance. The result: essentially nothing merges (the audit's "no-op"), and the initial graph's weights
are on a different scale than the callback's.

**Spec — make the graph, the callback, and the threshold one consistent unit (distance).**

1. Build the RAG in **distance** mode so the initial edge weights match `_weight_mean_color`:
   ```python
   g = sk.graph.rag_mean_color(img, segments_fz, mode='distance')   # edge weight = ||Δmean colour|| (small = alike)
   ```
2. Set `thresh` in the **same intensity-difference units** as the mean-colour distance, not `std²/2`
   (which is a variance, wrong units). A defensible, scale-aware choice is a fraction of the image's
   intensity spread:
   ```python
   # img is float32 normalised; merge segments whose mean-intensity difference is below a small
   # fraction of the dynamic range. Expose `merge_tol` as a parameter (default e.g. 0.05) so the
   # user controls merge aggressiveness, per the anti-black-box preference.
   threshold = merge_tol * float(img.max() - img.min())
   ```
   Add `merge_tol: float = 0.05` to the function signature and the UI (`run_fz_segmentation_and_merging`
   already surfaces scale/sigma/min_size; add a merge-tolerance field beside them).
3. Keep `merge_func=merge_mean_color` and `weight_func=_weight_mean_color` — both already speak
   distance, so with a distance-mode graph and a distance threshold the merge is now internally
   consistent and actually runs.
4. Update the two misleading comments (the `std²/2` "sub-1 value" note and the `mode='similarity'`
   comment) to state the distance-based logic.

**Test (`tests/test_fz_merge.py`, new):**
```python
# A two-region image where felzenszwalb over-segments one region into several sub-segments with
# near-identical mean intensity must MERGE them back: post-merge label count < initial segment count.
img = two_flat_regions_with_texture()               # induces fz over-segmentation
seg_initial = sk.segmentation.felzenszwalb(img.astype('float32'), scale=..., sigma=..., min_size=2)
out = fz_segmentation_and_binarization(img, merge_tol=0.05)   # or whatever the public entry is
assert n_labels(out) < n_labels(seg_initial)        # merge actually reduced the count (old code: equal)
# merge_tol monotonicity: larger tol merges more (fewer labels)
assert n_labels(fz(img, merge_tol=0.2)) <= n_labels(fz(img, merge_tol=0.02))
```
The first assertion fails on the current code (merge is a no-op, so counts are equal).

---

## S5 — Low-severity cleanups (fold into the next relevant code change; no standalone commit)

These are not failures; group them into whichever nearby code change you're already making.

**S5a — Verify the ~13 dropped locals are truly dead, not lost outputs.** Most are harmless
(`moduli.py:249 N`, `frap_tools.py:455 f0` correctly unused in a central-difference Hessian,
`ui_segmentation_mixin.py:238 cell_diameter` — StarDist needs no diameter). But eyeball these to
confirm none was meant to reach the output before deleting:
`puncta_refinement.py:301,521 cell_bg_std`, `brightfield_tools.py:622 bg_std`,
`pipeline_snr_tools.py:224 first_snr`, `invitro/partition.py:488 dense`. If dead → delete the
assignment; if it *was* meant to feed a return/df column → wire it. Do this by reading each site, not by
blanket-deleting.

**S5b — 24 placeholder-less f-strings.** Sweep the `f"..."` literals with no `{}` (list from
`batch/steps/*.py`, `timeseries/ui.py`, `batch_step_registry.py:205,210`, etc.). Each is either a
stray `f` prefix (drop the `f`) or a dropped interpolation (add the variable). Read each — a couple
read as if a variable *was* intended, so this is a confirm-then-fix, not a blind strip.

No tests required beyond the existing suite staying green; these are cosmetic/correctness-neutral except
where S5a finds a genuinely lost output (which then needs its own assertion).

---

## S6 — Redundancy: `create_layer_dropdown` delegators drop `binding=` (unblocks the resolver)

**Finding, corrected on closer read:** this is **not** ten duplicate implementations. There is one
canonical implementation (`ui/base_ui.py:184`, the full tag-aware version with `name_hint` **and**
`binding`), and the nine toolbox UIs each define a **thin one-line delegator** to it. The problem is
that eight of the nine delegators have a **truncated signature that drops `binding=`**, so a toolbox
panel physically cannot pass a binding through — which is why only 3 of 180 dropdowns are resolver-bound.

Current delegator signatures:
```
base_ui.py:184                 create_layer_dropdown(self, layer_type, name_hint='', binding='')   ← canonical
invitro_fluor_ui.py:136        (self, layer_type, binding='')          → forwards binding ✓
frap_ui.py:52                  (self, layer_type, name_hint='')        → drops binding ✗
temperature_ui.py:88           (self, layer_type, name_hint='')        → drops binding ✗
vpt_ui.py:158                  (self, layer_type, name_hint='')        → drops binding ✗
brightfield_ui.py:82           (self, layer_type)                      → drops name_hint AND binding ✗
invitro_bf_ui.py:62            (self, lt)                              → drops both ✗
zstack_segmentation_ui.py:65   (self, lt)                              → drops both ✗
timeseries_invitro_fluor_ui.py:79 (self, layer_type)                  → drops both ✗
fusion_ui.py:56                (self, layer_type, name_hint='')        → drops binding ✗
```

**Spec — make every delegator forward the full signature (small, mechanical, unblocks S6-dependent
resolver work):**

1. Give each toolbox `create_layer_dropdown` the canonical signature and forward all args:
   ```python
   def create_layer_dropdown(self, layer_type, name_hint: str = '', binding: str = ''):
       return self.central_manager.toolbox_functions_ui.create_layer_dropdown(
           layer_type, name_hint=name_hint, binding=binding)
   ```
   (For `brightfield_ui`, `invitro_bf_ui`, `zstack_segmentation_ui`, `timeseries_invitro_fluor_ui` this
   also restores the dropped `name_hint`.) `invitro_fluor_ui` is already correct except it's missing
   `name_hint` — add it for uniformity.
2. **Better (optional): delete the delegators entirely and inherit.** All nine bodies are identical
   one-liners forwarding to `toolbox_functions_ui`. If these UI classes can reach the canonical method by
   inheritance (they already reference `self.central_manager.toolbox_functions_ui`), the cleanest fix is
   to have them inherit the base implementation and remove the local overrides — zero delegators to keep
   in sync. Check the class hierarchy: if the toolbox UIs don't inherit `base_ui.BaseUIClass`, keep the
   thin forwarders (step 1); if they do, delete the overrides.
3. Once the signatures forward `binding=`, **wire the covered dropdowns** (the S6-dependent resolver
   task): pass `binding='<key>'` at the call sites whose field maps to an existing `layer_bindings.json`
   key — `vpt.bead_stack`/`vpt.host_mask` in `vpt_ui`, `brightfield.input_image` in `brightfield_ui`,
   `invitro_fluor.input_image` in the invitro UIs, etc. This is what takes the resolver from 3/180 to
   meaningfully bound.

**Test (`tests/test_dropdown_binding_forwarded.py`, new):**
```python
# Every toolbox create_layer_dropdown must accept and forward binding= (contract test).
import inspect
for cls in TOOLBOX_UI_CLASSES:                       # vpt_ui, frap_ui, ... base
    sig = inspect.signature(cls.create_layer_dropdown)
    assert 'binding' in sig.parameters, f"{cls.__name__} drops binding="
# Behaviour: a bound dropdown carries _pycat_binding after construction (already covered for the 3;
# extend to the newly-forwarded ones as they're wired).
```

---

## Delivery sequencing (each line = one code change = one version + ritual)

1. **S1** — radial profile inversion. *Highest priority*: live, reproduced, conclusion-inverting,
   ~4-line fix + the missing pairing test.
2. **S2** — pooled molecular-counting pedestal/read-noise (prefer refactoring `_fit_counting_nu` to
   plain args so single + pooled share one ν fitter).
3. **S3** — tortuosity geodesic diameter (+ optional shared `_skeleton_geodesic` helper for
   consistency with `fibril_tools`).
4. **S4** — Felzenszwalb distance-mode graph + intensity-unit threshold + `merge_tol` param.
5. **S6** — forward `binding=` in the nine delegators (or delete-and-inherit), then bind the covered
   dropdowns. Fold **S5** (dropped locals, f-strings) into whichever of the above touches those files.

Each scientific fix (S1–S4) ships with its golden-master/A-B test as part of the deliverable. S1's test
is the one that was conspicuously absent — assert the **count-and-area-in-the-same-bin** pairing, which
is the exact invariant the bug violates and the previous rewrite's test did not check.
