# Claude Code spec — Comparative phenotyping increment 1: the condition/metadata model

> **✅ STATUS — DONE.** Part A (the resolver / condition-metadata model) shipped in 1.6.95; Parts B & C
> (batch-loop + session-manifest wiring) in 1.6.133. (1.6.95 predates the current CHANGELOG, which starts at
> 1.6.103; 1.6.133 is present and references Part A.) `src/pycat/utils/sample_metadata.py` provides the
> frozen `SampleMetadata` dataclass, `SampleMetadataResolver.for_image()` (precedence + field-level merge),
> `load_sample_sheet`, and safe `parse_filename` (named-group regex, no eval), plus batch/manifest wiring
> helpers. Pinned by `tests/test_sample_metadata.py` + `tests/test_sample_metadata_wiring.py`. Every
> Definition-of-done item met.

**Date:** 2026-07-17 · **Target tree:** 1.6.90 · Verified against the 1.6.90 tree. First increment of
the comparative-phenotyping roadmap (`roadmap_comparative_phenotyping_2026-07-16.md`). Builds the
metadata layer that attaches condition/perturbation labels to data — the prerequisite for the
consolidated table (increment 2) and everything after. Additive; no behaviour change to existing
per-image output. Touches a new `utils/sample_metadata.py`, `batch_processor.py`,
`session_manifest.py`. Not `file_io.py` core.

## Why this first
Verified: batch writes one subfolder per image (`batch_processor.py:255` `file_output = output_dir /
image_path.stem`), iterating images at `:245`. There is **no condition/perturbation concept anywhere**
(no sample_sheet / genotype / SampleMetadata in the codebase). Nothing can be comparative without a way
to say "this image is WT replicate 2 at 10 µM." This increment supplies exactly that, from three
sources, behind one resolver.

## The model (Gable: all three attach paths available)
A condition label attaches to an image via ANY of three sources, resolved by one `SampleMetadata` API
so nothing downstream cares which supplied it:

1. **Sample sheet (primary).** A CSV the user points batch at: one row per image, columns
   `filename` (or `stem`) + arbitrary condition fields (`genotype, treatment, replicate, dose, …`).
   Batch joins by filename/stem. Arbitrary columns — do NOT hardcode a fixed schema; whatever columns
   the sheet has become the condition fields.
2. **Filename / folder parse (fallback).** A configurable pattern (e.g.
   `{genotype}_rep{replicate}_{dose}uM`) that extracts fields from the stem when no sheet row exists.
   A small, safe parser (named-group regex or a simple `{field}` template compiled to regex) — NOT
   `eval`. Fields it can't fill are left absent, not guessed.
3. **In-app tag (interactive).** A per-image/session condition dict the user sets in the app, stored
   in the manifest. `session_manifest.write_manifest` (`session_manifest.py:143`) already persists a
   `data_repository`-derived blob — extend it with a `sample_metadata` field so an in-app tag round-
   trips with the session.

**Precedence** (explicit beats inferred): sample-sheet row > in-app tag > filename parse >
`{}` (unlabelled). The resolver returns the merged condition dict for a given image.

## Part A — `utils/sample_metadata.py` (the resolver)
```python
@dataclass(frozen=True)
class SampleMetadata:
    fields: dict[str, str]          # {'genotype': 'WT', 'replicate': '2', 'dose': '10', ...}
    source: str                     # 'sample_sheet' | 'filename' | 'in_app' | 'none' (provenance)

class SampleMetadataResolver:
    def __init__(self, sheet_path=None, filename_pattern=None, in_app_tags=None): ...
    def for_image(self, image_path) -> SampleMetadata:
        # apply precedence; merge field-by-field (a sheet row can supply some fields,
        # filename parse fill others); record which source won per field if cheap.
```
- `load_sample_sheet(path) -> dict[str, dict]` — stem → condition dict; validate the filename/stem
  column exists; tolerate extra columns; warn (don't crash) on rows that match no image and images
  that match no row.
- `parse_filename(stem, pattern) -> dict` — compile the `{field}` template to a named-group regex;
  return the captured fields or `{}` on no match.
- Never invent a field value — absent stays absent (consistent with the NaN-not-a-lie contract).

## Part B — wire into batch
In `batch_processor` (the `for … image_path` loop at `:245`), construct a `SampleMetadataResolver`
once (from a sample-sheet path / pattern the batch UI supplies) and call `resolver.for_image(path)`
per image. Attach the resulting condition dict to that image's results (a `sample_metadata` dict on the
per-image output for now — increment 2 turns it into consolidated-table columns). Additive: if no
sheet/pattern/tag is provided, every image resolves to `source='none'`, `fields={}`, and batch behaves
exactly as today.

## Part C — in-app tag persistence
Extend `session_manifest` to store/restore a `sample_metadata` dict, so a user who tags an image
in-app has it travel with the session and be available to `SampleMetadataResolver(in_app_tags=…)`.
Back-compat: a manifest without the field loads as no tag.

## Steps
1. `utils/sample_metadata.py`: `SampleMetadata` + resolver + sheet loader + filename parser.
2. Wire the resolver into the batch loop; attach `sample_metadata` per image (additive).
3. Extend `session_manifest` with the `sample_metadata` field (write + read, back-compat).
4. Tests (`core`, pure): all three sources yield the same condition dict for an image; precedence is
   honoured (sheet overrides parse overrides nothing); field-level merge works (sheet supplies some,
   filename fills others); an unmatched sheet row / unmatched image warns not crashes; a bad filename
   pattern returns `{}` not an exception; the manifest round-trips a tag; no-metadata batch is
   unchanged.
5. Full `pytest -m core` green (complexity budget — keep the resolver small).
6. Ship: own version + PyPI push + commit (EXPLICIT filenames) + CHANGELOG (comparative phenotyping
   inc 1: condition/metadata model, three attach paths behind one resolver, additive).

## Definition of done
- A `SampleMetadataResolver` returns a condition dict for any image from sample-sheet / filename /
  in-app tag, with explicit precedence and field-level merge.
- Batch attaches `sample_metadata` per image when a source is provided; is byte-unchanged when none is.
- In-app tags persist in the manifest and round-trip.
- Never fabricates a field value; warns rather than crashes on mismatches.
- Full `pytest -m core` green.

## Cautions
- Arbitrary condition columns — do NOT impose a fixed genotype/treatment schema; whatever the sheet
  has is the vocabulary.
- Absent field stays absent (no guessed defaults) — the same honesty contract as pixel-size/z-step.
- Additive only — a batch with no metadata source must behave exactly as it does today.
- Filename parsing must be a safe template→regex, never `eval`.
- This increment does NOT build the consolidated table (increment 2) or any figure — just the metadata
  layer the table will join on. Stop at attaching the dict.
