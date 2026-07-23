"""**Surface metadata contradictions without training the user to ignore them.**

A microscope file can carry internally inconsistent metadata — a Zeiss ZEN export that says the objective is
oil immersion while ObjectiveSettings say the medium is air (refractive index 1.518 giving the game away) —
or metadata that disagrees with what the pixels look like. A user who is shown every such disagreement, on
every file, stops reading them: the warning becomes wallpaper. This detects contradictions and keeps the
signal meaningful, three ways: **severity** (only a *quantitative* contradiction raises the red flag — most
files show nothing, so red keeps meaning something); **cry-wolf discipline** (a clean file must raise ZERO —
enforced by tests); and a **per-pattern anti-numbing store** — a user can mark a contradiction "expected for
this instrument" and it is demoted, but keyed to the acquisition FINGERPRINT (never the file, or the user
re-dismisses forever), per-pattern only (no global mute), reversibly, and with a developer-facing precision
signal so a pattern that is *always* dismissed is fixed in the rule rather than absorbed by the user.

Detection never blocks and metadata always wins — this records and shows, it does not override. Qt-free: the
red button indicator, the tooltip, and the dialog listing are the UI surface over this engine.
"""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Contradiction:
    """One detected disagreement. ``pattern`` is the stable RULE key (what the anti-numbing store is keyed on,
    with the fingerprint — never the file). ``severity`` is ``'critical'`` (quantitative → red flag) or
    ``'info'`` (cosmetic / metadata-wins → recorded, no flag). ``message`` names it CONCRETELY (a vague
    warning is ignorable, a specific one is actionable)."""
    pattern: str
    severity: str
    message: str
    fields: tuple = ()


def _norm(v):
    return str(v).strip().lower() if v not in (None, '') else None


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _medium_from_ri(ri):
    """The immersion medium a refractive index implies, or None. Oil ≈ 1.51–1.52, water ≈ 1.33, air ≈ 1.0."""
    if ri is None:
        return None
    if 1.50 <= ri <= 1.54:
        return 'oil'
    if 1.30 <= ri <= 1.36:
        return 'water'
    if 0.98 <= ri <= 1.02:
        return 'air'
    return None


#: Coarse optical category, so a declared modality only conflicts with the pixels across a real divide
#: (fluorescence vs brightfield), never on wording ('widefield fluorescence' vs 'fluorescence').
def _optical_category(text):
    t = _norm(text) or ''
    if any(k in t for k in ('fluor', 'laserscanning', 'confocal', 'epi')):
        return 'fluorescence'
    if any(k in t for k in ('brightfield', 'bright field', 'bf', 'phase', 'dic', 'transmitted')):
        return 'brightfield'
    return None


def detect_contradictions(metadata, *, pixel_modality=None):
    """Every contradiction in ``metadata`` (a normalised metadata dict), most-severe first. Never blocks;
    metadata always wins — this records. ``pixel_modality`` is the pixel classifier's call, for the
    metadata-vs-pixels check. A clean, consistent file returns ``[]`` (the cry-wolf contract)."""
    md = metadata or {}
    out = []

    # ── internal: objective immersion vs objective-settings medium (a real Zeiss ZEN inconsistency) ──
    imm = _norm(md.get('immersion') or md.get('objective_immersion'))
    med = _norm(md.get('medium') or md.get('objective_medium'))
    ri = _safe_float(md.get('refractive_index') or md.get('ri'))
    if imm and med and imm != med:
        ri_medium = _medium_from_ri(ri)
        ri_note = f" (RI {ri:.3f} indicates {ri_medium})" if ri_medium else ""
        out.append(Contradiction(
            pattern='immersion_vs_medium', severity='critical',
            message=(f"Objective says {imm.title()} immersion; ObjectiveSettings says {med.title()} "
                     f"medium{ri_note}. This changes the effective NA and the microns-per-pixel."),
            fields=('immersion', 'medium', 'refractive_index')))

    # ── metadata vs pixels: declared modality disagreeing with the pixel classifier (metadata wins) ──
    declared_cat = _optical_category(md.get('modality') or md.get('acquisition_mode')
                                     or md.get('contrast_method'))
    pixel_cat = _optical_category(pixel_modality)
    if declared_cat and pixel_cat and declared_cat != pixel_cat:
        out.append(Contradiction(
            pattern='modality_vs_pixels', severity='info',
            message=(f"Metadata declares a {declared_cat} channel, but the pixels look like {pixel_cat}. "
                     f"Metadata wins (the declared modality is kept); the disagreement is recorded."),
            fields=('modality',)))

    out.sort(key=lambda c: 0 if c.severity == 'critical' else 1)
    return out


