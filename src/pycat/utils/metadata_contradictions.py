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
