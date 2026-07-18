"""**Attach a condition label to an image, from any of three sources, behind one resolver.**

Nothing in PyCAT can be *comparative* without a way to say "this image is WT replicate 2 at 10 µM".
Batch writes one folder per image and there is no condition/perturbation concept anywhere — so a study
across N mutants is N folders of disconnected CSVs the scientist stitches by hand. This is the metadata
layer the consolidated table (increment 2) joins on.

A label reaches an image three ways, and all three are available:

1. **Sample sheet (primary)** — a CSV with one row per image (`filename`/`stem` + arbitrary condition
   columns). Whatever columns the sheet has *are* the condition vocabulary; nothing is hardcoded.
2. **Filename parse (fallback)** — a `{field}` template (e.g. `{genotype}_rep{replicate}_{dose}uM`)
   compiled to a named-group regex. A safe template→regex, never ``eval``.
3. **In-app tag (interactive)** — a per-image condition dict the user sets in the app, persisted in the
   session manifest.

**Precedence: explicit beats inferred.** sheet row > in-app tag > filename parse > `{}`. The merge is
**field by field** — a sheet row can supply `genotype` while the filename fills `dose` — and each
field records which source won, so provenance is never guessed.

The one rule under everything: **an absent field stays absent.** Never a default, never a guess — the
same honesty contract as the pixel-size gate and the z-step NaN. A fabricated condition label is worse
than a missing one, because a comparison across it would be silently wrong.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from pycat.utils.notify import show_warning as _warn


# Precedence order, highest first. A field is filled by the first source in this list that has it.
_PRECEDENCE = ('sample_sheet', 'in_app', 'filename')


@dataclass(frozen=True)
class SampleMetadata:
    """The resolved condition for one image.

    ``fields`` is the merged condition dict. ``source`` is the highest-precedence source that
    contributed at least one field (or ``'none'``). ``field_sources`` records, per field, which source
    actually supplied it — so a mixed result (sheet + filename) is honest about which value came from
    where, not flattened to a single label.
    """
    fields: dict
    source: str = 'none'
    field_sources: dict = field(default_factory=dict)


def parse_filename(stem: str, pattern: Optional[str]) -> dict:
    """Extract condition fields from ``stem`` using a ``{field}`` template. ``{}`` on no match.

    ``{genotype}_rep{replicate}_{dose}uM`` becomes a named-group regex; the literal text between
    placeholders is escaped so ``.`` / ``(`` in a real filename cannot smuggle in regex. Fields are
    matched non-greedily so adjacent ``{a}{b}`` still split on the literal that separates them.

    Safe by construction — a template compiled to a regex, never evaluated as code.
    """
    if not pattern:
        return {}
    try:
        regex = _template_to_regex(pattern)
    except Exception as exc:
        _warn(f"Sample metadata: filename pattern {pattern!r} is not usable ({exc}); skipping it.")
        return {}
    m = regex.fullmatch(str(stem))
    if not m:
        return {}
    # Absent captures (optional groups that did not fire) stay absent, never a guessed value.
    return {k: v for k, v in m.groupdict().items() if v is not None}


def _template_to_regex(pattern: str) -> "re.Pattern":
    """Compile a ``{field}`` template to a named-group regex. Duplicate field names are rejected."""
    out = []
    seen = set()
    i = 0
    for token in re.finditer(r'\{(\w+)\}|([^{]+)|(\{)', pattern):
        name, literal, stray = token.group(1), token.group(2), token.group(3)
        if name is not None:
            if name in seen:
                raise ValueError(f"field {name!r} appears twice")
            seen.add(name)
            out.append(f'(?P<{name}>.+?)')
        elif literal is not None:
            out.append(re.escape(literal))
        else:                                  # a lone '{' — treat literally
            out.append(re.escape(stray))
    if not seen:
        raise ValueError("pattern has no {field} placeholders")
    return re.compile(''.join(out))


def load_sample_sheet(path) -> dict:
    """Read a CSV into ``{stem: condition_dict}``. Warns, does not crash, on a malformed sheet.

    The stem column may be named ``stem`` or ``filename`` (a filename is reduced to its stem, so a
    sheet written with extensions still joins). Every other column is a condition field — arbitrary by
    design. A blank cell is an absent field, not the empty string.
    """
    import pandas as pd
    import pathlib

    try:
        df = pd.read_csv(path, dtype=str)
    except Exception as exc:
        _warn(f"Sample metadata: could not read sample sheet {path} ({exc}); ignoring it.")
        return {}

    key_col = next((c for c in ('stem', 'filename', 'file', 'image') if c in df.columns), None)
    if key_col is None:
        _warn(f"Sample metadata: sample sheet {path} has no 'stem'/'filename' column; ignoring it.")
        return {}

    field_cols = [c for c in df.columns if c != key_col]
    out = {}
    for _, row in df.iterrows():
        raw_key = row[key_col]
        if not isinstance(raw_key, str) or not raw_key.strip():
            continue
        stem = pathlib.Path(raw_key.strip()).stem     # tolerate 'img.tif' -> 'img'
        cond = {c: row[c] for c in field_cols
                if isinstance(row[c], str) and row[c].strip()}    # blank = absent
        out[stem] = cond
    return out


class SampleMetadataResolver:
    """Resolve the condition dict for an image from sheet / in-app tag / filename, with precedence.

    Construct once (a batch builds one and calls ``for_image`` per file); it holds the sheet and the
    in-app tags in memory and applies the filename pattern per image.
    """

    def __init__(self, sheet_path=None, filename_pattern=None, in_app_tags=None):
        self._sheet = load_sample_sheet(sheet_path) if sheet_path else {}
        self._pattern = filename_pattern
        # {stem: condition_dict} set interactively, e.g. loaded from a session manifest.
        self._in_app = dict(in_app_tags or {})
        self._matched_stems = set()      # for the unmatched-row warning

    def for_image(self, image_path) -> SampleMetadata:
        """The merged condition for one image, field by field, explicit beating inferred."""
        import pathlib
        stem = pathlib.Path(str(image_path)).stem

        by_source = {
            'sample_sheet': self._sheet.get(stem, {}),
            'in_app': self._in_app.get(stem, {}),
            'filename': parse_filename(stem, self._pattern),
        }
        if by_source['sample_sheet']:
            self._matched_stems.add(stem)

        merged, field_sources = {}, {}
        for src in _PRECEDENCE:                 # highest precedence first
            for k, v in by_source[src].items():
                if k not in merged:             # first (highest-precedence) source wins the field
                    merged[k] = v
                    field_sources[k] = src

        winner = next((s for s in _PRECEDENCE if any(fs == s for fs in field_sources.values())),
                      'none')
        return SampleMetadata(fields=merged, source=winner, field_sources=field_sources)

    def warn_unmatched_sheet_rows(self):
        """After a batch, warn about sheet rows no image used — a likely filename typo, not a crash."""
        unused = set(self._sheet) - self._matched_stems
        if unused:
            _warn(f"Sample metadata: {len(unused)} sample-sheet row(s) matched no image "
                  f"({', '.join(sorted(unused)[:5])}{'…' if len(unused) > 5 else ''}). "
                  f"Check the stem/filename column against the actual files.")


# ── In-app tag persistence — a tag travels with the session ──────────────────

_MANIFEST_KEY = 'sample_metadata'


def tags_to_manifest_extra(in_app_tags) -> dict:
    """The ``extra=`` blob to hand ``write_manifest`` so in-app tags round-trip with the session.

    ``{stem: {field: value}}`` under one key. A caller does
    ``write_manifest(..., extra=tags_to_manifest_extra(tags))``; the write side already merges
    ``extra`` in, so nothing in the manifest writer changes.
    """
    return {_MANIFEST_KEY: {str(k): dict(v) for k, v in (in_app_tags or {}).items()}}


def tags_from_manifest(manifest) -> dict:
    """Read in-app tags back out of a parsed manifest. ``{}`` when the field is absent.

    Back-compat is the default: a manifest written before this field loads as "no tag", not an error.
    Feed the result to ``SampleMetadataResolver(in_app_tags=...)``.
    """
    if not isinstance(manifest, dict):
        return {}
    tags = manifest.get(_MANIFEST_KEY)
    if not isinstance(tags, dict):
        return {}
    return {str(k): dict(v) for k, v in tags.items() if isinstance(v, dict)}
