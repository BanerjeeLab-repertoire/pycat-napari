"""**One tidy table for a whole batch — the keystone comparative-phenotyping output.**

Batch writes one folder per image, each with its own wide CSVs, and nothing at the top level. A study
across N mutants is therefore N folders the scientist joins by hand — the exact error-prone manual step
PyCAT exists to remove. This assembles, at the top of a batch, **one long-format table**:

    image_stem | <condition fields> | object_type | object_id | measurement | value | units |
                 channel | frame | pixel_size_um | pycat_version | operation_id

- **Long (tidy), not wide.** One `measurement`/`value`/`units` triple per row — the substrate grouped
  stats and faceting need. Wide is a pivot away; long is not recoverable from wide.
- **Condition labels joined per row** from increment 1's `SampleMetadata`, so every measurement knows
  which mutant/dose/replicate it belongs to.
- **Provenance travels per row** (pixel size, version, operation, channel, frame) — the
  metadata-awareness made automatic, so a consolidated row is traceable and self-describing.
- **Streaming.** A 200-image batch appends each image and holds none of the others in memory.
- **Additive.** This sits *alongside* the existing per-image folders; it removes nothing.

The pure builder (`melt_object_measurements`, `build_image_long_table`) is separated from the streaming
writer so the assembly — the part that must be correct — is testable without touching a disk or a
batch loop.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pycat.utils.entity_ref import ENTITY_ID_COLUMN
from pycat.utils.notify import show_warning as _warn


# The columns every row carries, in order, after the condition fields. Kept fixed so a streamed CSV
# has a stable schema regardless of which image is being appended.
#
# ``entity_id`` (increment-3 extension) carries each object's resolvable ``_pycat_entity_id`` — the
# SAME global id `stamp_entity_ids` already put on the object table — through the melt, so a click on a
# comparative-figure object point can route through the `SelectionService` (it was dropped before, which
# is exactly why brushing was blocked). Blank when the source table was never stamped.
_CORE_COLS = ('object_type', 'object_id', 'entity_id', 'measurement', 'value', 'units')
_DEFAULT_PROVENANCE_COLS = ('channel', 'frame', 'pixel_size_um', 'pycat_version', 'operation_id')


def melt_object_measurements(df, object_type, *, id_col='object_id', value_cols=None,
                             units=None) -> pd.DataFrame:
    """Wide per-object table → long ``(object_type, object_id, measurement, value, units)`` rows.

    ``df`` is one image's objects of one type (e.g. a puncta table): one row per object, measurement
    columns across. ``value_cols`` names the measurement columns; when ``None`` it defaults to every
    numeric column except the id — a caller with non-measurement numeric columns (a bbox coordinate,
    a label) should pass ``value_cols`` explicitly rather than melt those as measurements.

    ``units`` is an optional ``{measurement: unit}`` map; an unlisted measurement gets ``''`` (blank),
    not a guessed unit.
    """
    df = pd.DataFrame(df)
    if df.empty:
        return pd.DataFrame(columns=list(_CORE_COLS))

    ids = (df[id_col] if id_col in df.columns
           else pd.Series(range(len(df)), index=df.index, name=id_col))
    # The object's global entity id, carried through untouched (blank if the table was never stamped).
    ent = (df[ENTITY_ID_COLUMN] if ENTITY_ID_COLUMN in df.columns
           else pd.Series([''] * len(df), index=df.index))

    if value_cols is None:
        value_cols = [c for c in df.columns
                      if c != id_col and pd.api.types.is_numeric_dtype(df[c])]
    value_cols = [c for c in value_cols if c in df.columns and c != ENTITY_ID_COLUMN]

    units = units or {}
    frames = []
    for col in value_cols:
        frames.append(pd.DataFrame({
            'object_type': object_type,
            'object_id': ids.values,
            'entity_id': ent.values,
            'measurement': col,
            'value': pd.to_numeric(df[col], errors='coerce').values,
            'units': units.get(col, ''),
        }))
    if not frames:
        return pd.DataFrame(columns=list(_CORE_COLS))
    return pd.concat(frames, ignore_index=True)


def build_image_long_table(records, *, image_stem, sample_metadata=None, provenance=None,
                           condition_fields=None, provenance_cols=_DEFAULT_PROVENANCE_COLS,
                           units=None) -> pd.DataFrame:
    """One image's full long table: its objects melted, with condition + provenance columns attached.

    ``records`` is a list of ``(object_type, wide_df)`` or ``(object_type, wide_df, id_col)``. The
    condition columns come from ``sample_metadata.fields``; ``condition_fields`` fixes their names and
    order so a streamed batch has a stable schema (a field absent for this image is blank, never
    guessed). ``provenance`` is a per-image dict whose values fill ``provenance_cols``.
    """
    from pycat.utils.sample_metadata import SampleMetadata

    fields = {}
    if isinstance(sample_metadata, SampleMetadata):
        fields = sample_metadata.fields
    elif isinstance(sample_metadata, dict):
        fields = sample_metadata

    if condition_fields is None:
        condition_fields = list(fields.keys())
    provenance = provenance or {}

    parts = []
    for rec in records:
        object_type, wide = rec[0], rec[1]
        id_col = rec[2] if len(rec) > 2 else 'object_id'
        parts.append(melt_object_measurements(wide, object_type, id_col=id_col, units=units))
    long = (pd.concat(parts, ignore_index=True) if parts
            else pd.DataFrame(columns=list(_CORE_COLS)))

    # Assemble in canonical column order: stem, conditions, core, provenance.
    out = pd.DataFrame()
    out['image_stem'] = [image_stem] * len(long)
    for cf in condition_fields:
        out[cf] = fields.get(cf, '')            # absent condition => blank, not a lie
    for c in _CORE_COLS:
        out[c] = long[c].values if len(long) else []
    for pc in provenance_cols:
        out[pc] = provenance.get(pc, '')
    return out


def consolidated_columns(condition_fields, provenance_cols=_DEFAULT_PROVENANCE_COLS):
    """The full, ordered column schema — so the streaming writer and any reader agree."""
    return (['image_stem'] + list(condition_fields) + list(_CORE_COLS) + list(provenance_cols))


#: The per-object measurement tables PyCAT writes to a data repository, keyed ``<type>_df``. Kept an
#: explicit allowlist rather than "any ``*_df``" so a non-object table (``timing_df``,
#: ``line_profile_df``, a single-row summary) is not silently melted as if its columns were per-object
#: measurements. A caller with another object table names it.
DEFAULT_OBJECT_TABLES = ('cell_df', 'puncta_df')


def records_from_data_repository(repo, object_tables=DEFAULT_OBJECT_TABLES):
    """``(object_type, dataframe)`` records for the per-object tables present in a data repository.

    ``object_type`` is the key with ``_df`` stripped (``puncta_df`` → ``punctum``... no — ``puncta``;
    the key's stem is used verbatim, so the label matches what the rest of PyCAT calls it). Absent or
    empty tables are skipped. Pure — feed it a plain dict in a test, no batch loop required.
    """
    records = []
    for key in object_tables:
        df = (repo or {}).get(key)
        if isinstance(df, pd.DataFrame) and not df.empty:
            object_type = key[:-3] if key.endswith('_df') else key
            records.append((object_type, df))
    return records


def records_from_output_dir(output_dir, image_stem, object_tables=DEFAULT_OBJECT_TABLES):
    """``(object_type, dataframe)`` records read from an image's per-image batch output folder.

    Reads ``<stem>_<key>.csv`` (``<stem>_cell_df.csv``, ``<stem>_puncta_df.csv``) — the files the replay
    steps already wrote — so the consolidated writer STREAMS from disk rather than holding each image's
    data repository open. Absent, empty, or unreadable files are skipped (an image that produced no
    objects simply contributes no rows). Pure: point it at a temp dir in a test.
    """
    import pathlib
    out = pathlib.Path(output_dir)
    records = []
    for key in object_tables:
        csv = out / f"{image_stem}_{key}.csv"
        if not csv.exists():
            continue
        try:
            df = pd.read_csv(csv)
        except Exception as exc:
            _warn(f"Consolidated table: could not read {csv.name}: {exc}")
            continue
        if not df.empty:
            object_type = key[:-3] if key.endswith('_df') else key
            records.append((object_type, df))
    return records


class ConsolidatedLongWriter:
    """Append each image's long rows to one CSV, holding no other image in memory.

    Constructed with the condition-field vocabulary (knowable from the sample sheet + filename pattern
    before any pixels are read), so the CSV schema is fixed and streaming append is safe. ``add_image``
    writes immediately; nothing accumulates across images.
    """

    def __init__(self, path, condition_fields, *, provenance_cols=_DEFAULT_PROVENANCE_COLS,
                 units=None):
        self.path = str(path)
        self.condition_fields = list(condition_fields)
        self.provenance_cols = tuple(provenance_cols)
        self.units = units or {}
        self._columns = consolidated_columns(self.condition_fields, self.provenance_cols)
        self._header_written = False
        self.n_images = 0
        self.n_rows = 0

    def add_image(self, image_stem, records, *, sample_metadata=None, provenance=None):
        """Melt one image's objects and append them. Returns the number of rows written."""
        table = build_image_long_table(
            records, image_stem=image_stem, sample_metadata=sample_metadata,
            provenance=provenance, condition_fields=self.condition_fields,
            provenance_cols=self.provenance_cols, units=self.units)
        # Reindex to the fixed schema so append can never drift a column.
        table = table.reindex(columns=self._columns)

        table.to_csv(self.path, mode=('w' if not self._header_written else 'a'),
                     header=not self._header_written, index=False)
        self._header_written = True
        self.n_images += 1
        self.n_rows += len(table)
        return len(table)

    def summary(self) -> str:
        return (f"Consolidated table: {self.n_rows} rows from {self.n_images} image(s) "
                f"-> {self.path}")
