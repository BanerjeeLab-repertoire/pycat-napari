## [1.6.96] - 2026-07-17
### Added — **Comparative phenotyping increment 2: the consolidated long-format table (the keystone).**
Batch wrote one folder per image and nothing at the top level, so a study across N mutants was N
folders of disconnected CSVs joined by hand — the error-prone manual step PyCAT exists to remove. New
`utils/consolidated_table.py` assembles one tidy table:

    image_stem | <condition fields> | object_type | object_id | measurement | value | units |
                 channel | frame | pixel_size_um | pycat_version | operation_id

- **Long (tidy), not wide** — one measurement/value/units triple per row, the substrate grouped stats
  and faceting need. Wide is a pivot away; long is not recoverable from wide.
- **Condition labels joined per row** from increment 1's `SampleMetadata` — every measurement knows
  its mutant/dose/replicate. An absent condition field is **blank, never fabricated** (the honesty
  contract; a comparison across a guessed label is silently wrong).
- **Provenance travels per row** (pixel size, version, operation, channel, frame) — traceable and
  self-describing by construction.
- **Streaming** — `ConsolidatedLongWriter` appends each image and holds no other in memory (a
  200-image batch keeps counters, not rows); the schema is fixed from the condition vocabulary up
  front, so append never drifts a column.
- **Additive** — this sits alongside the per-image folders; it removes nothing.

The assembly (`melt_object_measurements`, `build_image_long_table`), the streaming writer, and the
`records_from_data_repository` extractor (per-object tables keyed `<type>_df`) are all pure and tested
headlessly — assembly correctness is not trusted to a GUI run.
### Notes
- **The batch-loop wiring is on branch `phenotyping-batch-wire`, NOT here** — it edits the Qt-bound
  `BatchWorker`, which cannot be exercised without a real batch run, and this session cannot drive one
  (the same boundary as the worker-thread task). The keystone *logic* ships to main, fully verified; a
  user runs the wiring branch to confirm a live batch emits `consolidated_long.csv` correctly, then it
  merges. The library is usable directly today (point `ConsolidatedLongWriter` at per-image outputs).
- The calibration columns increment 1's spec anticipated (`dense_concentration_uM`, `Kp`,
  `dG_transfer_kcal_mol`, 1.6.94) melt into this table like any other measurement — the two increments
  compose.
- Increment 3 (comparative faceted figures with honest stats) is a view over this table, not built
  here.

