# PyCAT codebase audit — improvements + napari-plugin synergies

**Date:** 2026-07-15
**Scope:** `src/pycat` (139 files, ~88K LOC). Read-only analysis; no code changed.
**Framing:** This audit is deliberately *grounded against the real source*, not a generic code-smell
pass. There is already a mature design document in the project
(`PyCAT_Scientific_Navigator_Architecture.md`) that did the architectural thinking and explicitly
said the one input it still needed was the codebase itself — to verify which of its predicted
findings are real vs already fixed. That verification is the highest-value thing this audit can do,
so it leads. A general improvement + plugin-synergy pass follows.

---

## Part A — Verifying the architecture doc's predicted findings against real code

The architecture doc's §4 ("PDF8 tag findings — ship these first") lists six concrete claims about
PyCAT's *current* tag engine. Checking each against `src/pycat/utils/layer_tags.py` and
`tag_registry.py`:

### A1. `source='pipeline'` is silently downgraded — **CONFIRMED, LIVE BUG (highest-value finding)**
This is real and currently in the shipping code.
- `layer_tags.py:115` — `VALID_SOURCES = {'from_metadata', 'inferred', 'derived', 'user_set'}`.
  `'pipeline'` is **not** in the set.
- `tag_registry.py:235–242` — the pipeline auto-tagger writes `tag_layer(..., source='pipeline')`
  **six times** (op, role, target, and extra keys).
- `layer_tags.py:210–211` — `if source not in VALID_SOURCES: source = 'inferred'`. So every one of
  those pipeline tags is silently relabelled `inferred`.
- `layer_tags.py:212–213` + `DEFAULT_CONFIDENCE` — `inferred` carries confidence **0.6**, whereas a
  pipeline-produced tag is *definitional* (the operation that made the layer is known, not guessed)
  and should be ~0.95 like `derived`.

**Impact:** every tag produced by a recorded pipeline step is stored as a low-confidence *inference*.
Any future tag-based resolver that ranks candidates by confidence (exactly what the Navigator
design intends) will treat pipeline-produced layers as barely-trusted guesses. This quietly
undermines the whole "resolve by tags, prefer the confident one" premise.

**Fix (tiny, ~2 lines):** add `'pipeline'` to `VALID_SOURCES` and give it a high confidence in
`DEFAULT_CONFIDENCE` (e.g. `'pipeline': 0.95`). Alternatively change the six call sites to
`source='derived'` — but adding `pipeline` is better because it *distinguishes* "a PyCAT pipeline
step made this" from "some derivation made this," which is exactly the kind of provenance
granularity the tag system exists to keep. This is the single cleanest high-value fix in the audit:
small, isolated, and it un-breaks a correctness property the architecture is being built on.

