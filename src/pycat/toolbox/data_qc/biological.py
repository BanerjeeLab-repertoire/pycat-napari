"""Biological QC — did the acquisition actually capture plausible biological objects (surfaced separately from the acquisition checks).

Moved VERBATIM out of `data_qc_tools` (data_qc_decomposition); that module re-exports these.
"""
from __future__ import annotations


from pycat.utils.general_utils import debug_log
from pycat.toolbox.data_qc._base import _not_applicable

def qc_biological_objects(object_table, labels=None, *, parent_labels=None, k=3.5):
    """Object-level QC as a report section — *"can I trust this OBJECT?"* beside the imaging checks.

    Runs ``biological_qc`` on the segmented-object table and returns one report dict per flag family,
    each stating **how many of the N objects** tripped it, in the same
    Assessment → Interpretation → Recommendation shape the imaging checks use. This is the second QC
    layer the roadmap names: imaging QC asks whether the image is trustworthy; this asks whether each
    measured object is.

    **Flags, never filters.** A flag count is a review hint, not a verdict — edge-touching is stated
    definitively (a truncated object is a measurement artefact), the rest are worded as observations
    ("unusual size") because a mitotic or dead cell is real data. ``labels`` enables the edge flag;
    ``parent_labels`` enables containment; both are optional and simply not reported when absent.
    """
    try:
        import pandas as pd
        from pycat.toolbox.biological_qc_tools import biological_qc, _FLAG_WORDS
    except Exception as exc:      # broad-ok: scientific_result — the object-QC add-on must never break the imaging report
        debug_log('qc_biological_objects: import failed', exc)
        return []

    table = pd.DataFrame(object_table)
    if table.empty:
        return [_not_applicable('Object QC (biological)',
                                "No segmented objects to assess — run segmentation first.")]

    try:
        result = biological_qc(table, labels=labels, parent_labels=parent_labels, k=k)
    except Exception as exc:      # broad-ok: scientific_result — object-level QC degrades to an N/A note, never a crash
        debug_log('qc_biological_objects: biological_qc failed', exc)
        return [_not_applicable('Object QC (biological)',
                                f"Object-level QC could not run on this table ({exc}).")]

    counts = result.attrs.get('qc_report', {})
    n = len(table)
    _DEFINITIVE = {'edge_touching'}     # a truncated object is objectively wrong, not a hint

    # A per-flag interpretation: what the flag means and what to do about it.
    _MEANING = {
        'edge_touching':     ("Objects truncated by the field edge have wrong area, shape, and total "
                              "intensity, and bias every population statistic.",
                              "Exclude edge-touching objects before comparing populations, or re-image "
                              "with the objects fully inside the field."),
        'size_outlier':      ("An object far from the population's size (robust MAD) may be an "
                              "oversegmented fragment or a merged pair / aggregate.",
                              "Review the flagged objects; a real size range is fine — recompute with "
                              "and without them and see if the conclusion holds."),
        'shape_outlier':     ("Unusual eccentricity or solidity can mark a segmentation error — but a "
                              "mitotic or dead cell is legitimately odd-shaped.",
                              "A review hint, not a rejection: look before excluding."),
        'intensity_outlier': ("An extreme-intensity object may be an aggregate, debris, or a saturated "
                              "region rather than a typical object.",
                              "Cross-check against the saturation report; decide explicitly whether to "
                              "keep it."),
        'containment_violation': ("A child object whose centroid falls outside every parent object is "
                              "usually a segmentation error (a condensate not in a cell).",
                              "Review the parent/child masks for the flagged objects."),
        'scan_shear':        ("Objects torn by motion during a raster scan have distorted shape — an "
                              "acquisition-geometry artifact, not biology.",
                              "Exclude motion-sheared objects, or re-image stable fields."),
    }

    section = [dict(
        name='Object QC (biological)', tier='core', status='info',
        value=float(n), unit='objects',
        headline=f"{n} object(s) assessed for biological / segmentation outliers",
        how="A second QC layer, at the object level: imaging QC asks whether the IMAGE is trustworthy; "
            "this asks whether each measured OBJECT is. Every flag below is REPORTED, never removed — "
            "excluding an object is your explicit decision.",
        good="", diag=None)]

    for flag, tripped in counts.items():
        tripped = int(tripped)
        frac = tripped / n if n else 0.0
        if flag in _DEFINITIVE:
            status = 'good' if tripped == 0 else ('warn' if frac <= 0.25 else 'bad')
        else:
            # Observation flags never read 'bad' — they are hints; many just means a variable population.
            status = 'good' if tripped == 0 else 'warn'
        meaning, rec = _MEANING.get(flag, ("", ""))
        word = _FLAG_WORDS.get('qc_' + flag, flag.replace('_', ' '))
        section.append(dict(
            name=f"· {word}", tier='core', status=status,
            value=float(tripped), unit='objects',
            headline=(f"{tripped} of {n} object(s) flagged ({frac*100:.0f}%)"
                      if tripped else f"none of {n} objects flagged"),
            how=meaning, good=rec, diag=None))
    return section
