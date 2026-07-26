"""**Generate the manuscript figures that the data supports — headless (manuscript_toolbox).**

The headless counterpart of the (deferred) Qt gallery: iterate the :mod:`pycat.toolbox.manuscript.panels`
registry, generate every panel whose data is present in ``context``, and save each to ``output_dir`` — figures
as PNG, tables as CSV (or ``.txt`` for a text report section). Returns a manifest so a caller (a script, a
CI figure build, or the future gallery) knows exactly what was produced and what was greyed and *why*.

Non-gating by construction: a greyed panel records its ``data_requirement`` (never a dead entry), and a panel
that raises during generation is recorded ``status='error'`` and skipped — one panel never aborts the rest of
the report.
"""
from __future__ import annotations

from pathlib import Path

from pycat.toolbox.manuscript.panels import manuscript_panels, panel_is_available


def _save_panel(panel, result, out: Path) -> Path:
    """Persist one generated panel by what it ``produces``: a matplotlib figure → PNG, a DataFrame → CSV, any
    other (e.g. a text report section) → ``.txt``. Returns the written path."""
    if panel.produces == "figure" and hasattr(result, "savefig"):
        path = out / f"{panel.key}.png"
        result.savefig(str(path), dpi=200, bbox_inches="tight")
        try:
            import matplotlib.pyplot as plt
            plt.close(result)
        except Exception:      # broad-ok: ui_cleanup — closing the figure is housekeeping, never fatal
            pass
        return path
    if hasattr(result, "to_csv"):
        path = out / f"{panel.key}.csv"
        result.to_csv(str(path), index=False)
        return path
    path = out / f"{panel.key}.txt"
    path.write_text(str(result), encoding="utf-8")
    return path


def generate_manuscript_report(context, output_dir) -> list:
    """Generate every manuscript panel whose data is present in ``context``, saving each under ``output_dir``.

    Returns a manifest: one dict per registered panel with ``key`` / ``title`` / ``produces`` and a
    ``status`` — ``'generated'`` (with its ``path``), ``'greyed'`` (with the ``data_requirement`` to satisfy),
    or ``'error'`` (with the ``reason``). The order matches the manuscript's figure order.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for panel in manuscript_panels():
        entry = {"key": panel.key, "title": panel.title, "produces": panel.produces,
                 "figure_role": panel.figure_role, "path": None}
        if not panel_is_available(panel, context):
            entry.update(status="greyed", reason=panel.data_requirement)
            manifest.append(entry)
            continue
        try:
            result = panel.generate(context)
            entry.update(status="generated", path=str(_save_panel(panel, result, out)))
        except Exception as exc:      # broad-ok: batch_step — one panel's failure is recorded in the manifest, never aborts the rest of the report
            entry.update(status="error", reason=f"{type(exc).__name__}: {exc}")
        manifest.append(entry)
    return manifest


def report_summary(manifest) -> str:
    """A one-line-per-panel human summary of a :func:`generate_manuscript_report` manifest."""
    lines = []
    for e in manifest:
        if e["status"] == "generated":
            lines.append(f"✓ {e['key']}: {e['produces']} → {e['path']}")
        elif e["status"] == "greyed":
            lines.append(f"– {e['key']}: needs — {e['reason']}")
        else:
            lines.append(f"✗ {e['key']}: {e.get('reason', 'error')}")
    return "\n".join(lines)
