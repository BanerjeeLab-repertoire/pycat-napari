# Claude Code spec — File-I/O decomposition #4: `save_and_clear_all` write loop → `writers.py`

> **✅ STATUS — DONE, shipped in 1.6.59** (git commit 36fdf01; predates the current CHANGELOG, which starts
> at 1.6.103). `src/pycat/file_io/writers.py::write_session_outputs` is the pure, extracted write loop, and
> `file_io.py` imports and calls it. Every Definition-of-done item met. (A later `Unnamed: 0` CSV-index
> quirk was found and handled separately in 1.6.125 — not a gap against this spec.)

**Date:** 2026-07-15 · **Target tree:** 1.6.58 · Verified against the uploaded 1.6.58 tree.

## Read first
- `docs/audits/fileio_godclass_roadmap_2026-07-15.md` — piece **#4** of 5. Its line:
  *"`save_and_clear_all` (183) → writers/. The `_save_layer` stub already points at writers.py, so the
  seam exists."*
- Pieces #1/#2 (readers) done + shipped; #3 (IMS reader) may be in progress — check `git log`.

## HONEST SCOPING (read this before estimating the work)
Unlike the reader pieces, **the write path is ALREADY mostly extracted.** Verified in 1.6.58:
- `_save_layer` (file_io.py @ 3440) is already a thin stub forwarding to `writers._save_layer`.
- Dataframe CSVs already use `writers.atomic_write`.
- The session manifest already delegates to `session_manifest.write_manifest`.
- Tag persistence already uses `writers._apply_saved_tags_to_layer`.

So `save_and_clear_all` (@ 3211, ~229 lines) is now **~90% orchestration** — three dialogs
(`LayerDataframeSelectionDialog`, `QFileDialog` save, batch-export `QMessageBox`), batch-recorder
`record()`/`terminate_recording()`, viewer clearing, workflow-checklist reset. There is NOT 183 lines
of pure write logic to lift. **Do not invent work.** #4 is a **small, surgical** extraction: pull the
*output-writing loop* into one pure function in `writers.py`, leaving all Qt/dialog/clear/batch
orchestration in the controller.

## The seam: what moves vs what stays
**MOVES → `writers.py::write_session_outputs(...)`** (pure, Qt-free — takes already-decided inputs,
does the file writes):
The block from ~line 3300 (`layer_names = [...]`) through ~line 3366 (manifest write), i.e. the
`with warnings.catch_warnings():` output loop:
- the per-selected-layer save loop (calls `writers._save_layer`, builds `_manifest_layers`),
- the per-selected-dataframe CSV loop (`atomic_write` + `to_csv`, builds `_manifest_dfs`),
- the metadata JSON export,
- the `session_manifest.write_manifest` call.

Signature shape (pure inputs, no viewer/dialog):
```
def write_session_outputs(central_manager, layers_by_name, selected_layers,
                          selected_dataframes, dataframes, file_metadata,
                          save_name, session_dir, source_path, stem) -> dict:
    # returns {'manifest_layers': [...], 'manifest_dfs': [...]}  (for logging/tests)
```
It resolves each selected layer's `data`/`layer_type`/`safe_name`/tag-store, calls
`writers._save_layer`, writes the dataframe CSVs, the metadata JSON, and the manifest. It must NOT
touch the viewer, dialogs, or clearing.

**STAYS in `save_and_clear_all` (controller orchestration):**
- The `LayerDataframeSelectionDialog` + `clear_without_saving` branch + `get_selections()`.
- The `QFileDialog` save-path prompt + base-name logic + `save_name` derivation.
- The session-folder creation (`session_manifest.default_session_dir` + `mkdir`) — decides
  `session_dir`/`save_name`; PASS the result into `write_session_outputs`.
- The `bp.record('save_and_clear', ...)` call.
- ALL the clear logic (`clear_all` branch, `persist_measurements` save/restore, `select_all`/
  `remove_selected`, `reset_values`).
- The workflow-checklist reset + the batch-export `QMessageBox` + `terminate_recording()`.

After extraction, `save_and_clear_all` gathers inputs, creates the session dir, then calls
`write_session_outputs(...)`, then does the clear/reset orchestration with the returned manifest info
already written.

## Steps
1. Add `write_session_outputs(...)` to `writers.py` (it already imports/holds `atomic_write`,
   `_save_layer`, `_apply_saved_tags_to_layer`; it will import `session_manifest.write_manifest` and
   `json`). Move the write-loop body VERBATIM, re-parameterised to the pure signature (replace
   `self.viewer.layers[name]` → `layers_by_name[name]`, `self.central_manager...get_dataframes()` →
   the passed `dataframes`, `self.base_file_name`/`self.filePath` → passed `stem`/`source_path`).
2. In `save_and_clear_all`, replace the `with warnings.catch_warnings(): …` output block with a call
   to `write_session_outputs(...)`, passing `{l.name: l for l in self.viewer.layers}` as
   `layers_by_name`, the metadata from the repo, and the `session_dir`/`save_name` already computed
   above. Keep the `warnings.catch_warnings()` suppression INSIDE `write_session_outputs` (it wraps
   the skimage save warnings).
3. Compile: `python -c "import pycat.file_io.file_io; import pycat.file_io.writers"`.
4. Test `tests/test_writers_session_outputs.py`: with fake layers (objects exposing `.name`, `.data`,
   `.metadata`) + a couple of small DataFrames + a temp dir, call `write_session_outputs` and assert
   the expected files land (layer files, `_<df>.csv` with correct row counts, `_metadata.json`, and
   the manifest), and the returned `manifest_layers`/`manifest_dfs` match. This is a PURE test — no Qt,
   no viewer.
5. GUI confirm: run `run-pycat`, load something, run a workflow, hit Save & Clear — confirm the
   session folder + files + manifest are written and the clear/reset still happens, AND that Load
   Session restores it (round-trip, since this is the save half of the 1.6.52 session feature).

## Definition of done
- `writers.py::write_session_outputs` owns the file writes; `save_and_clear_all` owns dialogs + clear +
  batch orchestration only.
- New pure `test_writers_session_outputs.py` passes; existing session tests still pass.
- Save & Clear → Load Session round-trip works in the GUI (the 1.6.52 feature — don't regress it).
- Shipped: own version bump + PyPI push + commit (EXPLICIT filenames) + CHANGELOG.

## Cautions
- The atomic-CSV write (`atomic_write` → temp then rename) exists because a **truncated CSV is the
  worst failure** (opens, parses, silently short). Keep `atomic_write`; do not "simplify" to a direct
  `to_csv(path)`.
- The manifest write is what makes **Load Session** work (1.6.52/1.6.53). If you change the manifest
  fields, the loader (`session_loader.py` + `ui_modules._open_session_loader`) must still read them —
  don't touch the manifest schema in this piece; just move the call.
- `save_name` gets REASSIGNED to the in-session path (~line 3296) before the write loop — make sure
  `write_session_outputs` receives the FINAL in-session `save_name`, not the pre-folder one.
- Do NOT touch `_open_stack_generic` (#5, the 542-line monster, LAST) or the CZI/IMS specs
  (`claude_code_spec_czi_*`, `claude_code_spec_fileio3_ims_*`) — all edit file_io.py; do them ONE AT A
  TIME, commit between, to avoid colliding in file_io.py.
