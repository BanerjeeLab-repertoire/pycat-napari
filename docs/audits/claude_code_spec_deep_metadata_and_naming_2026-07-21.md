# Claude Code spec — Deep metadata extraction (reader-independent) + filename-aware channel naming

> **◐ STATUS — Part 3 DONE (1.6.280); Part 1 DONE (1.6.294 + 1.6.295); Part 2 merge policy DONE (1.6.296).
> Remaining: deepen CZI/LIF/ND2/IMS readers to the same `channels` shape (item 4), the Qt provenance/conflict
> surface, full dispatcher adoption of the merge, and lazy/bounded per-page TIFF metadata.**
>
> **Part 2 (reader-independent merge) — merge policy DONE, shipped 1.6.296.** New `merge_metadata_sources`
> (pure, Qt-free) merges an ordered `(name, common)` list by field-level precedence (first *meaningful* value
> wins, `is_meaningful` reused so `'N/A'` never beats a real value), records per-field provenance, and reports
> every two-source disagreement as a finding (both values + winner + reason; `pixel_size_um` conflicts marked
> `surfaced` for the gate; rounding within 1e-3 is not a conflict). `extract_metadata_merged(file_path, *,
> sources=None)` orchestrates it and **skips a failing source with a recorded reason** (`raw['source_failures']`)
> instead of the old bare `except: pass`; the merge trail rides in `raw['metadata_sources']` /
> `['metadata_conflicts']` / `['raw_by_source']`. Additive — `extract_metadata` unchanged. `sources` injectable
> for testing. `tests/test_metadata_merge.py` (`core`, 8 tests). **Remaining:** wiring the dispatcher to prefer
> the merged result, the Qt surface for provenance/conflicts, and lazy/bounded per-page TIFF metadata (item 6 —
> the `_extract_mm_frame_times_from_tiff` `max_pages` bounding already exists as the pattern).
>
> **Part 1 (element-scoped OME parse) — DONE, shipped 1.6.294.** New `_parse_ome_xml_scoped` reads each
> attribute from the element it belongs to (`ElementTree`, namespace-agnostic): the first `<Pixels>` element's
> geometry/`Type`/`DimensionOrder`, the `<AcquisitionDate>` **child element**, the first `<Plane>` exposure —
> fixing `Pixels/@Type` resolving to `PMT` from a preceding `<Detector Type="PMT">` and multi-image
> first-match cross-contamination. The scoped value wins; the whole-string regex remains a gap-filler so
> nothing already-correct regresses; unparseable OME → `{}` → regex fallback (never crashes). OME-XML is now
> detected BEFORE the ImageJ `key=value` branch (line-wrapped attributes were mis-parsed as key=value).
> `tests/test_ome_xml_scoped_parse.py` (`core`, 6 tests).
>
> **Part 1 (per-channel + instrument schema) — DONE, shipped 1.6.295.** New `parse_ome_channels_and_instrument`
> (Qt-free, additive) exposes the `channels` list (per-`<Channel>` name/fluor/excitation/emission/contrast/
> acquisition-mode/detector-id/gain/offset/binning/amplification-gain/color, with detector settings resolving
> DetectorSettings-then-referenced-Detector) and the `instrument` block (lens_na, nominal_magnification,
> immersion, medium, refractive_index, dimension_order — the oil-vs-air contradiction recorded, not resolved).
> Numeric coercion is int/float/None (missing stays missing). Wired into `extract_reader_metadata`
> (`raw['channels']`/`raw['instrument']`; objective NA lifts into flat `common['numerical_aperture']`).
> `tests/test_ome_channels_and_instrument.py` (`core`, 7 tests). **Item 4 (deepen CZI/LIF/ND2/IMS readers to
> the same `channels` shape) and item 5's Qt surface remain as follow-ons.** **Part 2** (reader-independent
> `extract_metadata_merged`) remains.
>
> **Part 3 — DONE.** `channel_naming.identify_channel` gained a `file_stem` argument, a **Tier 1c** (match
> the stem with the existing `_match_fluorophore_name`, below real metadata but above the pixel/position
> guess — `Image1-GFP.tif` → `EGFP` source `filename`; `Image1-DAPI.tif` → `DAPI`, distinct, no `(1)`), and
> the **never-worse-than-input rule** (`_stem_distinguishing_text` keeps the user's text over a generic
> modality word when nothing else matched, retaining the colormap bucket). The stem is threaded from all
> four open paths (`image_reader_2d` + the IMS/structured/CZI-streaming openers). Real metadata still wins,
> a purely generic stem still falls through to pixel classification, and no-stem callers are unchanged.
> `tests/test_channel_naming_filename.py` (`core`, 6 tests); existing naming/extraction tests pass. Full
> core green (1710).
>
> **Parts 1–2 REMAIN** (larger, and they touch `metadata_extract.py` which feeds the pixel-size gate —
> "do not change any value that is already correct"): Part 1 (scoped per-element OME parse fixing the
> `Pixels/@Type=='PMT'` bug, the per-channel `channels` schema, the instrument/objective block, contradiction
> flagging) and Part 2 (`extract_metadata_merged` — decouple the metadata source from the pixel reader, with
> per-field provenance + conflict reporting + bounded/lazy per-page TIFF metadata). Part 1 needs a real
> Zeiss OME-TIFF fixture.

