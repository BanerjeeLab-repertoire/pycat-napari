"""**Histogram bins and aggregate rows as honest COHORT selections.**

The cohort selection target shipped in 1.6.151 (``Cohort`` + ``select_cohort``), and the comparative
box/violin group emitter with it. Two emitters were deferred as clean follow-ons; this module is them:

* **Histogram bin → cohort.** A bar is a *set* of objects whose metric falls in a range, not an entity.
  Clicking it selects that set, carrying the RANGE as the cohort's definition (``"area ∈ [12.0, 18.0)
  µm²"``) so a dock can say *why* the objects are grouped, never an anonymous highlight.
* **Aggregate row → cohort.** A per-cell mean or population-fit row summarizes many objects; selecting
  it must highlight ALL contributors and say *"summarizes N objects"*, never resolve to one arbitrary
  member.

Both ride the existing ``SelectionService``: ``select_cohort`` fills ``selected`` with the members too,
so a cohort-UNAWARE view (the image/labels overlay) highlights every member for free, while a
cohort-AWARE view reads the definition and count. **A cohort is a SELECTION, not a FILTER** — it never
mutates the DataFrame or the analysed population (that is the deferred FilterStore's separate job).

The membership logic (``bin_cohort``, ``aggregate_cohort``) is pure and GUI-free so the part that must be
correct — *which* objects a bin or row groups — is tested without a matplotlib event loop, exactly as the
comparative emitter is.
"""

from __future__ import annotations

import numpy as np

from pycat.utils.selection_service import Cohort


def _fmt(x) -> str:
    """A compact, stable number for a bin-edge label (no trailing-zero noise, no locale surprises)."""
    v = float(x)
    return f"{v:.3g}"


def bin_cohort(values, entity_ids, bin_index, bin_edges, *, measurement=None, units=None,
               source_view='histogram') -> Cohort:
    """The cohort of entities whose value falls in histogram bin ``bin_index`` — computed independently
    of any drawn bars, so it is the ground truth the drawing must match.

    Bins follow numpy/matplotlib's convention: half-open ``[edge[i], edge[i+1])`` for every bin EXCEPT
    the last, which is closed ``[edge[-2], edge[-1]]`` so the maximum value lands in it (matplotlib's
    ``hist`` does the same). The definition records the range and, when known, the measurement and units,
    so the selection reads ``"area ∈ [12, 18) µm²"`` rather than an anonymous group.
    """
    v = np.asarray(values, dtype=float)
    eids = np.asarray([str(e) for e in entity_ids], dtype=object)
    edges = np.asarray(bin_edges, dtype=float)
    n_bins = len(edges) - 1
    i = int(bin_index)
    if not (0 <= i < n_bins):
        raise IndexError(f"bin_index {i} out of range for {n_bins} bins")

    lo, hi = edges[i], edges[i + 1]
    last = (i == n_bins - 1)
    in_bin = (v >= lo) & (v <= hi) if last else (v >= lo) & (v < hi)
    members = frozenset(str(e) for e, keep in zip(eids, in_bin) if keep and e and e != 'nan')

    bracket = ']' if last else ')'
    name = measurement or 'value'
    unit = f" {units}" if units else ''
    definition = f"{name} ∈ [{_fmt(lo)}, {_fmt(hi)}{bracket}{unit}"
    return Cohort(members=members, definition=definition, kind='bin', source_view=source_view)


def aggregate_cohort(members, *, definition=None, source_view='aggregate') -> Cohort:
    """The cohort a table's AGGREGATE row stands for — its contributing objects.

    A summary row (per-cell mean, population fit) must select ALL the objects it summarizes, not one
    arbitrary member. The default definition is the honest *"summarizes N objects"*; a caller with a
    richer label (``"WT · mean area"``) can pass its own, and the count is still appended.
    """
    mem = frozenset(str(e) for e in (members or ()) if e and str(e) != 'nan')
    base = definition.strip() if definition else 'summarizes'
    if definition:
        text = f"{base} · {len(mem)} objects"
    else:
        text = f"summarizes {len(mem)} objects"
    return Cohort(members=mem, definition=text, kind='aggregate', source_view=source_view)


