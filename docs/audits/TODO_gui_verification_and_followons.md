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

---

*When a GUI check surfaces a bug or a UX change, note it here (or open a spec) so the fix is scoped against
observed behaviour rather than a guess.*