def has_critical(contradictions) -> bool:
    """Whether ANY contradiction is critical — the single trigger for the red button indicator (info-only
    contradictions are recorded in the dialog but never raise the flag; that is the biggest anti-numbing
    lever)."""
    return any(c.severity == 'critical' for c in contradictions)


# ── The anti-numbing store: 'expected for this instrument', per-pattern, per-fingerprint ──────────

def acquisition_fingerprint(metadata) -> str:
    """A stable key for the ACQUISITION (instrument / software / objective) — what an 'expected' judgement
    is keyed on, so a known vendor quirk is remembered for that setup, never for one file."""
    md = metadata or {}
    parts = [_norm(md.get('instrument') or md.get('microscope')) or '',
             _norm(md.get('software')) or '',
             _norm(md.get('objective')) or '']
    return '|'.join(parts) or 'unknown'


def _key(pattern, fingerprint):
    return f"metadata.expected.{fingerprint}.{pattern}"


def is_expected(pattern, fingerprint, store) -> bool:
    """True if the user has marked ``pattern`` expected for this acquisition ``fingerprint``."""
    return store.get_bool(_key(pattern, fingerprint), False)


def mark_expected(pattern, fingerprint, store, expected=True):
    """Mark (or unmark — it is REVERSIBLE) ``pattern`` as expected for ``fingerprint``. Keyed to the
    fingerprint, NEVER the file. Also records, in a precision-signal set, which fingerprints marked this
    pattern — so a pattern marked expected across MANY fingerprints surfaces as a probable rule bug."""
    store.set(_key(pattern, fingerprint), bool(expected))
    seen_key = f"metadata.expected_fingerprints.{pattern}"
    seen = set(store.get(seen_key, []) or [])
    if expected:
        seen.add(fingerprint)
    else:
        seen.discard(fingerprint)
    store.set(seen_key, sorted(seen))


def apply_expectations(contradictions, fingerprint, store):
    """Demote (do NOT delete) any contradiction the user marked expected for this fingerprint to ``info`` —
    so it still appears in the dialog, greyed, reversibly. There is deliberately no 'ignore all'; suppression
    is per-pattern only."""
    out = []
    for c in contradictions:
        if c.severity != 'info' and is_expected(c.pattern, fingerprint, store):
            out.append(dataclasses.replace(
                c, severity='info',
                message=c.message + " (You marked this expected for this instrument.)"))
        else:
            out.append(c)
    return out


def rules_dismissed_across_many_fingerprints(store, *, threshold=3):
    """The developer-facing precision signal: patterns marked 'expected' across ``threshold``+ distinct
    acquisition fingerprints. A contradiction that is *always* dismissed is a bug in the RULE (a false
    positive), not something to keep asking the user about — surface it so the rule is fixed, not the user
    numbed. Returns ``{pattern: n_fingerprints}``."""
    out = {}
    for pattern in ('immersion_vs_medium', 'modality_vs_pixels'):
        n = len(store.get(f"metadata.expected_fingerprints.{pattern}", []) or [])
        if n >= threshold:
            out[pattern] = n
    return out


# ── Surfacing over loaded metadata: the report + the (Qt-free, duck-typed) indicator ──────────────
#
# Part 3 of tag_confidence: turn the engine above into what the UI shows. The metadata button gains a WARNING
# GLYPH (deliberately not the field_status step-status red — a different concept) and a CONCRETE tooltip when a
# file carries a critical contradiction; clicking still opens the existing metadata dialog. Never blocks.


@dataclasses.dataclass(frozen=True)
class ContradictionReport:
    """What the UI reads for one loaded file: the ``contradictions`` (after the user's 'expected' judgements
    are applied), whether any is ``critical`` (the sole trigger for the warning indicator), and the acquisition
    ``fingerprint`` (so the dialog can offer 'expected for this instrument')."""
    contradictions: tuple = ()
    fingerprint: str = 'unknown'
    is_critical: bool = False