### A2. `viewer.add_*` tagging gap — **ALREADY SOLVED (correct the architecture doc)**
The doc (PDF8 finding #5) recommends routing every `viewer.add_*` through a central
`TaggedLayerFactory` + a CI lint against direct calls. In the real code this is **already handled**,
just by a different (and arguably better) mechanism:
- `src/pycat/utils/layer_tag_hook.py` wraps `add_image` / `add_labels` / `add_points` /
  `add_shapes` / `add_tracks` so *every* insert is auto-tagged — its own header says there are
  "116 `viewer.add_*` call sites … and 2 of them tagged anything," and the hook fixes that class
  structurally: "a new call site is tagged automatically, because it does not know it is being
  tagged."
- The hook is honest about confidence: `op` from a known operation is definitional; a guessed tag is
  written `source='inferred'`; an absent tag is left absent rather than faked.
- (Today there are 122 raw `add_*` sites across the tree; top files: `segmentation_tools.py` (10),
  `vpt_ui.py` (8), `file_io.py` (8), `ts_cellpose_tools.py` (7), `timeseries_condensate_tools.py`
  (7), `fibril_tools.py` (7).) The wrapping hook covers them all without touching the sites.

**Takeaway:** the *wrapping hook* is a legitimate alternative to the *factory* the doc assumed would
be needed — it achieves the same "no call site can forget" guarantee without a large mechanical
migration of 122 sites. The doc's Phase-1 "add the factory + migrate call sites" work is therefore
mostly **already done**; what's NOT done is the CI lint (a cheap addition worth keeping) and — more
importantly — the *representation*/*state* vocabulary below.

### A3. `representation` tag (finding #1) — **NOT DONE (real gap)**
The doc recommends splitting the coarse `role` into `role` + a separate `representation`
(`intensity_field`/`binary_mask`/`instance_labels`/`coordinates`/`trajectories`/…). In the real
vocabulary (`layer_tags.py` `CORE_KEYS`/`CORE_VALUES`), there is **no `representation` key**. `role`
carries `{image, mask, labels, bead_stack, host_mask, roi, annotation, result, overlay, reference}`
— it *was* extended (1.5.493 added labels/overlay/reference, per the code comment), but it still
conflates "what the layer IS in the workflow" with "how it is represented." A resolver that needs
"give me instance labels, not a binary mask" cannot express that today: both are `role` values but
there is no typed compatibility between them.

**Recommendation:** this is the genuine remaining tag-vocabulary gap. Adding `representation` as a
separate key with a small compatibility lattice (instance_labels satisfies a request for labels/mask;
binary_mask does not satisfy instance_labels) is the enabling step for the Navigator's typed
capability matching. Medium effort, high leverage, and it's additive (existing `role` tags keep
working).

### A4. Processing `state` tag (finding #2) — **NOT DONE (real gap)**
No explicit `state` key (`raw → corrected → enhanced → segmented → refined → tracked → measured →
fitted → validated`). `provenance` (`raw/derived/segmentation/pycat-generated/user-created`) is
adjacent but coarser and semantically different (it's about *origin*, not *workflow stage*). The
doc's resolver uses `state` *ordering* to pick the most-refined candidate (e.g. hand-refined labels
over raw Cellpose labels) — that selection capability is not expressible with the current tags.

**Recommendation:** add `state` as an ordered vocabulary. This is what makes "prefer the most
processed version" a tag query instead of a name-matching heuristic — directly serves the
anti-black-box, resolve-don't-guess goal.

### A5. Lineage relations (finding #3) — **PARTIALLY DONE**
`layer_tags.py:116` — `VALID_RELATIONS = {'belongs_to', 'derived_from', 'supersedes', 'pairs_with'}`.
The doc recommends adding `registered_to`, `measured_from`, `tracks`, `reference_for`. So
`pairs_with` was added (good — that's beyond the original three), but the four measurement/tracking/
registration relations are **not present**. These matter specifically for the VPT/MSD and
colocalization workflows (a tracks layer `tracks` a detections layer; a measurement table
`measured_from` a labels layer) — i.e. exactly the plot↔layer brushing and linked-navigation
features already on the roadmap.

### A6. QC-writes-onto-layers (finding #4) — **CONFIRMED GAP**
The doc recommends QC set `quality_status`/`analysis_ready_for` *on the assessed layer* so a
downstream module can request "a layer that passed QC." Confirmed by reading `data_qc_tools.py` and
the whole tree:
- `run_full_qc(...)` (`data_qc_tools.py:1354`) **returns an ordered list of result dicts**
  (line 1452) — a disconnected result.
- The strings `quality_status` and `analysis_ready_for` appear **NOWHERE in the entire codebase**,
  and neither `data_qc_tools.py` nor `data_qc_ui.py` / `qc_gallery*.py` calls `tag_layer` to write
  a verdict onto the assessed layer.

**Impact:** QC's judgement is stranded in a result table; it never attaches to the layer it judged.
So the anti-black-box promise "the tool tells you which data to trust" stops at *display* — the
trust verdict can't be *queried* by a later step ("resolve a labels layer whose source image passed
QC"). This is the same disconnection the tag system exists to prevent, applied to the most
scientifically important annotation of all.

**Recommendation:** when QC assesses a layer, write `quality_status` (pass/warn/fail) and optionally
`analysis_ready_for` onto that layer via `tag_layer`. Additive, and it turns QC from a report into a
gate the resolver can honour. Pairs with the `representation`/`state` work (A3/A4) — together they're
what make "give me the trustworthy, most-refined version" a single tag query.

**Part A summary:** of the six predicted findings, **A1 was a live bug — now FIXED in this audit**
(see below; 'pipeline' added to VALID_SOURCES + confidence 0.95, verified by a headless core test),
**A3 + A4 are the real remaining vocabulary gaps** (additive, enabling), **A5 is partially done**,
**A2 is already solved** (correct the doc's assumption — the tag *hook* already covers all add_*
sites), and **A6 is a confirmed gap** (QC's verdict never attaches to the layer). That's a much
sharper Phase-1 list than "add a factory and migrate 122 call sites" — most of that is done; the
value is in the vocabulary (A3/A4), the QC-writeback (A6), and the confidence bug (A1, now done).

---

## Part B — General code-health observations (grounded, not nitpicks)

### B1. The codebase is genuinely clean — this is a real strength, and a manuscript point
Only **4 TODO/FIXME/HACK/XXX markers in ~88K LOC.** For a research codebase that is extraordinary —
most academic tools are littered with them. Combined with the dense explanatory comments (the
file_io and tag modules read like literate programming, explaining *why* not just *what*), this
supports the "reproducibility/rigor as an enabling layer" manuscript thesis concretely: the code
*itself* embodies the anti-black-box philosophy. Worth noting in the paper's software-quality
framing.

### B2. Module-size distribution flags refactor candidates
The largest modules are `ui_modules.py` (5446), `file_io.py` (3112, though we just refactored its
retention path), `timeseries_condensate_tools.py` (2807), `image_processing_tools.py` (2669),
`vpt_tools.py` (2626). `ui_modules.py` at 5.4K LOC is the standout — it's the god-module. Not urgent
(size alone isn't a bug), but if any module is going to accrue subtle coupling bugs, it's the one
every mixin and hook reaches into. A future decomposition (it already has `ui_*_mixin.py` siblings,
so the seams exist) would pay off, but only when there's a reason to touch it — not as speculative
churn.

### B3. The `core` vs `integration` test split is the right instinct — lean into it
`pyproject.toml` declares the pytest markers: `core: pure scientific kernels - no napari, no Qt, no
GPU. Must pass headlessly.` / `integration: requires napari/Qt/file-IO/GPU.` This is exactly the
separation that lets more of the suite run in CI (and in a sandbox like mine). The scientific
kernels — MSD/viscosity, spatial correlation, partition coefficients — are the parts where a
regression is a *silently wrong number*, and they're the parts that *can* run headlessly. Investing
in `core`-marked golden-master tests for the measurement kernels (the MRI/validation-suite roadmap
item) is well-supported by this existing structure.

---

## Part C — napari plugin synergies (the second half of the request)

**Key architectural fact discovered:** PyCAT does **not** register as a napari plugin. There is no
npe2 manifest / `napari.yaml` / `[project.entry-points]` for napari. PyCAT *embeds* napari as its
viewer and drives it, rather than being a plugin loaded *into* someone else's napari. This is a
deliberate and correct choice for the "PyCAT is a workbench, napari is its visualization layer"
positioning (memory: the rebrand direction) — but it shapes which plugin integrations make sense.

Because PyCAT owns the napari instance, it can *consume* other plugins' capabilities as libraries,
but it can't rely on the plugin-discovery ecosystem the way a plugin-in-napari would. So "synergy"
here means: which plugins' underlying functionality is worth calling, not which plugins to co-install
for a user to wire up themselves.

### C1. `devbio-napari` — already an optional extra, underexploited
`pyproject.toml` already declares `devbio-napari` as an optional dependency. devbio-napari bundles
napari-segment-blobs-and-things-with-membranes, napari-simpleitk-image-processing, and the
`napari-skimage-regionprops` family. PyCAT reimplements a lot of regionprops-style measurement in
`feature_analysis_tools.py` — worth an audit of whether any devbio-napari measurement is more
complete/validated than the in-house version, OR whether devbio-napari should be dropped from the
extras if nothing actually uses it (a declared-but-unused optional dep is quiet cruft). *Targeted
follow-up: grep for actual devbio-napari imports; if none, either wire it or drop it.*

### C2. `napari-ome-zarr` — deliberately NOT added, and that's correct (already audited)
Prior memory recorded this: PyCAT's lazy loader shares only the word "zarr" with napari-ome-zarr,
which is an OME-Zarr *format* reader PyCAT doesn't need until a user shows up with OME-Zarr data. No
action; noted here for completeness so it isn't re-raised.

### C3. `napari-clusters-plotter` / linked-plot plugins — conceptual overlap with the roadmap
The VPT/MSD plot↔layer brushing item (memory #16) and the biological-object-model linked navigation
(memory #12) are essentially "select in a plot → highlight in the viewer." `napari-clusters-plotter`
does exactly this for regionprops feature spaces. **Do not adopt it** (it assumes the plugin-in-napari
model PyCAT doesn't use, and PyCAT wants its own matplotlib plots with track_id identity plumbing) —
but it's worth reading as a *reference implementation* of the pick→highlight interaction before
building PyCAT's own. Steal the interaction pattern, not the dependency.

### C4. Where a plugin would genuinely help: 3D volume rendering presets
The 3D-rendering roadmap item (publication-quality Z-stack/condensate rendering) is napari-native and
needs no plugin — but `napari-animation` (rotation-movie export) is a real, focused capability PyCAT
doesn't have and that pairs directly with the MP4-export + publication-figure direction. This is the
one plugin whose functionality is worth calling rather than reimplementing, since keyframe-based
camera-path animation is fiddly to get right. Consider it a library dependency for the 3D-viz module
when that's built.

### C5. Plugin-synergy bottom line
PyCAT's embed-don't-plugin architecture means the synergy story is narrow and specific, not "install
these five plugins." The real wins are: (a) decide devbio-napari's fate (use or drop), (b) mine
napari-clusters-plotter as an interaction reference for the linked-navigation build, and (c) pull in
napari-animation as a library when the 3D-viz/movie-export module lands. Everything else PyCAT is
right to own itself.

---

## Prioritized action list (from this audit)

1. **[DONE in this audit] Fixed the `source='pipeline'` downgrade (A1).** Added `'pipeline'` to
   `VALID_SOURCES` + `DEFAULT_CONFIDENCE['pipeline']=0.95` in `layer_tags.py`, with a headless core
   test (`tests/test_pipeline_tag_source.py`, 4 assertions, verified passing in-sandbox). Un-breaks
   a correctness property the Navigator is being built on. Shipped as a zip alongside this audit.
2. **[confirmed gap] QC writes its verdict onto the assessed layer (A6).** Have QC call `tag_layer`
   to set `quality_status`/`analysis_ready_for`. Turns QC from a disconnected report into a
   resolver-honourable gate. Sandbox-doable (core-testable).
3. **[additive, enabling] Add `representation` (A3) and `state` (A4) tag keys.** The real remaining
   tag-vocabulary gap; makes typed capability matching + "prefer most-refined" expressible.
4. **[cheap] Add the four missing lineage relations (A5)** — `registered_to`, `measured_from`,
   `tracks`, `reference_for` — specifically enabling for VPT/MSD brushing + coloc.
5. **[cheap, hygiene] CI lint** against direct `viewer.add_*` outside the tag hook.
6. **[decision] devbio-napari: use or drop (C1).** Grep for real imports; a declared-unused optional
   dep is cruft.
7. **[when building 3D viz] napari-animation as a library (C4).**

Items 2–5 are all sandbox-doable code changes that need only `core` unit tests to verify — no GUI.
