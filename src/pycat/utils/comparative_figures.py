"""**Comparative figures that SHOW the replicate structure, not hide it.**

Increment 3's visible layer, drawn from the consolidated table. The one design decision that matters:
a comparative figure must make pseudoreplication *visible*, not paper over it. So every condition is
drawn twice — the object cloud (light, many points) **and** the replicate means on top (dark, few
points) — because the honest test (`comparative_stats.compare_conditions`) runs on those few dark
points, and the figure should let the eye see that. A p-value annotation that came from the objects
would contradict what the picture shows; here they agree by construction.

Static matplotlib (Agg-safe, renders headlessly). A PyQtGraph render is a later increment.

── Selection / brushing (increment-3 Part D) ──
**Single-entity brushing is now wired** (`_attach_object_brushing`), through the EXISTING
`SelectionService`: the consolidated table now carries a resolvable `entity_id` per object row (the id
`stamp_entity_ids` already stamped, carried through the melt instead of dropped), so a click on an
object point selects that entity everywhere, and a selection from another view rings the matching point.
No second selection path was built — it routes through the same contract as every other PyCAT view.

**Cohort selection stays the noted-blocked seam:** clicking a replicate/condition marker to select the
cohort it summarizes needs the typed/cohort-target `SelectionState` still deferred on the
interaction-layer roadmap (§3/§4). The figures make the single-vs-cohort distinction *visually* (objects
light, unit means dark); the cohort *click* is the follow-on, deliberately not faked.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pycat.utils.comparative_stats import compare_conditions, aggregate_to_replicate


def _measurement_frame(long_df, measurement, condition_col, value_col):
    df = pd.DataFrame(long_df)
    if 'measurement' in df.columns:
        df = df[df['measurement'] == measurement]
    df = df[[condition_col] + [c for c in (value_col,) if c in df.columns]].copy()
    df[value_col] = pd.to_numeric(df[value_col], errors='coerce')
    return df.dropna(subset=[value_col])


def condition_comparison_figure(long_df, measurement, *, condition_col, replicate_col,
                                value_col='value', order=None, parametric=False, ax=None):
    """A per-condition comparison: object cloud + replicate means + the honest stats annotation.

    Returns the matplotlib ``Figure``. The x-axis is the conditions; each shows a box of its objects,
    the individual objects jittered behind it (light), and the **replicate means as large dark
    points** — the units the test actually compares. The title carries the test, the p-value, and n at
    both levels, straight from ``compare_conditions``; a non-inferential result is labelled as such,
    never given a significance marker.
    """
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    objs = _measurement_frame(long_df, measurement, condition_col, value_col)
    reps = aggregate_to_replicate(long_df, measurement, condition_col=condition_col,
                                  replicate_col=replicate_col, value_col=value_col)
    result = compare_conditions(long_df, measurement, condition_col=condition_col,
                                replicate_col=replicate_col, value_col=value_col,
                                parametric=parametric)

    conditions = order or sorted(objs[condition_col].astype(str).unique())
    fig = ax.figure if ax is not None else plt.figure(figsize=(1.6 * len(conditions) + 2, 4.5))
    ax = ax or fig.add_subplot(111)
    rng = np.random.default_rng(0)

    for i, cond in enumerate(conditions):
        ov = objs.loc[objs[condition_col].astype(str) == str(cond), value_col].to_numpy()
        rv = reps.loc[reps[condition_col].astype(str) == str(cond), 'value'].to_numpy()
        if ov.size:
            ax.scatter(np.full(ov.size, i) + rng.uniform(-0.12, 0.12, ov.size), ov,
                       s=6, color='#c7d3e8', alpha=0.5, zorder=1)      # the object cloud, light
            ax.boxplot(ov, positions=[i], widths=0.5, showfliers=False,
                       medianprops=dict(color='#34495e'), zorder=2)
        if rv.size:
            ax.scatter(np.full(rv.size, i), rv, s=70, color='#c0392b', edgecolor='white',
                       linewidth=0.8, zorder=3, label='replicate mean' if i == 0 else None)  # the units tested

    ax.set_xticks(range(len(conditions)))
    ax.set_xticklabels(conditions)
    ax.set_xlabel(condition_col)
    ax.set_ylabel(f"{measurement} ({value_col})")
    ax.legend(loc='best', fontsize=8, frameon=False)

    ax.set_title(_annotation(result), fontsize=9, loc='left')
    fig.tight_layout()
    return fig


def _annotation(result) -> str:
    """One honest caption. Never a star without an inferential p-value behind it."""
    ns = " · ".join(f"{c}: n={g['n_replicates']} rep / {g['n_objects']} obj"
                    for c, g in result.groups.items())
    if result.inferential:
        star = '*' if (result.p_value is not None and result.p_value < 0.05) else 'n.s.'
        head = f"{result.measurement}: {result.test}, p = {result.p_value:.3g} [{star}]"
    else:
        head = f"{result.measurement}: NO TEST — {result.note.split('.')[0]}"
    return head + "\n" + ns


def dose_response_figure(long_df, measurement, *, dose_col, replicate_col,
                         value_col='value', ax=None):
    """Replicate-mean dose-response: mean ± SEM across replicates at each dose, points = replicates.

    The error bar is over REPLICATES, not objects — the same anti-pseudoreplication rule as the
    comparison. Doses are read as numbers so the x-axis is quantitative and ordered.
    """
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    reps = aggregate_to_replicate(long_df, measurement, condition_col=dose_col,
                                  replicate_col=replicate_col, value_col=value_col)
    reps = reps.assign(_dose=pd.to_numeric(reps[dose_col], errors='coerce')).dropna(subset=['_dose'])

    fig = ax.figure if ax is not None else plt.figure(figsize=(5, 4))
    ax = ax or fig.add_subplot(111)

    summary = reps.groupby('_dose')['value'].agg(['mean', 'std', 'size']).reset_index()
    summary['sem'] = summary['std'] / np.sqrt(summary['size'].clip(lower=1))
    ax.errorbar(summary['_dose'], summary['mean'], yerr=summary['sem'].fillna(0),
                marker='o', capsize=3, color='#34495e', zorder=2, label='replicate mean ± SEM')
    ax.scatter(reps['_dose'], reps['value'], s=30, color='#c0392b', alpha=0.6, zorder=1)

    ax.set_xlabel(dose_col)
    ax.set_ylabel(f"{measurement} ({value_col})")
    ax.set_title(f"{measurement} dose-response (n over replicates, not objects)", fontsize=9, loc='left')
    ax.legend(loc='best', fontsize=8, frameon=False)
    fig.tight_layout()
    return fig


# ── The increment-3 spec API: (Figure, summary_df), a declared unit, n at every level ──────────
#
# The figure functions above return only a Figure. The spec's contract is (Figure, summary_df): the
# numbers behind every figure must be inspectable, never figure-only. These wrap the proven internals,
# generalise the condition/unit to MULTIPLE fields (genotype × dose), default the biological unit to
# the image when no replicate is declared, and are **descriptive by default** — a p-value appears only
# when `test=True` is asked for, and then it comes from `compare_conditions` (replicate-level, named,
# refusing loudly below the minimum). The summary frame reports n at all three levels.

_DEFAULT_UNIT_COLS = ('image_stem',)          # the biological unit is the image unless one is declared


def _measurement_slice(long_df, measurement, value_col='value'):
    df = pd.DataFrame(long_df)
    if 'measurement' in df.columns:
        df = df[df['measurement'] == measurement]
    df = df.copy()
    df[value_col] = pd.to_numeric(df.get(value_col), errors='coerce')
    return df.dropna(subset=[value_col])


def _composite(df, cols):
    """Several condition (or unit) fields joined into ONE label, so grouping is field-count-agnostic."""
    cols = [c for c in (cols or []) if c in df.columns]
    if not cols:
        return pd.Series(['all'] * len(df), index=df.index)
    return df[cols].astype(str).agg(' | '.join, axis=1)


def aggregate_to_unit(long_df, *, measurement, unit_cols, condition_cols, value_col='value'):
    """One value per biological unit (condition × unit) — the anti-pseudoreplication step, generalised
    to multi-field conditions/units. Returns ``[condition, unit, unit_value]``."""
    df = _measurement_slice(long_df, measurement, value_col)
    df = df.assign(condition=_composite(df, condition_cols),
                   unit=_composite(df, unit_cols))
    return (df.groupby(['condition', 'unit'])[value_col].mean()
              .reset_index().rename(columns={value_col: 'unit_value'}))


def _attach_object_brushing(fig, ax, object_points, selection_service, *, view_id='comparative',
                            cohort_markers=None):
    """Route a click on an object point through the existing ``SelectionService`` — single-entity
    selection, the increment-3 Part-D wiring, now that the consolidated table carries a resolvable
    ``entity_id`` per object.

    ``object_points`` is ``[(entity_id, x, y), …]`` in DATA coordinates. Clicking near an object point
    selects that entity everywhere; a selection arriving from ANOTHER view rings the matching point(s)
    here. Self-highlight on emit is deliberate — the service suppresses a view's own receive, so the
    view must paint its own click (the same rule the VPT panels follow).

    **COHORT selection is now wired** (the cohort target shipped): ``cohort_markers`` is
    ``[(x, y, Cohort), …]`` — a condition's unit-mean marker carrying the cohort of that condition's
    objects. Clicking nearest a cohort marker selects the whole GROUP (``select_cohort``, so a
    cohort-aware dock can say *"N objects, condition=WT"*); clicking nearest an object point selects the
    single entity. Nearest-wins between the two. A selection from another view rings its members either
    way (the cohort's members ride in ``selected``).

    Returns ``{'emit_nearest', 'apply_selection'}`` so the behaviour is testable without a GUI event
    loop (matplotlib clicks do not fire under Agg).
    """
    from pycat.utils.selection_service import Selection

    pts = [(str(e), float(x), float(y)) for (e, x, y) in object_points if e]
    cohorts = [(float(x), float(y), coh) for (x, y, coh) in (cohort_markers or [])]
    state = {'ring': None}

    def _highlight(eids):
        eids = {str(x) for x in (eids or ())}
        if state['ring'] is not None:
            try:
                state['ring'].remove()
            except Exception:                        # broad-ok: artist already gone
                pass
            state['ring'] = None
        sel = [(x, y) for e, x, y in pts if e in eids]
        if sel:
            xs, ys = zip(*sel)
            (state['ring'],) = ax.plot(xs, ys, 'o', mfc='none', mec='#ff8c00', mew=2.0,
                                       ms=12, zorder=5)
        try:
            fig.canvas.draw_idle()
        except Exception:                            # broad-ok: no live canvas (headless) — nothing to redraw
            pass

    def apply_selection(state_obj):
        _highlight(getattr(state_obj, 'entity_ids', ()) or ())

    def emit_nearest(x_disp, y_disp, radius_px=14.0):
        if not pts and not cohorts:
            return None
        trans = ax.transData
        best = None                                  # (dist, kind, payload)
        for e, x, y in pts:
            px, py = trans.transform((x, y))
            d = ((px - x_disp) ** 2 + (py - y_disp) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, 'entity', e)
        for x, y, coh in cohorts:
            px, py = trans.transform((x, y))
            d = ((px - x_disp) ** 2 + (py - y_disp) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, 'cohort', coh)
        if best is None or best[0] > radius_px:
            return None
        _, kind, payload = best
        if kind == 'cohort':
            _highlight(payload.members)              # self-highlight every member of the group
            selection_service.select_cohort(payload, source=view_id)
            return payload
        _highlight({payload})                        # self-highlight — our own receive is suppressed
        selection_service.select(Selection(
            entity_ids=(payload,), primary_id=payload, mode='selected',
            source_view=view_id, generation=selection_service.next_generation()))
        return payload

    try:
        selection_service.subscribe(view_id, apply_selection)
    except Exception:                                # broad-ok: service without subscribe → no receive wiring
        pass
    try:
        fig.canvas.mpl_connect(
            'button_press_event',
            lambda ev: (getattr(ev, 'inaxes', None) is ax and getattr(ev, 'x', None) is not None
                        and emit_nearest(ev.x, ev.y)))
    except Exception:                                # broad-ok: no canvas to connect (headless)
        pass
    return {'emit_nearest': emit_nearest, 'apply_selection': apply_selection}


def _condition_summary(objs, units, order=None):
    """Per-condition descriptive numbers AT THE UNIT LEVEL: n_objects, n_units, mean, std, and the
    unit-level SEM (std of unit means / sqrt(n_units)) — deliberately NOT the object-level SEM, which
    is the pseudoreplicated lie this whole module exists to refuse."""
    rows = []
    for cond in (order or sorted(units['condition'].unique())):
        ov = objs.loc[objs['condition'] == cond, 'value'].to_numpy()
        uv = units.loc[units['condition'] == cond, 'unit_value'].to_numpy()
        std = float(np.std(uv, ddof=1)) if uv.size > 1 else float('nan')
        rows.append(dict(
            condition=cond, n_objects=int(ov.size), n_units=int(uv.size),
            mean=float(np.mean(uv)) if uv.size else float('nan'), std_units=std,
            sem_units=(std / np.sqrt(uv.size)) if uv.size > 1 else float('nan')))
    return pd.DataFrame(rows)


def _run_unit_test(long_df, measurement, condition_cols, unit_cols, parametric):
    """Run the honest replicate-level test on the composite condition/unit, returning the
    ``ComparisonResult``. Reuses `compare_conditions`, so the refusal-below-minimum is inherited."""
    df = _measurement_slice(long_df, measurement)
    df = df.assign(_cond=_composite(df, condition_cols), _unit=_composite(df, unit_cols))
    df['measurement'] = measurement
    return compare_conditions(df, measurement, condition_col='_cond', replicate_col='_unit',
                              parametric=parametric)


def condition_comparison(long_df, *, measurement, condition_cols, unit_cols=None, kind='box',
                         show_objects=True, show_units=True, test=False, parametric=False,
                         order=None, ax=None, selection_service=None):
    """Superplot per condition + the inspectable summary. **Descriptive by default** (no p-value unless
    ``test=True``). Returns ``(Figure, summary_df)``; the summary carries n at every level and, when a
    test is run, its name/p-value or its stated refusal.

    ``selection_service`` (optional): route object-point clicks through the existing ``SelectionService``
    (single-entity brushing — needs the consolidated table's ``entity_id`` column). The brushing handle
    is stashed on ``fig._pycat_brushing``."""
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    unit_cols = list(unit_cols) if unit_cols else list(_DEFAULT_UNIT_COLS)
    objs = _measurement_slice(long_df, measurement)
    objs = objs.assign(condition=_composite(objs, condition_cols))
    units = aggregate_to_unit(long_df, measurement=measurement, unit_cols=unit_cols,
                              condition_cols=condition_cols)
    conditions = order or sorted(objs['condition'].unique())
    summary = _condition_summary(objs, units, order=conditions)

    result = _run_unit_test(long_df, measurement, condition_cols, unit_cols, parametric) if test else None
    summary.attrs['test'] = None if result is None else result.test
    summary.attrs['p_value'] = None if result is None else result.p_value
    summary.attrs['inferential'] = bool(result and result.inferential)
    summary.attrs['note'] = '' if result is None else result.note
    summary.attrs['unit_cols'] = unit_cols

    # Attach the measurement's definition/units/caveats when the ontology knows this measurement, so a
    # figure legend or Methods section can read them off the summary frame instead of a scattered docstring.
    from pycat.utils.measurement_ontology import describe
    _mdef = describe(measurement)
    summary.attrs['measurement_display_name'] = _mdef.display_name if _mdef else measurement
    summary.attrs['measurement_units'] = _mdef.units if _mdef else None
    summary.attrs['measurement_definition'] = _mdef.definition if _mdef else None
    summary.attrs['measurement_caveats'] = list(_mdef.caveats) if _mdef else []

    fig = ax.figure if ax is not None else plt.figure(figsize=(1.6 * len(conditions) + 2, 4.5))
    ax = ax or fig.add_subplot(111)
    rng = np.random.default_rng(0)
    object_points = []                              # (entity_id, x, y) for single-entity brushing
    cohort_markers = []                             # (x, y, Cohort) for group (condition) brushing
    _has_eid = 'entity_id' in objs.columns
    for i, cond in enumerate(conditions):
        sub = objs.loc[objs['condition'] == cond]
        ov = sub['value'].to_numpy()
        uv = units.loc[units['condition'] == cond, 'unit_value'].to_numpy()
        if show_objects and ov.size:
            xj = np.full(ov.size, i) + rng.uniform(-0.12, 0.12, ov.size)
            ax.scatter(xj, ov, s=6, color='#c7d3e8', alpha=0.5, zorder=1)
            if selection_service is not None and _has_eid:
                object_points.extend(zip(sub['entity_id'].to_numpy(), xj, ov))
        if ov.size and kind in ('box', 'violin'):
            (ax.violinplot(ov, positions=[i], widths=0.6, showextrema=False) if kind == 'violin'
             else ax.boxplot(ov, positions=[i], widths=0.5, showfliers=False,
                             medianprops=dict(color='#34495e'), zorder=2))
        if show_units and uv.size:
            ax.scatter(np.full(uv.size, i), uv, s=70, color='#c0392b', edgecolor='white',
                       linewidth=0.8, zorder=3, label='unit mean' if i == 0 else None)
            # Clicking a unit-mean marker selects the whole CONDITION as a cohort (its member objects,
            # with the condition as the stated definition) — the box/violin group case of the cohort spec.
            if selection_service is not None and _has_eid:
                from pycat.utils.selection_service import Cohort
                members = frozenset(str(e) for e in sub['entity_id'].to_numpy() if e)
                if members:
                    coh = Cohort(members=members, kind='group', source_view='comparative',
                                 definition=f"{cond} · {len(members)} objects")
                    cohort_markers.extend((i, float(v), coh) for v in uv)
    ax.set_xticks(range(len(conditions)))
    ax.set_xticklabels(conditions, rotation=20, ha='right')
    ax.set_xlabel(' | '.join(condition_cols) if isinstance(condition_cols, (list, tuple)) else condition_cols)
    ax.set_ylabel(f"{_mdef.display_name} ({_mdef.units})" if _mdef else measurement)
    ax.legend(loc='best', fontsize=8, frameon=False)
    _title = f"{measurement} — unit: {'|'.join(unit_cols)}"
    if result is not None:
        _title += (f" — {result.test}: p={result.p_value:.3g}" if result.inferential
                   else " — NO TEST (see summary.note)")
    ax.set_title(_title, fontsize=9, loc='left')
    fig.tight_layout()
    if selection_service is not None and (object_points or cohort_markers):
        fig._pycat_brushing = _attach_object_brushing(
            fig, ax, object_points, selection_service, cohort_markers=cohort_markers)
    return fig, summary


def dose_response(long_df, *, measurement, dose_col, condition_cols=None, unit_cols=None, ax=None):
    """Measurement vs a numeric condition field: unit means ± SEM (over units, not objects) at each
    dose. Returns ``(Figure, summary_df)`` — the per-dose n and mean±SEM behind the curve."""
    import matplotlib
    if ax is None:
        matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    unit_cols = list(unit_cols) if unit_cols else list(_DEFAULT_UNIT_COLS)
    units = aggregate_to_unit(long_df, measurement=measurement, unit_cols=unit_cols,
                              condition_cols=[dose_col])
    units = units.assign(_dose=pd.to_numeric(units['condition'], errors='coerce')).dropna(subset=['_dose'])

    summary = (units.groupby('_dose')['unit_value'].agg(['mean', 'std', 'size'])
               .reset_index().rename(columns={'_dose': dose_col, 'size': 'n_units'}))
    summary['sem_units'] = summary['std'] / np.sqrt(summary['n_units'].clip(lower=1))

    fig = ax.figure if ax is not None else plt.figure(figsize=(5, 4))
    ax = ax or fig.add_subplot(111)
    ax.errorbar(summary[dose_col], summary['mean'], yerr=summary['sem_units'].fillna(0),
                marker='o', capsize=3, color='#34495e', zorder=2, label='unit mean ± SEM')
    ax.scatter(units['_dose'], units['unit_value'], s=30, color='#c0392b', alpha=0.6, zorder=1)
    ax.set_xlabel(dose_col)
    ax.set_ylabel(measurement)
    ax.set_title(f"{measurement} dose–response (n over units: {'|'.join(unit_cols)})",
                 fontsize=9, loc='left')
    ax.legend(loc='best', fontsize=8, frameon=False)
    fig.tight_layout()
    return fig, summary


def measurement_matrix(long_df, *, measurements, condition_cols, unit_cols=None, kind='box'):
    """A small-multiples grid: one condition-comparison panel per measurement, for scanning several at
    once. Returns ``(Figure, summary_df)`` — the per-(measurement, condition) numbers stacked."""
    import matplotlib
    matplotlib.use('Agg', force=False)
    import matplotlib.pyplot as plt

    measurements = list(measurements)
    n = len(measurements)
    ncol = min(3, n) or 1
    nrow = int(np.ceil(n / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.5 * ncol, 3.8 * nrow), squeeze=False)

    summaries = []
    for k, meas in enumerate(measurements):
        ax = axes[k // ncol][k % ncol]
        _, s = condition_comparison(long_df, measurement=meas, condition_cols=condition_cols,
                                    unit_cols=unit_cols, kind=kind, ax=ax)
        s = s.copy()
        s.insert(0, 'measurement', meas)
        summaries.append(s)
    for k in range(n, nrow * ncol):                 # blank any unused cells
        axes[k // ncol][k % ncol].axis('off')

    fig.tight_layout()
    combined = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    return fig, combined
