"""**The Explore → Refine → Export engine — one figure, refined not recomputed, exported WYSIWYG.**

A comparative figure is expensive to compute and its numbers are already correct; only its *presentation*
needs work. This holds a rendered figure plus its canonical `figure_spec.FigureSpec`, so mutating a spec
field re-applies presentation to the SAME figure (never re-running the analysis), and export writes exactly
what the refined figure shows. Qt-free, so the workflow contract — refine-not-recompute, and
what-you-see-is-what-you-export — is unit-testable without a GUI; the dialog is a thin skin over this.
"""
from __future__ import annotations

import dataclasses

from pycat.utils.figure_spec import FigureSpec, refine, export, apply_size_preset


class FigureRefineController:
    """Owns one figure + its `FigureSpec` (+ optional summary table). ``set`` / ``size_preset`` mutate the
    spec and re-apply presentation; ``export_bundle`` writes the bundle of exactly what is on screen."""

    def __init__(self, fig, spec=None, *, summary_df=None):
        self.fig = fig
        self.spec = spec if spec is not None else FigureSpec()
        self.summary_df = summary_df

    def set(self, **fields) -> "FigureRefineController":
        """Mutate spec fields and re-apply presentation to the figure — **never recomputes**. Chainable."""
        self.spec = dataclasses.replace(self.spec, **fields)
        self.apply()
        return self

    def size_preset(self, name) -> "FigureRefineController":
        """Apply a size preset (single/1.5/double column + legible font) and re-apply. Sizes, not a
        journal-compliance claim."""
        self.spec = apply_size_preset(self.spec, name)
        self.apply()
        return self

    def apply(self):
        """Re-apply the current spec to the figure: set it to its final physical size (so the preview IS the
        print size) and restyle. Presentation only — the plotted data is untouched."""
        try:
            self.fig.set_size_inches(self.spec.figure_size_in)
        except Exception:      # broad-ok: a detached/odd figure can't be resized — style it anyway
            pass
        return refine(self.fig, self.spec)

    def export_bundle(self, path):
        """Write the figure bundle — vector PDF/SVG (embedded text) + high-DPI PNG + the spec JSON (+ summary
        CSV). **WYSIWYG:** the current spec is applied first, so the exported figure is exactly the preview.
        Returns the written paths."""
        self.apply()
        return export(self.fig, path, spec=self.spec, summary_df=self.summary_df)

    @classmethod
    def from_bundle(cls, spec_json_path) -> "FigureRefineController":
        """Reopen a saved bundle: restore the refined `FigureSpec` and, from the ``*_data.json`` written
        beside it, regenerate the exact figure — so closing and reopening a figure restores its refined
        state (a reproducible figure). Falls back to a spec-only controller (no figure) if the raw data is
        absent."""
        spec, figdata = load_bundle(spec_json_path)
        if figdata is None:
            ctl = cls.__new__(cls)
            ctl.fig, ctl.spec, ctl.summary_df = None, spec, None
            return ctl
        from pycat.utils.figure_spec import render
        ctl = cls(render(figdata, spec), spec)
        return ctl


def load_bundle(spec_json_path):
    """Restore ``(spec, figdata_or_None)`` from an exported bundle's spec JSON — and the raw plotted data
    (``<name>_data.json``) beside it when present. Reopening a figure this way restores exactly what was
    saved (the spec's ``_provenance`` key is tolerated by ``spec_from_dict``)."""
    import json
    import pathlib
    from pycat.utils.figure_spec import spec_from_dict, figdata_from_dict
    p = pathlib.Path(spec_json_path)
    spec = spec_from_dict(json.loads(p.read_text(encoding='utf-8')))
    data_path = p.with_name(p.stem + '_data.json')
    figdata = None
    if data_path.exists():
        figdata = figdata_from_dict(json.loads(data_path.read_text(encoding='utf-8')))
    return spec, figdata