def _engine_input(file_metadata):
    """Flatten a loaded ``{common, raw}`` metadata dict into the keys :func:`detect_contradictions` /
    :func:`acquisition_fingerprint` read. Immersion / medium / refractive index live in the per-file
    ``raw['instrument']`` block (added by the deep-metadata OME parse); the declared modality comes from the
    curated common fields or the first channel. Absent fields stay absent — the engine is cry-wolf-clean."""
    md = file_metadata or {}
    common = md.get('common') or {}
    raw = md.get('raw') or {}
    inst = raw.get('instrument') or {}
    channels = raw.get('channels') or []
    ch0 = channels[0] if channels else {}
    return {
        'immersion': inst.get('immersion') or common.get('immersion'),
        'medium': inst.get('medium') or common.get('medium'),
        'refractive_index': inst.get('refractive_index') or common.get('refractive_index'),
        'modality': (common.get('modality') or ch0.get('contrast_method') or ch0.get('acquisition_mode')),
        'acquisition_mode': ch0.get('acquisition_mode') or common.get('acquisition_mode'),
        'contrast_method': ch0.get('contrast_method'),
        'instrument': common.get('microscope') or common.get('instrument') or inst.get('instrument'),
        'software': common.get('software'),
        'objective': common.get('objective'),
    }


def contradiction_report(file_metadata, *, pixel_modality=None, store=None) -> ContradictionReport:
    """Detect the contradictions in a loaded ``{common, raw}`` metadata dict, applying the user's per-pattern
    'expected' judgements when a ``store`` is given, and return the :class:`ContradictionReport` the UI reads.
    Empty / absent metadata → an all-clear report (the cry-wolf contract, end to end)."""
    md = _engine_input(file_metadata)
    contradictions = detect_contradictions(md, pixel_modality=pixel_modality)
    fingerprint = acquisition_fingerprint(md)
    if store is not None:
        contradictions = apply_expectations(contradictions, fingerprint, store)
    return ContradictionReport(contradictions=tuple(contradictions), fingerprint=fingerprint,
                               is_critical=has_critical(contradictions))


#: The neutral metadata-button label (info glyph); the warning label swaps the glyph so the indicator reads as
#: a DISTINCT concept from the field_status step-status red (which means 'required input missing').
_METADATA_LABEL = "ⓘ  Metadata"     # ⓘ
_METADATA_WARN_LABEL = "⚠  Metadata"  # ⚠


def indicator_label(report, *, base=_METADATA_LABEL, warn=_METADATA_WARN_LABEL) -> str:
    """The metadata action's label for ``report``: the warning glyph when a critical contradiction is present,
    the neutral info glyph otherwise."""
    return warn if report.is_critical else base


def report_tooltip(report, *, clean_text="Acquisition metadata for the loaded file.") -> str:
    """A CONCRETE multi-line tooltip naming each contradiction (critical first — the engine already sorts),
    or ``clean_text`` when there are none. A vague warning is ignorable; a specific one is actionable."""
    if not report.contradictions:
        return clean_text
    lines = ["Metadata contradictions found — click for details:"]
    for c in report.contradictions:
        lines.append(("⚠ " if c.severity == 'critical' else "• ") + c.message)
    return "\n".join(lines)


def install_metadata_indicator(meta_action, viewer, *, get_metadata, store=None,
                               base_label=_METADATA_LABEL, clean_tooltip="Acquisition metadata for the "
                               "loaded file."):
    """Keep the metadata action's label + tooltip in sync with the loaded file's contradictions, refreshing
    whenever a layer is inserted (a file sets its metadata, then adds layers). **Never blocks** — it only
    restyles the button; clicking it still opens the metadata dialog.

    Qt-free by duck-typing so it is core-tested with fakes: ``meta_action`` needs ``setText`` / ``setToolTip``,
    ``viewer`` needs ``layers.events.inserted.connect``, and ``get_metadata()`` returns the current
    ``{common, raw}`` (or ``None``). Returns the refresh callable (also useful to call after a manual reload)."""
    def _refresh(*_):
        try:
            report = contradiction_report(get_metadata() or {}, store=store)
            meta_action.setText(indicator_label(report, base=base_label))
            meta_action.setToolTip(report_tooltip(report, clean_text=clean_tooltip))
        except Exception:      # broad-ok: ui_cleanup — a bad metadata dict must never break the toolbar
            pass

    try:
        viewer.layers.events.inserted.connect(_refresh)
    except Exception:      # broad-ok: optional_probe — no live viewer/events (headless) → still refresh once
        pass
    _refresh()
    return _refresh
