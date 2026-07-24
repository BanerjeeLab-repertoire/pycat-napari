# Claude Code spec — Sidecar metadata discovery, the ISS case, and a last-resort channel-identity dialog

> **◐ STATUS — Part 1 (both immediate bug fixes) DONE: Step 1a shipped 1.6.285, Step 1b shipped 1.6.286.
> Parts 2-3 CORE DONE (sidecar discovery mechanism + ISS parser, shipped 1.6.289; built and tested against a synthetic fbs matching the spec format). Part 4 dialog MECHANISM DONE (1.6.316).**
>
> **◐ Load-path wiring — 2D path DONE, 1.6.320.** New Qt-free orchestration `file_io/load_channel_identity.py`
> joins the two shipped mechanisms into the live 2D loader: `resolve_channel_identity_on_load(file_path,
> channel_info)` is called after `read_2d_image_channels` (file_io.py:387) — it discovers a sidecar
> (`sidecar_metadata_for`) and **enriches naming from its per-channel emission ABOVE the pixel/position
> guess** (an ISS `_Ch1`/`_Ch2` pair is named far-red / green from 647/525 nm and **never falls to
> `Brightfield`**), then **applies any remembered identity** for this acquisition layout
> (`recall_channel_identities`) as a `source='user'` label. `_channels_all_confident` now counts `'user'`, so
> a recalled answer skips the naming dialog (**a same-layout file is never re-asked**). When the user DOES
> type a name for a genuinely-unidentified channel in the existing naming dialog,
> `remember_user_channel_names` persists it (blank/default answers are not stored) — the recall side then has
> something to recall. All orchestration is deterministic and **non-gating** (any failure → the image loads
> with what the reader found); `tests/test_load_channel_identity.py` (`core` 3 enrichment + `base` 4
> persistence). Rather than stack a **second** last-resort prompt on top of the existing naming dialog (which
> already asks for an ambiguous channel — a second would violate the spec's own "the dialog is a last resort"
> caution), the wiring reuses that dialog as the ask and adds recall/remember/sidecar-enrichment around it.
>
> **◐ Stack-path sidecar enrichment — DONE (IMS + generic), 1.6.324.** `enrich_channel_from_sidecar(ch_info,
> sidecar, channel_index)` (Qt-free; `enrich_with_sidecar` now delegates to it) is wired into the IMS and
> generic (TIFF/CZI-via-structured-reader) stack openers: the sidecar is discovered once per load (non-gating)
> and each weak channel is named from its emission above the pixel/position guess, so an ISS stack is never
> `Brightfield`. `tests/test_load_channel_identity.py` +3 (`core`). The CZI-streaming back-end is left unwired
> (it sits exactly at the function-length ratchet, and CZI carries its own structured metadata so the sidecar
> case does not arise there).
>
> **REMAINING after 1.6.324:** (1) identity **recall/remember** on the stack path — the stack openers name each
> layer in isolation, so this still needs an aggregating channel-info list threaded through
> `_finalise_stack_load` (the enrichment above does not need it; recall/remember does). (2) **Live GUI
> validation** of the 2D/stack wiring (dialog interaction, layer renaming) is not headlessly reproducible here —
> the orchestration is unit-tested and the live calls are failure-tolerant, but the end-to-end load has not been
> exercised in a running napari.
> **Part 4 (last-resort channel-identity prompt) — mechanism DONE, 1.6.316.** `channel_naming.channel_needs_identity`
> (True only for the position-guess fall-through), `channel_designations.remember_channel_identity` /
> `recall_channel_identities` / `forget_channel_identity` (signature-keyed, extends the same store as the
> condensate designation — they coexist either order; opt-in, layout-guarded, reversible), and
> `ui/channel_identity_dialog.py` (`build_channel_identity_dialog` / `prompt_channel_identities` — optional
> per-channel prompt, only the unidentified channels, never in batch/headless, `harvest()` seam).
> `tests/test_channel_identity.py` (`core` 4 + `integration` 2). Residual: calling the prompt on load when a
> channel needs it and applying recalled answers — the live load-path wiring, alongside the sidecar-on-load
> integration.
>
> **Step 1b — DONE.** `metadata_extract._parse_voxelsize` parses `VoxelSize=X x Y x Z` (µm) out of a TIFF
> `PageName`; `extract_tiff_metadata` fills `z_step_um` from Z (the 19 µm the structured pixel-size object
> never sees) and cross-checks the in-plane X against XResolution — agreement confirms it, a disagreement
> beyond 2% is recorded in `common['conflicts']` rather than silently resolved (XResolution keeps the value).
> `tests/test_pagename_voxelsize.py` (`core`, 4 tests).
>
> **Step 1a — DONE.** The 2-D load path (`file_io.py`) now routes `pixel_size_from_metadata` through the ONE
> provenance helper `_calibration_is_from_metadata` (which reads `pixel_size_source`) whenever a real
> (non-sentinel) pixel size is in the repository — not only inside the 1.0-sentinel recovery branch. So a 2-D
> TIFF whose scale came straight from `tiff_tags` (reader succeeded, no recovery) is now marked calibrated and
> the scale bar reads **µm**, not `px` (Meet's ISS file). Guarded on the sentinel so a rejected-corrupt scale
> is never re-marked as real. `tests/test_sidecar_metadata_step1.py` (`core`, 4 tests) incl. the spec's guard
> that **no site sets the flag by a bare value comparison** (the single documented fallback lives inside the
> helper). Full core green (1714).
>
> **REMAINING:** Step 1b (parse `PageName VoxelSize=X x Y x Z` for pixel/z-step + cross-check); Part 2 (the
> generic sidecar-discovery layer — bounded, parallel, non-gating, with a parser registry); Part 3 (the ISS
> Vista `_fbs.xml` parser + channel↔file mapping); Part 4 (the last-resort channel-identity dialog +
> signature-keyed persistence). **Parts 3–4 need the real ISS fixture files** (`im-1-FUS-PLD-1_Ch{1,2}.tif` +
> `_fbs.xml`), which are not in the repo.

**Date:** 2026-07-21 · **Target tree:** 1.6.281 · Verified against the 1.6.281 tree **and against real
user files** (`im-1-FUS-PLD-1_Ch{1,2}.tif` + `im-1-FUS-PLD-1_fbs.xml`, ISS Vista / Alba confocal,
reported by Meet Raval). Three joined pieces: a **generic sidecar discovery** layer, a **parser for the
ISS case** that proves it, and a **last-resort dialog** for genuinely orphaned files whose answers are
remembered. Plus two immediate bug fixes the same files exposed.

---

## The evidence (measured, not assumed)

**The TIFFs carry no channel identity at all.** Both files have 23 standard tags, **no private/vendor
tags**, and are byte-identical in metadata. PyCAT's extractor returns:
```
pixel_size_um     = 0.09765625     ← correct
pixel_size_source = 'tiff_tags'    ← provenance recorded
channel_name / excitation_nm / emission_nm / objective / NA = None
```
The only descriptive strings are `ImageDescription = "Alba confocal intensity image"` and
`Software = "ISS Vista"`.

**The sidecar carries everything.** `im-1-FUS-PLD-1_fbs.xml` (6.7 KB) holds, in free-text
`<fromComments>` blocks under `[Section]` headers:

| channel | emission filter | pinhole | detector |
|---|---|---|---|
| **Ch1** | **647/57 nm** | 100 µm | APD (PerkinElmer SPCM-AQR-15) |
| **Ch2** | **525/50 nm** | 0 µm | APD (PerkinElmer SPCM-AQR-15) |

plus lasers **488 nm @30%** and **635 nm @15%**, excitation dichroic 405/488/594, emission dichroic
495 LP, **objective 60×**, pixel dwell **0.1 ms**, scan region **50×50 µm** over 512 px (independently
confirming 0.0977 µm/px), and z-range −4→15 µm. `<Channels>2</Channels>` with
`ChannelMapping = 0,1,-1,-1` confirms two active channels, so the `_Ch1`/`_Ch2` filename suffix maps
directly onto the `[Ch1]`/`[Ch2]` sections.

**Three independent facts make "Brightfield" impossible** for these files: emission filters exist
(brightfield has none), the detectors are **APDs** (photon counters, fluorescence-only), and the image
description says *confocal intensity image*. Yet PyCAT labels one `im-Brightfield` because it falls
through to a pixel-shape guess.

---

## Part 1 — Two immediate bug fixes these files expose

### 1a. The scale bar shows "pixel" although the pixel size is read correctly *(the reported bug)*
The value and its provenance are both correct in the repository, but the **2D load path never sets
`pixel_size_from_metadata`**:
- **Stack path** (`stack_load.py:69`) calls `_calibration_is_from_metadata` (`tagging.py:35`), which
  reads `pixel_size_source`. Run against this file's repo state it returns **True** — correct.
- **2D path** (`file_io.py:385 → 426`) sets the flag `True` **only inside a recovery branch** that runs
  when the pixel size came back as the 1.0 sentinel. Here the reader *succeeded*, so the branch is
  skipped and **nothing else on the 2D path sets the flag** — it stays at its `data_modules.py:111`
  default of `False`.

`napari_adapter._is_calibrated` then sees `False` and renders `'px'`.

**Fix:** the 2D path must set `pixel_size_from_metadata` from `_calibration_is_from_metadata` — the same
provenance-based helper the stack path uses — **whenever a pixel size was obtained**, not only in the
recovery branch. The flag is currently written from **10 sites**; route them through the one helper so
this cannot diverge again, and add a guard test (mirroring `test_pixel_size_single_accessor`) that no
site sets it by a bare value comparison.

**Regression test:** load `im-1-FUS-PLD-1_Ch1.tif` → `pixel_size_from_metadata is True`,
`_is_calibrated` True, scale-bar label `'um'`.

### 1b. `PageName` carries an unparsed z-step
`PageName = "Page 1, S=1, P=1, T=1, Z=1, VoxelSize=0.0977x0.0977x19.0000"` — `z_step_um` currently
extracts as `None` while **19.0 µm** sits in the string. Parse `VoxelSize=XxYxZ` from `PageName` as a
pixel-size/z-step source (recorded as its own `pixel_size_source` value). It also **cross-checks**
XResolution: 0.09765625 vs 0.0977 agree to 0.04%. Where two in-file sources disagree beyond tolerance,
**report the conflict** (per the metadata-validity spec) rather than silently preferring one.

---

## Part 2 — Generic sidecar discovery (thorough, parallel, never gating)

**No sidecar logic exists today** (the "companion" references in `lazy_sources` are multi-file OME-TIFF,
a different concept). This adds a general layer: on load, look for companion files that describe the
image.

### Discovery rules
1. **Stem matching, progressively relaxed.** For `im-1-FUS-PLD-1_Ch1.tif`, try in order:
   the full stem; the stem with a **channel/position/time suffix stripped**
   (`_Ch\d+`, `_C\d+`, `_ch\d+`, `_s\d+`, `_t\d+`, `_z\d+`, `_Pos\d+`, `_XY\d+`); then the stem with a
   known sidecar tag appended (`_fbs`, `_metadata`, `_settings`, `_properties`, `_info`, `_log`).
   The ISS case resolves at step 2+3: `im-1-FUS-PLD-1` + `_fbs.xml`.
2. **Known-name lookup in the same directory**, vendor-agnostic — e.g. `metadata.txt`,
   `*_metadata.txt` (Micro-Manager), `DisplaySettings.json`, `.xlif`/`.lifext` (Leica),
   `*.nd`/`*.nd2info`, `Experiment.xml`, `*.pty`/`*.mes`, `*.txt` alongside a lone TIFF.
3. **Directory scan, bounded.** Same directory only, **no recursion**, a hard cap (e.g. ≤200 entries
   examined, ≤2 MB read per candidate). Never walk a whole drive — the same discipline as
   `test_session_discovery::test_discovery_does_not_CRAWL_the_whole_drive`.

### Execution: parallel and non-blocking
- Candidate sidecars are probed **concurrently** (a small thread pool) and with a **short deadline**
  (e.g. 500 ms total). This is I/O-bound stat/open work, so parallelism is the right tool.
- **Never gating.** If discovery is slow, fails, finds nothing, or the deadline expires, **the image
  loads normally** with whatever in-file metadata it has. A sidecar is an enrichment, never a
  precondition. Any failure is recorded with a reason (not a bare `except: pass`) and surfaced in the
  metadata dialog.
- Runs **once per load**, off the Qt thread (reuse the existing `operation_runner`/worker pattern so the
  UI never stalls).

### Merging
Sidecar fields merge through the **same field-level precedence + per-field source recording** the
deep-metadata spec defines (`pixel_size_source` generalised). A sidecar value fills a field the image
left `None`; where both exist and **disagree**, record both and flag the conflict — never silently
overwrite in-file metadata.

### Registry of parsers
```python
@dataclass(frozen=True)
class SidecarParser:
    name: str                      # 'iss_vista_fbs'
    matches: Callable[[Path], bool]     # cheap: extension + a header sniff
    parse: Callable[[Path], dict]       # -> the common metadata schema (+ channels list)
```
New instruments are added by registering a parser — **not** by editing the loader. Ship with the ISS
parser (Part 3) and a stub registry; Micro-Manager/Leica/Nikon follow as separate work.

---

## Part 3 — The ISS Vista parser (proves the mechanism on a real file)

`<fromComments>` is **free text**, not structured XML, so parse it as sectioned key/value:
- Section headers `[Excitation Laser]`, `[Emission Dichroic]`, `[Detection Channels]`, `[Ch1]`, `[Ch2]`,
  `[Microscope]`.
- Lines of the form `Key  -   : value` (tabs and a trailing `-` are noise — strip them).
- `[Ch\d+]` sections become **per-channel records**; everything else is system-level.

Map into the schema the deep-metadata spec defines:
- `Emission Filter "#3 - 647/57 nm"` → `emission_nm = 647`, `emission_bandwidth_nm = 57`
- Laser lines → available `excitation_nm` (488, 635) with power percentages
- `Detector = APD`, `Detector Module = PerkinElmer SPCM-AQR-15`
- `Microscope Objective Magnification = 60` → `nominal_magnification`
- `PixelDwellTime`, `ImageRegion` (a **cross-check** on pixel size: 50 µm / 512 px = 0.0977), z-range,
  `Channels`, `ChannelMapping`
- **`modality = 'fluorescence'`** — justified by emission filters + APD detectors, recorded with that
  reason, not asserted bare.

**Channel↔file mapping:** `_Ch1`/`_Ch2` in the filename → `[Ch1]`/`[Ch2]`. Where the mapping is
ambiguous (suffix absent, count mismatch with `<Channels>`), **do not guess** — leave the fields
unfilled and let Part 4 ask.

**Naming payoff:** Ch2 (488 ex → 525/50 em) is the GFP/Alexa-488 band; Ch1 (635 ex → 647/57 em) is
far-red. Feed these into the naming tiers **above** pixel classification, so the label becomes e.g.
`Ch2 525/50` (or a matched fluorophore) and **never `Brightfield`**.

---

## Part 4 — Last-resort channel-identity dialog (only when genuinely uninformed)

### The trigger — deliberately narrow
Show it **only** when, after in-file metadata **and** sidecar discovery, a channel has **no usable
identity**: no fluorophore, no emission/excitation, no channel name, no modality — i.e. the naming
would otherwise fall to a pixel guess or a bare position index.

**Never show it when** any real evidence exists, when the file is a mask/label layer, in **batch or
headless** runs (record the gap and continue), or for a channel layout the user has already answered
(Part 4c). Model it on `prompt_pixel_size_on_load` (`field_status.py:349`) — the existing
prompt-only-when-genuinely-missing precedent.

### What it asks (per channel, all optional)
Fluorophore (free text + common suggestions: DAPI, GFP/EGFP, mCherry, Alexa 488/568/647, Cy5, TRITC,
BFP, YFP, RFP, mNeonGreen, JF549…), excitation nm, emission nm, imaging modality (fluorescence /
brightfield / DIC / phase / transmitted), objective magnification and NA, and a free-text note.
**Every field skippable** — an unanswered field stays `None`, never a default (the missing-stays-missing
rule).

### 4c. Remembering — reuse the existing signature model
`utils/channel_designations.py` already solves exactly this problem for condensate-channel choice:
`acquisition_signature(channel_infos)` keys a remembered answer to **the acquisition layout, not the
file path** ("a new file with the same channel layout gets the remembered designation"), with
`remember_designation` / `recall_designation` / `forget_designation` and a JSON store.

**Extend that store to hold channel-identity answers** rather than building a parallel one:
- Key on the **acquisition signature** (channel count, order, spectral buckets, plus software/objective
  when known) — so the next `im-*_Ch1/_Ch2` ISS acquisition is named automatically and never re-asks.
- **Also persist into the session** so reloading a session restores the answers (the session already
  round-trips in-app tags — `test_sample_metadata::test_in_app_tags_ROUND_TRIP_through_the_manifest`).
- Answers are **user-sourced**: confidence `1.0` per the confidence spec, `source='user'`, and
  **user answers always beat later guesses** but never overwrite real metadata found afterwards.
- **Reversible** — a "forget/edit these channel identities" path, mirroring `forget_designation`.

---

## Tests (`core`, with the real files as fixtures)

**Bug fixes:**
- Loading `im-1-FUS-PLD-1_Ch1.tif` sets `pixel_size_from_metadata=True`; `_is_calibrated` True; scale
  bar label `'um'` (the reported regression).
- No site sets `pixel_size_from_metadata` by a bare value comparison (guard test).
- `VoxelSize=` parses to `z_step_um=19.0`; XResolution and VoxelSize agree within tolerance; a
  fabricated disagreement is reported.

**Sidecar discovery:**
- `im-1-FUS-PLD-1_Ch1.tif` finds `im-1-FUS-PLD-1_fbs.xml` via suffix-stripped stem + `_fbs`.
- Discovery is **bounded**: same directory only, no recursion, capped entries (assert the scan does not
  walk a parent tree).
- Deadline/failure/absence → the image **still loads** with in-file metadata (non-gating test).
- Discovery runs off the Qt thread.
- A sidecar value fills a `None` field; a conflicting value is flagged, not silently applied.

**ISS parser:**
- Ch1 → `emission_nm=647`, bandwidth 57, detector APD, pinhole 100 µm; Ch2 → 525/50, pinhole 0.
- Lasers 488@30%, 635@15%; objective magnification 60; dwell 0.1 ms.
- `ImageRegion` cross-check yields 0.0977 µm/px, agreeing with the tags.
- `modality='fluorescence'` with the emission-filter/APD reason recorded.
- **`Ch2` is never named `Brightfield`** (the reported bug, as a regression test).
- An ambiguous channel↔file mapping leaves fields unfilled rather than guessing.

**Dialog:**
- Fires only when identity is genuinely absent; suppressed when any evidence exists, for masks, and in
  batch/headless.
- Answers persist by acquisition signature; a second file of the same layout does **not** re-prompt.
- Answers round-trip through a saved session.
- Skipped fields remain `None`.
- User answers outrank guesses but never overwrite real metadata.
- Forget/edit works.

---

## Steps
1. Fix the 2D `pixel_size_from_metadata` handoff; route all 10 sites through
   `_calibration_is_from_metadata`; add the guard test. **Ship this alone** — it is the reported bug.
2. Parse `PageName VoxelSize=` for pixel size/z-step with cross-check + conflict reporting.
3. Sidecar discovery layer: stem rules, bounded parallel probe, deadline, non-gating, merge with
   per-field source recording, parser registry.
4. ISS Vista `_fbs.xml` parser + channel↔file mapping + modality justification.
5. Feed sidecar channel identity into the naming tiers above pixel classification.
6. Last-resort dialog + persistence via the extended `channel_designations` store + session round-trip.
7. Tests above; full `pytest -m core` green.
8. Ship each numbered step as its own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- A 2D TIFF with metadata pixel size shows a **µm** scale bar; the flag is set from provenance, from one
  helper, guarded against divergence.
- `z_step_um` is recovered from `PageName`; in-file source conflicts are reported.
- Sidecars are discovered in parallel, bounded, and **never gate the load**; found fields merge with
  recorded provenance and flagged conflicts.
- ISS `_fbs.xml` yields per-channel emission/detector/pinhole, lasers, objective and dwell time; the
  channels are named from their emission bands and **never labelled brightfield**.
- A genuinely orphaned file prompts once for channel identity; answers persist by acquisition signature
  and through sessions, are reversible, and never overwrite real metadata.
- Full `pytest -m core` green.

## Cautions
- **Sidecar discovery must never gate a load.** Bounded, deadlined, off-thread, failure-tolerant — the
  image opens regardless.
- **Never crawl beyond the file's own directory**; no recursion, hard caps.
- **Never guess a channel↔file mapping.** Ambiguity leaves fields empty and defers to the dialog.
- **The dialog is a last resort.** If it appears when evidence exists, the trigger is wrong — fix the
  trigger, do not widen the dialog.
- **Reuse `channel_designations`** (signature-keyed, path-independent) — do not build a second store.
- **Missing stays missing**; skipped dialog fields are `None`, never defaults.
- **User answers outrank guesses, never real metadata** found later.
- Step 1 is the user-visible regression — ship it first and alone, before the larger sidecar work.
