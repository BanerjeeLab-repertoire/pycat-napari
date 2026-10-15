"""Time-series condensate plots -- moved out of ``timeseries/ui.py`` (1.7.005) to keep that module under its
concentration ceiling. Moved verbatim; matplotlib stays imported lazily so this module imports headless."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _plot_condensate_fraction(results_df: pd.DataFrame):
    """
    Plot mean condensate fraction vs frame for each cell, with a mean ± SD
    across all cells overlay.
    """
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4))
    frames = sorted(results_df['frame'].unique())

    for cell_label, cell_data in results_df.groupby('cell_label'):
        ax.plot(
            cell_data['frame'], cell_data['condensate_fraction'],
            alpha=0.5, linewidth=1, label=f"Cell {cell_label}"
        )

    # Mean ± SD across cells
    grouped = results_df.groupby('frame')['condensate_fraction']
    means = grouped.mean().reindex(frames).values
    stds  = grouped.std().reindex(frames).fillna(0).values

    ax.plot(frames, means, 'k-', linewidth=2, label='Mean')
    ax.fill_between(frames,
                    np.nan_to_num(means - stds),
                    np.nan_to_num(means + stds),
                    alpha=0.15, color='black')

    ax.set_xlabel('Frame', fontsize=12)
    ax.set_ylabel('Condensate fraction (area/cell area)', fontsize=12)
    ax.set_title('Condensate area fraction vs time', fontsize=13)
    ax.legend(fontsize=8, loc='upper right')
    ax.minorticks_on()
    plt.tight_layout()
    # Non-blocking show: napari's Qt event loop is already running, so a blocking
    # plt.show() triggers "QCoreApplication::exec: The event loop is already
    # running". block=False lets the figure display without starting a second loop.
    plt.show(block=False)