**Date:** 2026-07-21 · **Target tree:** 1.6.269 · Verified against the 1.6.269 tree. Three joined
problems, all confirmed in code: (1) metadata is parsed **shallowly** for every format — an OME-TIFF
carrying 29 distinct attributes yields 6, one of them **wrong**; (2) the metadata reader is **coupled
to the pixel reader**, so the format that displays best dictates what metadata you get; (3) channel
naming **never sees the filename**, so `Image1-GFP.tif` is renamed `Image1-Fluorescence` — actively
worse than the name the user gave it.

---

## Part 1 — The shallow-parse problem (verified on a real file)

`metadata_extract.py:136` — the OME-XML branch is a regex over the whole XML string for **10 fixed
attribute names**, taking `re.search`'s **first match**:

```python
for attr in ('PhysicalSizeX','PhysicalSizeY','PhysicalSizeZ','TimeIncrement',
             'SizeT','SizeC','SizeZ','Type','ExposureTime','AcquisitionDate'):
    m = _re.search(rf'{attr}="([^"]+)"', s)
```

On a real Zeiss LSM OME-TIFF (`Image_3-OME_TIFF-Export-01_ome.tiff`) this captures **6 of 29**
attributes — and **`Type` resolves to `"PMT"`**, because `<Detector Type="PMT">` precedes
`<Pixels Type="uint16">` in the document. The pixel data type is silently recorded as a detector
category.

**Two distinct defects:**
- **First-match-wins across the whole document.** Any attribute name appearing on more than one element
  returns whichever comes first. On a multi-image/multi-position OME file, `PhysicalSizeX` silently
  takes image 0's value and applies it to all — a calibration error of exactly the kind the pixel-size
  gate exists to catch.
- **A fixed 10-name allow-list.** Everything else in the file is discarded even when present.

**Discarded on that file despite being present** (with why each matters):

| attribute | value | consumer that wants it |
|---|---|---|
| `LensNA` | 1.4 | Nyquist / diffraction-limit QC (`numerical_aperture` is currently None) |
| `NominalMagnification` | 63 | pixel-size sanity; acquisition profiles |
| `Immersion` / `RefractiveIndex` | Oil / 1.518 | PSF modelling, spherical-aberration QC |
| `ExcitationWavelength` / `EmissionWavelength` | 405/447, 488/516 | **per channel** — currently flattened to one |
| `Fluor` | DAPI, EGFP | channel identity (better than guessing) |
| `AcquisitionMode` | LaserScanningConfocal | modality gating — which scan-QC checks apply |
| `ContrastMethod` | Fluorescence ×2, **Other** ×1 | identifies ch3 as the transmitted PMT |
| `Gain` per detector | 600, 550, 336.8 | **calibration fingerprint** — gain mismatch is a hard block in `check_calibration_validity` |
| `Offset`, `Binning`, `AmplificationGain` | 0, 1x1, 1/1.2 | detector calibration |
| `Color` | 65535, 16711935 | channel display colours |
| `DimensionOrder` | XYZTC | axis interpretation |

**The structural point:** OME metadata is **hierarchical and per-channel**
(Instrument → Detector → Channel → DetectorSettings). The flat `_empty_common` schema
(`'excitation_nm': <one value>`) cannot express *"Ch1 = 405/447 DAPI on Detector:1 at gain 600;
Ch3 = transmitted PMT at gain 336.8."* Those three channels have genuinely different acquisition
parameters, which is precisely what the calibration fingerprint and modality gating need.

**Assume every format is shallow.** Verified field counts: `extract_ims_metadata` ~18,
`extract_tiff_metadata` ~16, `extract_reader_metadata` ~21 assignments — all filling the same flat
`_empty_common` schema regardless of what the file carries. CZI, LIF, ND2 and IMS all carry far more.

### The fix
1. **Parse properly, scoped to the element.** Use a real XML parse (`xml.etree`, or `ome-types` which
   is already reachable via the bioio stack) and read attributes **from the element they belong to** —
   `Pixels/@Type`, not the first `Type=` in the file. This alone fixes the `PMT`/`uint16` bug.
