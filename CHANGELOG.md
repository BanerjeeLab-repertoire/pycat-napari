## [1.6.95] - 2026-07-17
### Added — **Comparative phenotyping increment 1: the condition/metadata model.**
Nothing in PyCAT could be *comparative*: batch writes one folder per image and there was no
condition/perturbation concept anywhere, so a study across N mutants was N folders of disconnected CSVs
stitched by hand. New `utils/sample_metadata.py` is the metadata layer the consolidated table
(increment 2) joins on — a condition label reaches an image from **any of three sources, behind one
resolver**:

- **Sample sheet (primary)** — a CSV with `stem`/`filename` + arbitrary condition columns; whatever
  columns it has *are* the vocabulary, nothing hardcoded.
- **Filename parse (fallback)** — a `{field}` template (e.g. `{genotype}_rep{replicate}_{dose}uM`)
  compiled to a named-group regex; literal text is escaped, so it is a safe template→regex, never
  `eval`.
- **In-app tag (interactive)** — persisted in the session manifest and round-tripped.

**Precedence, explicit beating inferred:** sheet > in-app tag > filename > nothing. The merge is
**field by field** — a sheet row can supply `genotype` while the filename fills `dose` — and each
field records which source won (`field_sources`), so provenance is never guessed.

The rule under everything: **an absent field stays absent.** No default, no guess — the same honesty
contract as the pixel-size gate. A fabricated condition is worse than a missing one, because a
comparison across it would be silently wrong. Blank sheet cells, unmatched rows, and broken patterns
warn rather than crash or fabricate.
### Notes
- **Scope choice vs the spec:** the spec put "wire into batch" in this increment. The roadmap has
  increment 2 touching the same batch loop to emit the consolidated table, so rather than build a
  per-image attach that increment 2 immediately supersedes, increment 1 ships the **pure metadata
  substrate** (resolver + manifest persistence, fully tested headlessly) and the batch wiring lands in
  increment 2 alongside the table it feeds — `BatchWorker` is touched once, for its real consumer.
- In-app tag persistence rides the manifest's existing `extra=` merge (`tags_to_manifest_extra` /
  `tags_from_manifest`); a manifest written before this field loads as "no tag", not an error.
- All `core` (pure, headless) — the metadata layer is a standalone module by design.

