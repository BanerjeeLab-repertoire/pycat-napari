# Claude Code spec — Multi-scene switcher (load one position at a time, lazily)

**Date:** 2026-07-18 · **Target tree:** 1.6.121 · Verified against the 1.6.121 tree. Follow-up to the
context-aware opener, parked in `roadmap.rst` as needing "its own focused pass so it doesn't
destabilise the loader." That pass is now safe: the file-I/O decomposition is complete and the CZI
streaming arc (1.6.110–117) landed. Touches `file_io.py` (scene routing), a scene-aware lazy wrapper
in `lazy_sources.py`, and a small UI control. Additive.

## The verified current behaviour
`file_io.py:1862` detects multi-scene files and shows `show_position_selection_dialog`, then
**loads every selected scene at once** (`scenes_to_load = [scenes[i] for i in selected_idx]`, looped at
`:1910`). Consequences:
- selecting several positions materialises several scenes into memory simultaneously — the opposite of
  the data-local, load-what-you-need thesis;
- there is no way to *switch* position after loading without reopening the file;
- for a large multi-position CZI/IMS this is exactly the memory profile the streaming work removed
  everywhere else.

The pieces needed already exist: scene enumeration (`_struct.scenes`), per-scene reading
(`image.current_scene`), and the lazy-wrapper pattern (`lazy_sources.py`, plus the CZI LRU + read-ahead
from 1.6.113/114).

## The design
**One scene loaded at a time, switchable, lazily.**

### Part A — a scene-aware lazy wrapper
In `lazy_sources.py` (Qt-free — the headless contract), add a wrapper that holds the reader plus a
*current scene index* and reads planes from that scene on demand. It must satisfy the SAME contract as
the existing lazy wrappers (verified by `test_no_eager_reads.py`): `__array__` refuses,
shape/dtype honest, `__getitem__` reads one plane. Switching scene rebinds the underlying reader's
scene and invalidates any cached planes — it must NOT materialise either scene.

Reuse the CZI cache/prefetch machinery where the reader supports it; a scene switch must **clear or
key** the LRU by scene so a stale plane from the previous position can never be served (a silent
wrong-position frame is the worst possible failure here).

### Part B — routing: default to ONE scene
Change the multi-scene path so the default is a single scene:
- keep the existing dialog, but make it a **single-select** ("which position?") rather than
  multi-select, defaulting to the first scene;
- load only that scene, through the scene-aware wrapper;
- preserve an explicit escape hatch **only if** a caller genuinely needs several positions overlaid —
  if nothing needs it, remove the multi-load path rather than leaving a memory footgun.

This mirrors the session-loader decision already made (a session is one manifest; pick exactly one).

### Part C — the switcher control
A small dropdown (scene/position name + index) near the layer controls that switches the loaded scene
in place:
- switching updates the layer's data source to the new scene, keeps the layer identity/tags, and
  refreshes the view;
- the pixel size / metadata / `data_repository` entries must be re-read for the new scene — a scene can
  legitimately differ (do NOT assume the previous scene's calibration carries over);
- the switch runs off the Qt thread if the first read is slow (reuse the 1.6.106/107 worker pattern),
  with a progress indication — no "Not Responding".

### Part D — identity and tags
The layer must carry which scene it holds (a tag/metadata field), so:
- results tables and exports record the position (essential for multi-position experiments and for the
  comparative-phenotyping `sample_metadata` join — a position is often a condition);
- switching scenes does not silently invalidate analyses computed on the previous scene. Decide and
  state the rule: either block switching while derived layers exist, or clearly mark derived layers as
  belonging to the prior scene. **Do not let a scene switch leave stale derived layers looking
  current** — that is the same class of error as a stale cached plane.

## Steps
1. Scene-aware lazy wrapper in `lazy_sources.py`, contract-matched to the existing wrappers.
2. Scene-keyed (or cleared-on-switch) plane cache.
3. Route multi-scene opens to a single scene by default; single-select dialog.
4. The switcher dropdown + off-thread first read + metadata re-read per scene.
5. Scene identity on the layer (tag/metadata) + the stale-derived-layer rule.
6. Tests (`core` where Qt-free): the wrapper refuses `__array__` and reads one plane
   (mirror `test_no_eager_reads.py` / `test_one_plane_reads_one_plane.py`); switching scenes does not
   materialise; a plane read after a switch comes from the NEW scene (the stale-cache guard);
   per-scene metadata is re-read; opening a multi-scene file loads exactly one scene.
7. Full `pytest -m core` green (complexity budget).
8. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG. Update the roadmap rubric
   (line ~436) to RESOLVED.

## Definition of done
- Opening a multi-scene file loads exactly ONE scene, lazily.
- A dropdown switches position in place without reopening, without materialising, off the Qt thread.
- The plane cache can never serve a frame from the previous scene.
- Per-scene metadata/pixel size is re-read on switch.
- The layer records its scene; stale derived layers cannot masquerade as current.
- Full `pytest -m core` green.

## Cautions
- **The stale-plane risk is the headline hazard**: a cached frame from the previous position served
  after a switch is a silently wrong image. Key the cache by scene or clear it — and test it.
- Do not assume calibration carries across scenes; re-read metadata per scene.
- Keep the wrapper Qt-free in `lazy_sources.py` — the headless guard covers that module.
- Do not destabilise the single-scene path: files with one scene must behave exactly as today.
- If the multi-scene-overlay path has no real consumer, REMOVE it rather than keeping a
  load-everything footgun beside the new default.
- Coordinate with `sample_metadata` (comparative phenotyping): a position is often a condition, so the
  scene identity should be joinable, not a display-only string.