2. **Add a per-channel schema** alongside the existing flat one:
   ```python
   'channels': [
       {'index':0, 'name':'Ch1-T1', 'fluor':'DAPI', 'excitation_nm':405, 'emission_nm':447,
        'contrast_method':'Fluorescence', 'acquisition_mode':'LaserScanningConfocalMicroscopy',
        'detector_id':'Detector:1', 'gain':600, 'offset':0, 'binning':'1x1',
        'amplification_gain':1.0, 'color':65535},
       ...
   ]
   ```
   **Additive** — keep `excitation_nm`/`emission_nm` at top level (populate from channel 0 as today) so
   every existing consumer is untouched.
3. **Add the instrument/objective block:** `lens_na`, `nominal_magnification`, `immersion`,
   `refractive_index`, `dimension_order`, light sources.
4. **Deepen every other reader to parity** — CZI/LIF/ND2 via the structured reader's OME model, IMS via
   its HDF5 attribute tree. Same `channels` list shape from every format.
5. **Surface contradictions, don't resolve them.** This file has `Objective Immersion="Oil"` but
   `ObjectiveSettings Medium="Air"` with `RefractiveIndex="1.518"` (oil's RI) — a real Zeiss ZEN export
   inconsistency. Record both and flag the conflict; **never silently pick one**.
6. **Missing stays missing.** An absent attribute is `None`, never a default. (The
   `test_pixel_size.py` "unknown is NaN not one" contract generalised to all fields.)

---

## Part 2 — Decouple the metadata reader from the pixel reader

**The best reader for pixels is not always the best for metadata.** Today they're the same choice, so a
format that displays well can lock you out of metadata another library would parse fully.

The seed already exists — `extract_metadata` (line ~877) does exactly this for one field:

```python
result = extract_tiff_metadata(file_path)
if result['common'].get('pixel_size_um') is None:
    from_reader = extract_reader_metadata(file_path)   # a DIFFERENT reader
    if ...: result['common']['pixel_size_um'] = ...
```

But it's hardcoded to a single field, TIFF-only, and wrapped in a bare `except: pass`.

### Generalise it to a merge policy
```python
def extract_metadata_merged(file_path, *, pixel_reader=None) -> dict
```
- Ask **each available metadata source** (tifffile tags, OME-XML, structured reader/ome-types, IMS
  HDF5, BioFormats when installed) for what it can parse. Metadata is a **one-time pull**, so trying
  more than one source is cheap relative to a load.
- Merge by **field-level precedence**, and **record the source per field**
  (`pixel_size_source` already does this — generalise the pattern to every field). A user must be able
  to see *"NA came from OME-XML; frame interval came from MicroManager tags."*
- **The pixel reader is unaffected.** Display continues through whichever reader handles the pixels
  best; metadata comes from wherever it parses best. They are independent choices.
- **A source that fails is skipped with a reason, not swallowed** — replace the bare `except: pass`
  with a recorded, inspectable failure (this is a `# broad-ok:` candidate at most, and it should say
  which source failed).

### Conflict rule
When two sources give **different values for the same field**, that is a **finding**: record both plus
the winner and the reason. Do not silently prefer one. For `pixel_size_um` specifically a conflict must
surface to the pixel-size gate rather than being resolved quietly — a wrong scale corrupts every
physical measurement.

### Per-page metadata (multipage TIFF)
Multipage TIFFs carry **per-page** tags (per-plane exposure, timestamps, positions). The one-time-pull
model holds for file-level metadata, but per-page data is per-plane. Capture per-plane records
**lazily** — read page tags on demand, or sample bounded pages at load (the `_extract_mm_frame_times_from_tiff`
`max_pages` pattern already does this). **Never scan every page of a large stack at load time.**

---

## Part 3 — Filename-aware channel naming (the "name gets worse" bug)

**Verified: `channel_naming.py` contains zero references to the filename.** `derive_name` has no
`file_stem` parameter. Its precedence is:

```
Tier 1  fluorophore_name (OME Fluor)   → 2D TIFF: absent
Tier 1b channel_name                    → absent
Tier 2  emission wavelength             → absent
Tier 2b excitation + PMT-ish name       → absent
Tier 2c CLASSIFY THE PIXELS             → 'fluorescence' → label "Fluorescence"   ← fires here
Tier 3  position fallback
```

So for `Image1-GFP.tif` + `Image1-DAPI.tif`: metadata is empty, Tier 2c classifies both as
fluorescence, both become `Image1-Fluorescence`, and the second collides → `Image1-Fluorescence(1)`.
**"GFP" and "DAPI" were in the filenames and were never consulted.** The user's own labelling — often
the *only* channel identity a plain 2D TIFF has — is discarded in favour of a generic pixel guess, and
then a meaningless disambiguator is appended.

### The fix
1. **Add `file_stem` as an input** to `derive_name`.
2. **Insert filename matching as a new Tier 1c — above pixel classification**, below real metadata:
   ```
   Tier 1   OME Fluor / metadata fluorophore
   Tier 1b  metadata channel name
   Tier 1c  FILENAME  ← new: match _match_fluorophore_name against the file stem
   Tier 2   emission wavelength
   Tier 2b  excitation + transmitted heuristic
   Tier 2c  pixel classification
   Tier 3   position
   ```
   `_match_fluorophore_name` already recognises DAPI/EGFP/GFP/etc. — **reuse it on the stem**; do not
   write a second matcher. `Image1-GFP.tif` → matches GFP → label `GFP`.
3. **Never produce a name worse than the input.** If nothing matches, **keep the user's stem** rather
   than replacing it with a generic modality word. A file the user named `Image1-GFP` should never come
   back as `Image1-Fluorescence`. Concretely: when the derived label's only source is `pixels` or
   `position` **and** the stem carries distinguishing text, prefer the stem.
4. **Collisions must be meaningful.** `(1)` is a last resort. Two files whose stems already differ
   should keep those distinctions — the disambiguation problem largely disappears once the stem is used.
   If a numeric suffix is still needed, it means two genuinely identical names, which is worth a log
   line.
5. **Record the source** (`source: 'filename'`) so the naming is inspectable like every other derived
   value.

---

## Tests (`core`)

**Deep parse:**
- The real OME-TIFF fixture yields `Pixels/@Type == 'uint16'` — **not `'PMT'`** (the scoped-parse
  regression test).
- `lens_na=1.4`, `nominal_magnification=63`, `immersion='Oil'`, `refractive_index=1.518` are extracted.
- `channels` has 3 entries with the correct per-channel excitation/emission/fluor/gain
  (405/447/DAPI/600, 488/516/EGFP/550, transmitted/336.817474).
- Channel 3 is identified as non-fluorescence via `ContrastMethod='Other'`.
- The Immersion="Oil" vs Medium="Air" contradiction is **flagged**, not silently resolved.
- A multi-image OME file does **not** take image 0's `PhysicalSizeX` for all images.
- Absent attributes are `None`, never defaulted.
- Every reader (TIFF/OME/CZI/IMS) returns the same `channels` list shape.

**Reader independence:**
- A file whose pixel reader parses metadata poorly still gets full metadata from the better source.
- Each merged field records its source; a two-source conflict records both + winner + reason.
- A failing source is skipped with a recorded reason, not a silent `except: pass`.
- Per-page TIFF metadata is read lazily/bounded — loading does not scan every page (assert page count
  read).

**Naming:**
- `Image1-GFP.tif` → `GFP` (source `filename`), **not** `Fluorescence`.
- `Image1-GFP.tif` + `Image1-DAPI.tif` → `GFP` and `DAPI`, **no `(1)` suffix**.
- Real metadata still beats the filename (a file named `...GFP` whose OME says `Fluor=DAPI` → DAPI).
- A stem with no recognisable fluorophore keeps the user's stem rather than becoming a generic label.
- Pixel classification still fires when there is genuinely nothing else.
- The existing naming tests pass unmodified.

---

## Steps
1. Scoped XML parse replacing the regex block; fix `Pixels/@Type`; add the instrument/objective block.
2. Per-channel `channels` schema (additive; top-level fields unchanged).
3. Deepen IMS / CZI / structured-reader extractors to the same schema.
4. Contradiction flagging (immersion/medium and any other cross-field conflict).
5. `extract_metadata_merged` — multi-source, field-level precedence, per-field source recording,
   conflict reporting; replace the bare `except: pass`.
6. Lazy/bounded per-page TIFF metadata.
7. `file_stem` input + Tier 1c filename matching + never-worse-than-input rule + source recording.
8. Tests above; full `pytest -m core` green.
9. Ship: version(s) + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Definition of done
- OME/CZI/IMS/TIFF metadata is parsed from the correct elements, with a per-channel record and the
  instrument/objective block; `Pixels/@Type` is the pixel type.
- Metadata sources are independent of the pixel reader, merged with recorded per-field provenance and
  reported conflicts.
- Per-page TIFF metadata is bounded/lazy.
- `Image1-GFP.tif` names itself `GFP`; a derived name is never worse than the user's filename.
- No currently-correct value changes; full `pytest -m core` green.

## Cautions
- **Do not change any value that is already correct** — especially `pixel_size_um`; the pixel-size gate
  depends on it. Assert the existing pixel-size tests pass unmodified.
- **Scope every attribute read to its element** — the first-match bug is the root cause; a wider
  allow-list on the same regex would make it worse, not better.
- **Missing stays missing.** Never default a field to make the schema look complete.
- **Surface contradictions; never silently resolve them** — that includes source conflicts on merge.
- **Never scan every page** of a large multipage TIFF at load; bounded sampling or on-demand only.
- **Never produce a name worse than the user's filename** — that is the whole point of Part 3.
- Additive schema: the flat top-level fields stay so existing consumers are untouched.