def cohort_dock_label(cohort: Cohort) -> str:
    """The one-line dock caption for a cohort: count first, then WHY. Blank for an empty/None cohort."""
    if cohort is None or cohort.n == 0:
        return ''
    return f"{cohort.n} objects · {cohort.definition}" if cohort.definition else f"{cohort.n} objects"


def attach_histogram_brushing(fig, ax, values, entity_ids, *, bin_edges, selection_service,
                              view_id='histogram', measurement=None, units=None, bars=None):
    """Wire a drawn histogram so clicking a bar selects that bin's objects as a cohort.

    ``bin_edges`` are the exact edges the histogram was drawn with (pass ``ax.hist(...)[1]``), so the
    emitted cohort's membership matches the bars the user sees. ``bars`` (the ``BarContainer`` from
    ``ax.hist``) is used only for a light self-highlight of the clicked bar — the OVERLAY highlights the
    member objects via ``selected`` for free. Returns ``{'emit_bin', 'apply_selection'}`` so the
    behaviour is testable without a GUI event loop (matplotlib clicks do not fire under Agg).
    """
    edges = np.asarray(bin_edges, dtype=float)
    state = {'hi_bar': None}

    def _highlight_bar(i):
        # Reset any previous emphasis, then thicken the clicked bar's edge. Cosmetic; never load-bearing.
        try:
            patches = list(bars) if bars is not None else []
            for p in patches:
                p.set_linewidth(0.5)
                p.set_edgecolor('white')
            if patches and 0 <= i < len(patches):
                patches[i].set_edgecolor('#ff8c00')
                patches[i].set_linewidth(2.0)
                state['hi_bar'] = i
            fig.canvas.draw_idle()
        except Exception:                    # broad-ok: no live canvas / no bars → nothing to redraw
            pass

    def emit_bin(x_data):
        """Emit the cohort for the bin containing data-x ``x_data`` (a click's ``event.xdata``)."""
        if x_data is None or not np.isfinite(x_data):
            return None
        i = int(np.digitize([float(x_data)], edges)[0]) - 1
        i = max(0, min(i, len(edges) - 2))           # clamp edge/overflow clicks into the outer bins
        coh = bin_cohort(values, entity_ids, i, edges, measurement=measurement, units=units,
                         source_view=view_id)
        _highlight_bar(i)
        selection_service.select_cohort(coh, source=view_id)
        return coh

    def apply_selection(state_obj):
        # A selection arriving from ANOTHER view: nothing bar-specific to ring here (the overlay carries
        # the members). Kept for contract symmetry with the other adapters' apply_selection.
        return None

    try:
        selection_service.subscribe(view_id, apply_selection)
    except Exception:                                # broad-ok: a service without subscribe → no receive wiring
        pass
    _cid = None
    try:
        _cid = fig.canvas.mpl_connect(
            'button_press_event',
            lambda ev: (getattr(ev, 'inaxes', None) is ax and getattr(ev, 'xdata', None) is not None
                        and emit_bin(ev.xdata)))
    except Exception:                                # broad-ok: no canvas to connect (headless)
        pass

    def dispose():
        """Detach on close — idempotent (plot_lifecycle). ``apply_selection`` is a CLOSURE, so the service
        holds it STRONGLY (the weak-method net does not catch it); this is the explicit unsubscribe that
        keeps the subscriber list from growing across a session, plus the canvas cid disconnect."""
        try:
            selection_service.unsubscribe(view_id)
        except Exception:                            # broad-ok: teardown is best-effort; never raise on close
            pass
        if _cid is not None:
            try:
                fig.canvas.mpl_disconnect(_cid)
            except Exception:                        # broad-ok: a stale/twice-disconnected cid is harmless
                pass

    return {'emit_bin': emit_bin, 'apply_selection': apply_selection, 'dispose': dispose}


def select_aggregate_row(selection_service, members, *, definition=None, view_id='aggregate'):
    """Emit the cohort for an aggregate table row — its contributing objects, with the count stated.

    Returns the emitted ``Cohort`` (also useful for the dock caption via ``cohort_dock_label``)."""
    coh = aggregate_cohort(members, definition=definition, source_view=view_id)
    selection_service.select_cohort(coh, source=view_id)
    return coh
