"""Figures for `benchmarks/mask_level_calibration.py` (Phase 1 report).

Kept separate so the calibration itself has no plotting dependency. Colours are the fixed
categorical order of the validated reference palette (blue, orange, aqua), always assigned to
the same entity: FWHM / annotator Gable = blue, C=3 sigma / Meet = orange, A(r) / Shamli = aqua.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from benchmarks.mask_level_calibration import LEVELS, _level_for_rule

SERIES = ('#2a78d6', '#eb6834', '#1baf7a')
INK, INK_2, MUTED, GRID, SURFACE = '#0b0b0b', '#52514e', '#898781', '#e6e5e0', '#fcfcfb'
RADIUS_EDGES = np.array([1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8, 12])


def _style(plt):
    plt.rcParams.update({
        'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
        'axes.edgecolor': MUTED, 'axes.labelcolor': INK_2, 'xtick.color': MUTED,
        'ytick.color': MUTED, 'text.color': INK, 'axes.grid': True, 'grid.color': GRID,
        'grid.linewidth': 0.8, 'axes.spines.top': False, 'axes.spines.right': False,
        'font.size': 10, 'axes.titlesize': 11, 'axes.titleweight': 'bold', 'lines.linewidth': 2,
    })


def _plain_log_ticks(ax, axis='x'):
    from matplotlib.ticker import NullFormatter
    getattr(ax, f'{axis}axis').set_minor_formatter(NullFormatter())


def _binned(x, y, edges):
    centers, med, q1, q3 = [], [], [], []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        sel = (x >= lo) & (x < hi)
        if sel.sum() >= 15:
            centers.append(np.sqrt(lo * hi))
            med.append(np.median(y[sel]))
            q1.append(np.percentile(y[sel], 25))
            q3.append(np.percentile(y[sel], 75))
    return map(np.array, (centers, med, q1, q3))


def fig_level_vs_radius(plt, good, coef, path):
    r = np.array([o['eq_radius_px'] for o in good])
    a = np.array([o['A_frac_amplitude'] for o in good])
    c, med, q1, q3 = _binned(r, a, RADIUS_EDGES)
    fig, ax = plt.subplots(figsize=(7, 4.2))
    ax.fill_between(c, q1, q3, color=SERIES[2], alpha=0.18, lw=0, label='interquartile range')
    ax.plot(c, med, 'o', color=SERIES[2], ms=8, mec=SURFACE, mew=2, label='median level humans traced')
    xs = np.linspace(1, 12, 200)
    ax.plot(xs, np.clip(coef[0] + coef[1] * np.log(xs), 0.2, 0.8), color=SERIES[2], lw=2,
            label=f'fit A(r) = {coef[0]:.2f} {coef[1]:+.2f} ln r, clamped [0.2, 0.8]')
    ax.axhline(0.5, color=MUTED, lw=1.5, ls='--')
    ax.text(11.8, 0.515, 'FWHM (current default, 0.5)', color=INK_2, ha='right', va='bottom', fontsize=9)
    ax.set_xscale('log')
    ax.set_xticks([1, 2, 3, 4, 6, 8, 12], ['1', '2', '3', '4', '6', '8', '12'])
    ax.set_xlabel('object equivalent radius (px; 1 px = 0.098 µm)')
    ax.set_ylabel('best-fit level, fraction of amplitude (A)')
    ax.set_title(f'Humans trace further out as objects get larger (n = {len(good)})', loc='left')
    ax.set_ylim(0, 1)
    ax.legend(frameon=False, loc='lower left', fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_area_ratio(plt, human, rules, path):
    shown = (('FWHM A=0.50 (current)', 'FWHM 0.5 (current)'), ('C=3.0 sigma', '3σ over background'),
             ('A(r) clamp [0.20,0.80]', 'size rule A(r)'))
    r = np.array([o['eq_radius_px'] for o in human])
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for color, (key, label) in zip(SERIES, shown, strict=True):
        ratio = []
        for o in human:
            i = int(np.argmin(abs(LEVELS - _level_for_rule(o, rules[key]))))
            ratio.append(o['_areas'][i] / o['area_px'])
        c, med, _q1, _q3 = _binned(r, np.array(ratio), RADIUS_EDGES)
        ax.plot(c, med, '-o', color=color, ms=7, mec=SURFACE, mew=2, label=label)
        ax.annotate(label, (c[-1], med[-1]), xytext=(6, 0), textcoords='offset points',
                    color=INK_2, fontsize=9, va='center')
    ax.axhline(1.0, color=MUTED, lw=1.5, ls='--')
    ax.set_xscale('log')
    ax.set_yscale('log')
    ax.set_xticks([1, 2, 3, 4, 6, 8, 12], ['1', '2', '3', '4', '6', '8', '12'])
    ax.set_yticks([0.5, 0.75, 1, 1.5, 2, 3], ['0.5', '0.75', '1', '1.5', '2', '3'])
    ax.set_xlim(1.1, 16)
    _plain_log_ticks(ax, 'x')
    _plain_log_ticks(ax, 'y')
    ax.set_xlabel('object equivalent radius (px)')
    ax.set_ylabel('median predicted / human area')
    ax.set_title('FWHM over-draws small objects and under-draws large ones', loc='left')
    ax.legend(frameon=False, loc='upper right', fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_parameterizations(plt, good, path):
    panels = (('A_frac_amplitude', 'A: fraction of amplitude', (-0.3, 1.0)),
              ('B_rel_over_bg', 'B: relative over background', (0, 6)),
              ('C_noise_units', 'C: noise units (σ over bg)', (0, 12)))
    fig, axes = plt.subplots(1, 3, figsize=(10, 3.2))
    for ax, (key, title, rng) in zip(axes, panels, strict=True):
        v = np.array([o[key] for o in good])
        v = v[np.isfinite(v)]
        ax.hist(np.clip(v, *rng), bins=40, range=rng, color=SERIES[0], edgecolor=SURFACE, lw=1)
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        ax.axvline(med, color=INK, lw=1.5)
        ax.set_title(title, loc='left', fontsize=10)
        ax.text(0.98, 0.95, f'median {med:.2f}\nIQR {q1:.2f}–{q3:.2f}\nCV {v.std() / abs(v.mean()):.2f}',
                transform=ax.transAxes, ha='right', va='top', fontsize=9, color=INK_2)
        ax.set_yticks([])
    fig.suptitle('Where humans trace, in three parameterizations', x=0.01, ha='left',
                 fontweight='bold', fontsize=11)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_annotators(plt, good, path):
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for color, who in zip(SERIES, ('gable', 'meet', 'shamli'), strict=True):
        sel = [o for o in good if o['annotator'] == who]
        c, med, _q1, _q3 = _binned(np.array([o['eq_radius_px'] for o in sel]),
                                   np.array([o['A_frac_amplitude'] for o in sel]), RADIUS_EDGES)
        ax.plot(c, med, '-o', color=color, ms=7, mec=SURFACE, mew=2, label=f'{who.title()} (n={len(sel)})')
    ax.axhline(0.5, color=MUTED, lw=1.5, ls='--')
    ax.set_xscale('log')
    ax.set_xticks([1, 2, 3, 4, 6, 8, 12], ['1', '2', '3', '4', '6', '8', '12'])
    ax.set_ylim(0, 1)
    ax.set_xlabel('object equivalent radius (px)')
    ax.set_ylabel('median best-fit level (A)')
    ax.set_title('All three annotators show the same size trend', loc='left')
    ax.legend(frameon=False, loc='lower left', fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def draw_all(human, good, coef, rules, fig_dir):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    _style(plt)
    fig_dir = Path(fig_dir)
    fig_dir.mkdir(parents=True, exist_ok=True)
    fig_level_vs_radius(plt, good, coef, fig_dir / 'level_vs_radius.png')
    fig_area_ratio(plt, human, rules, fig_dir / 'area_ratio_by_size.png')
    fig_parameterizations(plt, good, fig_dir / 'parameterizations.png')
    fig_annotators(plt, good, fig_dir / 'annotators.png')
