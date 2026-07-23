"""**Register the default feature cards — give shipped-but-hidden capabilities a discoverable home.**

navigator increment 4. :mod:`feature_registry` holds the catalogue; this populates it with a card per
capability a beginner would otherwise never find, each opening the **real** feature. The card ``entry``
callables lazily import their UI opener, so this module stays Qt-free and ``core``-testable; the home dock
(``ui/home_dock``) is what invokes them.

**Only capabilities with a real opener are registered.** The spec's rule is that a card which opens nothing
is worse than an absent one, so a mock/placeholder card is never registered. Several shipped compute kernels
have NO UI surface at all yet — measurement stability, SMLM, kymographs, ratiometric, feature provenance,
analysis presets — and each needs a real opener built before it earns a card (tracked as a follow-on). The
capabilities surfaced here all have a verified opener; :data:`EXPECTED_CARD_KEYS` is the enumerated contract
a test asserts against, so a future capability that forgets to register a card is caught.
"""
from __future__ import annotations

from pycat.utils.feature_registry import FeatureCard, registry

# The capabilities that carry a card today — enumerated so the presence test can assert them and so the
# surfaced set is a visible, reviewable contract. Add a key here (and a card below) when a new opener lands.
EXPECTED_CARD_KEYS = (
    "data_qc",
    "control_validation",
    "spectral_unmixing",
    "comparative_figures",
    "feature_explorer",
)


def _data_qc_entry(cm):
    """Open the Data Quality dashboard — the real surface for image QC, scan aberrations, per-object
    biological QC and the reliability report (each is compute-only on its own; the dashboard is their home)."""
    def _open():
        from pycat.toolbox.data_qc_ui import _add_data_qc
        _add_data_qc(cm.toolbox_functions_ui, separate_widget=True)
    return _open


def _toolbox_method_entry(cm, method_name):
    """Open a capability whose opener is a ``_add_*`` method on the toolbox UI (a floating dock via
    ``separate_widget=True``)."""
    def _open():
        getattr(cm.toolbox_functions_ui, method_name)(separate_widget=True)
    return _open


def _comparative_figures_entry(cm):
    def _open():
        from pycat.ui.comparative_figures_ui import open_comparative_figures_dialog
        open_comparative_figures_dialog(cm, cm.viewer)
    return _open


def _feature_explorer_entry(cm):
    """Open the Feature Explorer over the current results table. The dock is a finished widget that was
    never wired; it needs a DataFrame, so we hand it the most specific results table in the repository and
    say so plainly when none is loaded (rather than opening an empty panel)."""
    def _open():
        from pycat.ui.feature_explorer_dock import build_feature_explorer_dock
        from pycat.utils.dock_space import add_results_dock
        dr = getattr(getattr(cm, "active_data_class", None), "data_repository", None) or {}
        table = None
        for key in ("consolidated_table", "cell_df", "per_droplet_df", "puncta_df"):
            candidate = dr.get(key) if hasattr(dr, "get") else None
            if candidate is not None:
                table = candidate
                break
        if table is None:
            try:
                from napari.utils.notifications import show_info
                show_info("Feature Explorer needs a results table — run an analysis first.")
            except Exception:      # broad-ok: optional_probe — the notification is best-effort, never gates
                pass
            return
        widget = build_feature_explorer_dock(table, selection_service=getattr(cm, "selection", None))
        if widget is not None:
            add_results_dock(cm.viewer.window, widget, name="Feature Explorer")
    return _open


def _default_cards(cm):
    """Build (do not register) the default cards, each closing over ``cm`` for its opener. Kept lazy so
    this module imports no Qt/toolbox code until a card is actually opened."""
    return [
        FeatureCard(
            key="data_qc", title="Data Quality Dashboard",
            summary="Check image quality, scan aberrations, and per-object biological QC before you trust a measurement.",
            category="Assess", entry=_data_qc_entry(cm), docs_anchor="data-quality"),
        FeatureCard(
            key="control_validation", title="Control Validation",
            summary="Validate your parameters against positive/negative controls and get recommended settings.",
            category="Assess", entry=_toolbox_method_entry(cm, "_add_control_validation"),
            docs_anchor="control-validation"),
        FeatureCard(
            key="spectral_unmixing", title="Spectral / Bleed-through Unmixing",
            summary="Separate overlapping fluorophore signals into clean per-channel images.",
            category="Correct", entry=_toolbox_method_entry(cm, "_add_run_spectral_unmixing"),
            docs_anchor="unmixing"),
        FeatureCard(
            key="comparative_figures", title="Comparative & Publication Figures",
            summary="Build grouped/faceted figures with honest statistics from the batch table, then refine them for publication.",
            category="Report", entry=_comparative_figures_entry(cm), docs_anchor="comparative-figures"),
        FeatureCard(
            key="feature_explorer", title="Feature Explorer",
            summary="Browse every measured feature with its plain-language meaning, units, and provenance.",
            category="Measure", entry=_feature_explorer_entry(cm), docs_anchor="feature-explorer"),
    ]


def register_default_feature_cards(central_manager, reg=None):
    """Populate ``reg`` (the process-wide registry by default) with the default cards, each opening the real
    feature via ``central_manager``. **Idempotent-safe:** a key already present is skipped, so calling this
    twice (startup, plus a test against the shared registry) does not raise on the duplicate-key guard."""
    reg = reg if reg is not None else registry()
    for card in _default_cards(central_manager):
        if card.key not in reg:
            reg.register(card)
    return reg
