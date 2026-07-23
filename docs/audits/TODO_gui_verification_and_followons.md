# TODO — GUI verification & remaining follow-ons

**Date opened:** 2026-07-23 · Tracks the hands-on-GUI checks and the intentionally-deferred pieces of the
brushable-workspace and sidecar-metadata work. Everything here is either **GUI-only to verify** (needs a
human in front of napari) or **deferred wiring** that is safe to leave until the GUI behaviour is confirmed.

---

## A. GUI verification of the brushable results panels (Gable to try, then report back)

The whole brushable-results-workspace feature (1.6.282–1.6.288) is headless-tested but has NOT been driven in
a live napari session. Please open each and confirm the behaviour, so any UX gaps are found before more is
built on top:

1. **In-vitro fluorescence** (Step 4 → *Compute Field Summary*): the docked *IVF Droplet Results* panel —
   intensity-vs-size and intensity-vs-circularity plots (left), per-droplet + field-stats tables (right).
   *Check:* clicking a droplet in the mask, a plot point, and a table row each highlight the same droplet in
   the other two.
2. **Cellular (Fluorescence)** (*Run Condensate Analyzer*): the *Cellular Object Results* panel — Csat +
   dilute plots, cell + condensate tables, and the two image tiers (cell labels + the new *Condensate Labels*
   layer). *Check:* clicking a **cell** brushes the cell table/plots; clicking a **condensate** brushes the
   condensate table — **without** having to change the active layer (the viewer-level dispatcher, 1.6.288);
   and a selection from a table/plot reveals the object in the image.
3. **Batch** (run a batch → auto-opens *Batch Results (brushable)*): the same plots+tables over every image,
   plus the *object image (from batch)* crop preview. *Check:* clicking a point/row shows that object's crop
   pulled offline from its source file.
4. **Session reload**: load a saved cellular session and confirm the panel re-opens and still brushes
   (1.6.288), and that a punctum resolves to the same object it did before saving.

**Known/expected rough edges to look for:** the crop preview is a plain grayscale thumbnail (contrast-
stretched); the batch panel is docked into the main viewer; two-tier picking prefers the finest tier
(condensate over cell) when both are under the cursor.

---

## B. Deferred wiring — sidecar metadata into the live load path (sidecar_metadata Parts 2 mid + 5)

The discovery mechanism and the ISS parser landed (1.6.289, `file_io/sidecar_discovery.py`,
`sidecar_metadata_for(image_path)`), fully unit-tested. **Not yet wired into a load.** To finish:

- Call `sidecar_metadata_for(file_path)` on load (off the Qt thread / within the existing worker; it is
  bounded and non-gating) and **merge** its fields into the repository's `file_metadata`, filling only
  fields the image left `None` and recording the per-field source. Where a sidecar value **disagrees** with
  an in-file one, record BOTH + the conflict (reuse the `common['conflicts']` pattern added in Step 1b) —
  never silently overwrite in-file metadata.
- **Feed the sidecar channel identity into naming** (Part 5): the ISS parser yields per-channel `emission_nm`
  / `excitation_lines_nm`; thread these into `channel_naming.identify_channel` (the emission-wavelength tier,
  ABOVE pixel classification) via `read_2d_image_channels`, so `Ch2` (525/50) names from its band and
  **never** falls through to `Brightfield`. Add the ISS regression test (`Ch2` is never `Brightfield`) once
  the real `im-1-FUS-PLD-1_*` fixtures are available.

## C. Deferred — the last-resort channel-identity dialog (sidecar_metadata Part 4)

A Qt dialog, shown **only** when — after in-file metadata AND sidecar discovery — a channel still has no
usable identity (no fluorophore / emission / excitation / channel name / modality). Model on
`field_status.prompt_pixel_size_on_load` (prompt-only-when-genuinely-missing). Per-channel optional fields;
**never** in batch/headless or for masks; skipped fields stay `None`. Persistence: **extend**
`utils/channel_designations.py` (signature-keyed, path-independent — do NOT build a second store) to hold
channel-identity answers, keyed on the acquisition signature; round-trip through the session; reversible;
user answers outrank guesses but never overwrite real metadata.

## D. Deferred — brushing niceties (optional)

- A per-tier legend / a small "which layer am I picking" affordance for the two-tier cellular case.
- In-vitro session-load re-mount (the cellular case re-mounts in 1.6.288; the in-vitro augmented droplet
  table would first need to be persisted into the repository so a reloaded session can rebuild it).

## E. Deferred — a GUI integration CI lane (from the qtbot-marker spec, Part B)

The `qtbot`-marker spec (2026-07-23, 1.6.291) is done except for one deliberately-deferred piece. Parts A/C/D
landed: the Qt-requiring brushing/plot tests were re-marked `integration` (per-test, across
`test_batch_brushing`, `test_cellular_brushable`, `test_brushable_workspace`, `test_plot_backend_pyqtgraph`);
the `core` lane is now provably headless; and `tests/test_ci_dependencies.py` gained two guards that fail if a
`core` test requests a pytest-qt fixture or a core-test file imports the GUI stack at module scope.

**Not done:** a **second CI lane** that installs `pip install -e ".[test]"` (which includes pytest-qt +
napari/Qt) and runs `pytest -m integration`. It was deferred because it needs offscreen-Qt infrastructure
(`QT_QPA_PLATFORM=offscreen` / xvfb) plus the heavy napari/torch/cellpose install, and a lane that can't be
validated green from a dev box is worse added than deferred. Until it exists, the integration tests run
**locally only** (via `.[test]`). The `core` lane's minimal install is now correct and honest on its own
(it deliberately omits pytest-qt; the new guard keeps it that way), so this is a coverage addition, not a bug.

---

*When a GUI check surfaces a bug or a UX change, note it here (or open a spec) so the fix is scoped against
observed behaviour rather than a guess.*
