"""**The workflow preset picker — a declared, provenanced starting point that populates but never locks (analysis_presets Part B).**

The preset OBJECTS — `AnalysisPreset`, the honestly-seeded registry, the import-time honesty invariants,
availability gating, and the populate-not-lock `PresetApplication` — are the Qt-free engine in
`utils.analysis_presets`. This is the picker surface: for a workflow it lists the presets that apply, each
with its description, its **provenance** (where the numbers came from — mandatory, never decorative), a
validated / starting-point badge, and any caveats; a preset the session cannot run yet is **greyed with the
reason** (from `preset_availability`, which reuses the one requirements vocabulary). Choosing one produces a
`PresetApplication` and hands it to the caller's `on_apply`, which seeds the workflow's parameter widgets —
the user may then change any value, and the application tracks the deviation. The picker never locks a value
and never invokes a workflow itself.

Thin and Qt-smoke tested; returns `None` when the workflow has no presets or Qt is unavailable."""
from __future__ import annotations

_CAVEAT_COLOUR = "#c98a00"     # amber — a caveat, not the field_status step-status red


def build_preset_picker(workflow_id, *, available=None, on_apply=None, parent=None):
    """Build the preset picker for ``workflow_id``, or ``None`` if it has no presets / Qt is unavailable.
    ``available`` is the set of requirement names the session provides (used to grey an unrunnable preset with
    its reason); ``on_apply(PresetApplication)`` receives the chosen preset's populate-not-lock application.
    Exposes ``_apply_buttons`` (preset key → button) for a Qt-smoke test."""
    try:
        from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QFrame
    except Exception:      # broad-ok: optional_probe — no Qt (headless) → no widget, callers guard on None
        return None
    from pycat.utils.analysis_presets import presets_for, preset_availability, PresetApplication

    presets = presets_for(workflow_id)
    if not presets:
        return None
    avail = set(available or ())

    widget = QWidget(parent)
    root = QVBoxLayout(widget)
    widget._apply_buttons = {}

    def _label(text, style=""):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        if style:
            lbl.setStyleSheet(style)
        return lbl

    def _apply(preset):
        application = PresetApplication(preset)
        if callable(on_apply):
            try:
                on_apply(application)
            except Exception:      # broad-ok: ui_cleanup — a bad consumer must not break the picker
                pass

    root.addWidget(_label("Starting-point presets — declared, provenanced parameters you can then edit.",
                          "font-weight: bold;"))
    for preset in presets:
        can_run, reason = preset_availability(preset, avail)
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        fl = QVBoxLayout(frame)
        badge = "  ✓ validated" if preset.validated else "  · starting point (not sensitivity-tested)"
        fl.addWidget(_label(f"<b>{preset.display_name}</b>{badge}"))
        fl.addWidget(_label(preset.description, "color: #444;"))
        fl.addWidget(_label("Provenance: " + preset.provenance, "color: gray; font-size: 11px;"))
        for caveat in preset.caveats:
            fl.addWidget(_label("⚠ " + caveat, f"color: {_CAVEAT_COLOUR}; font-size: 11px;"))
        btn = QPushButton("Apply these values")
        btn.setEnabled(bool(can_run))
        if not can_run:
            btn.setToolTip(reason or "Not available for this session yet.")
            fl.addWidget(_label(f"Unavailable — {reason}", "color: gray; font-size: 11px;"))
        btn.clicked.connect(lambda _=False, p=preset: _apply(p))
        fl.addWidget(btn)
        widget._apply_buttons[preset.key] = btn
        root.addWidget(frame)

    return widget
