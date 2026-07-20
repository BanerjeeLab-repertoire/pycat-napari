# Claude Code spec — Persist user-entered settings + manual pixel size in the session

> **◐ STATUS — Part 1 (manual pixel size) premise is STALE / already satisfied; Part 2 (workflow params)
> is a genuine open follow-on.** Re-verified against the current tree (1.6.193):
> **Part 1 — manual pixel size: ALREADY PERSISTS.** The spec's premise ("the manifest stores the pixel-size
> flags but NOT the value when the user entered it manually") is no longer true. `session_manifest.py:163`
> writes `microns_per_pixel_sq` (the value itself) alongside the `pixel_size_from_metadata` /
> `pixel_size_confirmed` flags, and `session_loader.py:337-342` restores the value AND both flags on reload —
> **regardless of provenance**, so a user-typed scale survives a save/reload and physical-unit measurements
> recompute correctly. The correctness gap this spec's highest-priority item describes is closed; nothing to
> implement. (Implementing the spec verbatim would add a redundant second copy of the same value.)
> **Part 2 — workflow parameters (method-widget values): NOT persisted, genuinely open.** Method-widget
> values (thresholds, radii, method selections) are still not written to the manifest, so a reloaded session
> cannot reproduce the exact analysis setup. This is real but shares plumbing with Bug 2 of the
> `session_clear_reset` spec — both need a central handle on the per-widget field-status registries, which
> does not exist yet. Left as a scoped follow-on (register the registries on `central_manager`; serialize
> their values into the manifest; restore on load), not shipped here.

**Date:** 2026-07-20 · **Target tree:** 1.6.176 · Verified against the 1.6.176 tree. A saved session
does not currently store the things the **user** supplied by hand — most importantly a pixel size
entered when the metadata lacked one, and user-chosen workflow parameters. So reloading a session
silently loses calibration the user typed, and every physical-unit measurement recomputes wrong. This
closes that gap.

## The gap (verified)
`session_manifest.py:write_manifest` stores pixel-size **flags** — `pixel_size_from_metadata` (:164),
`pixel_size_confirmed` (:165) — but **not the pixel-size value itself when the user entered it manually**
because the metadata had none. On reload, a file whose metadata lacks a scale is back to "resolution
incomplete, using 1.0" — the exact condition the pixel-size gate exists to catch — even though the user
already told PyCAT the real scale in the prior session. That is a silent calibration loss, and it
corrupts viscosity/ΔG/volume the moment analysis reruns.

Similarly, user-chosen **workflow parameters** (the values entered into a method widget — thresholds,
radii, method selections) are not persisted, so a reloaded session cannot reproduce the analysis the
user set up.

## What to persist
### 1. Manual pixel size (highest priority — it is a correctness issue)
When a pixel size was **user-supplied** (metadata absent/incomplete), store the value AND its
provenance in the manifest:
```json
"pixel_size": {
    "value_um_per_px": 0.067,
    "source": "user_entered",         // vs "metadata"
    "entered_at": "...",
    "z_step_um": 0.5                    // if the user supplied it (the isotropic-voxel bug's fix)
}
```
On reload:
- a `source: user_entered` scale is **restored and re-applied**, and the pixel-size gate is satisfied
  (it fired and was answered — persist that it was answered by the user);
- crucially, restore it **without silently trusting it blindly**: mark the layer's scale as
  user-provided (same as the live path does), so provenance stays honest — a restored manual scale is
  still a manual scale, not metadata.

This directly fixes "reloading loses the pixel size I typed."

### 2. User-entered workflow settings
Persist the parameters the user set in the active workflow so a reloaded session reproduces the setup:
- the method-widget field values (thresholds, radii, method/dropdown selections) for the active
  workflow;
- keyed by workflow + step so they repopulate the right controls on load.

Reuse the existing recording infrastructure where possible — `batch_processor.record(step, params)`
already captures step parameters for replay; the session save can serialize that same parameter record
rather than inventing a parallel capture. **One source of parameter truth, not two.**

### The distinction that keeps this honest
- **Metadata-derived values are NOT persisted as user settings** — they come from the file and will be
  re-read. Only persist what the **user supplied** (manual pixel size, entered parameters, explicit
  overrides). Persisting metadata-derived values would risk a stale copy overriding the file's own
  metadata on reload. Store *user intent*, re-read *file facts*.
- Provenance travels: a restored value records that it came from a saved session (which recorded that it
  came from the user), so the chain stays inspectable.

## Interaction with the general user-settings service
This is **session-scoped** persistence (travels with the saved session folder), distinct from the
**global** user-settings service (the separate spec, cross-session app preferences). Two different
scopes:
- manual pixel size for *this dataset* → the **session manifest** (here);
- "always default to 0.067 for the Dragonfly-63x profile" → the **global user-settings** acquisition
  profiles.
They compose: an acquisition profile in global settings can pre-fill the dialog; the value the user
accepts for this dataset is saved in the session. Note the relationship so they are not confused.

## Tests (`core`)
- A user-entered pixel size is written to the manifest with `source: user_entered` and restored on
  reload; the restored layer scale equals what the user entered (not 1.0).
- A metadata-derived pixel size is NOT persisted as a user setting (it is re-read from the file).
- `z_step_um`, when user-supplied, round-trips (guards the isotropic-voxel bug on reload).
- Workflow parameters entered by the user round-trip and repopulate the correct step controls.
- Provenance: a restored manual scale is marked user-provided, not metadata (honest chain).
- A session saved before this feature (no `pixel_size` block) still loads (backward-compatible).

## Steps
1. Extend `write_manifest` to store a `pixel_size` block when the value was user-entered (value +
   source + z_step).
2. Extend it to store user-entered workflow parameters (serialize the existing `batch_processor` step
   params for the active workflow).
3. On load, restore the manual scale (satisfying the gate, marked user-provided) and repopulate workflow
   controls.
4. Keep metadata-derived values out of the persisted user-settings (re-read from file).
5. Backward-compat: manifests without the new block load unchanged.
6. Tests above.
7. Full `pytest -m core` green.
8. Ship: version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (sessions now persist
   user-entered pixel size and workflow settings; reload no longer loses manual calibration).

## Definition of done
- A user-entered pixel size (and z-step) persists in the session and is restored on reload, satisfying
  the gate and marked user-provided.
- User-entered workflow parameters persist and repopulate the right controls.
- Metadata-derived values are re-read, not persisted as user settings.
- Old manifests remain loadable.
- Full `pytest -m core` green.

## Cautions
- **Persist user intent, re-read file facts.** Only user-supplied values are saved; metadata-derived
  ones are re-read on load. Saving a metadata value risks a stale copy overriding the file.
- **A restored manual scale is still manual** — mark its provenance honestly; do not launder it into
  "metadata" on reload.
- **Reuse the existing parameter record** (`batch_processor`) — do not build a second workflow-parameter
  capture path.
- **Backward-compatible** — sessions saved before this must still load.
- This is session-scoped; the global user-settings service (separate spec) handles cross-session
  app preferences and acquisition profiles — keep the scopes distinct.
