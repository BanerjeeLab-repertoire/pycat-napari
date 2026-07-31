"""Navigator-generated method panel (Method-Widget Spec 1.2).

A PyCAT method panel is an ordered sequence of ``_add_*`` section builders; a Navigator plan is an ordered
sequence of steps. ``GeneratedMethodUI`` is the join: it walks the plan in execution order (via the
headless-tested :func:`~pycat.navigator.sections.resolve_plan_sections`) and calls the bound builder for each
step into one layout. Because every builder already produces complete controls — layer dropdowns with tag
bindings, status circles, tooltips, run buttons — the result is a fully functional panel with **no per-step UI
code**. Unmapped steps render a visible placeholder, never silently dropped.

It subclasses ``AnalysisMethodsUI``, so it inherits the workflow header, the pixel-size gate, the dock lifecycle,
and the save/clear footer for free, and it docks through the same path as every hand-written panel — nothing
downstream needs to know it was generated.

**GUI-bound.** This module imports Qt and cannot run in the headless gate. The decisions it renders —
``resolve_plan_sections`` (which builders, in what order, which are gaps) and ``placeholder_text`` — are tested
headlessly in ``tests/navigator/test_section_coverage.py``; this class is the thin Qt shell that translates them
into widgets. Acceptance (Spec 1.6) is a manual napari run of the cell/condensate pipeline.
"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QLabel, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from pycat.ui.analysis_methods_ui import AnalysisMethodsUI
from pycat.ui.base_ui import _relax_min_widths, _apply_scroll_guard
from pycat.navigator.sections import resolve_plan_sections, builder_for, placeholder_text
from pycat.utils.general_utils import debug_log


class GeneratedMethodUI(AnalysisMethodsUI):
    """A method panel assembled from a Navigator plan (see the module docstring)."""

    def __init__(self, viewer, central_manager, plan, *, review=None, intent=None, name=None):
        super().__init__(viewer, central_manager)
        self.method_layout = QVBoxLayout()
        # Provenance (Spec 1.2 decision 5): stored now even though nothing reads it yet — Spec 2 persists it and
        # Spec 4 rebuilds from it. Retrofitting provenance is always worse than storing it up front.
        self._plan = plan
        self._intent = intent if intent is not None else getattr(plan, "intent", None)
        self._review = dict(review or {})
        self._name = name or "Generated Method"

    # ── Spec 1.3: seed the reviewed parameters BEFORE building ──────────────────────────────────────────
    def _seed_reviewed_parameters(self):
        """Write the reviewed values into the data repository so each section constructs already seeded. The
        builders read their defaults from the repository, so seeding first (rather than reaching into constructed
        widgets, which would need per-builder knowledge) is what makes the reviewed values reach the sections.
        A reviewed parameter with no repository home is skipped and logged — never given an invented location."""
        repo = getattr(getattr(self.central_manager, "active_data_class", None), "data_repository", None)
        if repo is None or not isinstance(self._review, dict):
            return
        for key, value in self._review.items():
            try:
                repo[key] = value
            except Exception as exc:  # broad-ok: ui_cleanup — a bad reviewed key must not abort the panel build
                debug_log(f"generated method: could not seed reviewed param {key!r}", exc)

    def _add_placeholder(self, op_id):
        """A visible stand-in for a step with no wired section — names the step and where to run it. A generated
        panel that quietly dropped a step the plan said was necessary would be a scientific-integrity failure."""
        label = QLabel(placeholder_text(op_id))
        label.setWordWrap(True)
        label.setStyleSheet("color: #b9770e; font-size: 11px; border: 1px solid #b9770e; "
                            "border-radius: 4px; padding: 4px; margin: 2px;")
        self.method_layout.addWidget(label)

    # ── Spec 4 live revision: build/reuse a session so a chosen alternative can swap + recompile ──────────
    def _ensure_session(self):
        """The session used to live-revise this panel (Spec 4). Built once from the panel's intent + the live
        data context and cached, so successive revisions accumulate their pins (swap the segmenter, then swap the
        enhancer, and both stick). ``None`` when the plan carries no intent — revision needs an intent to recompile
        against, so without one the panel is guidance-only."""
        if self._intent is None:
            return None
        if getattr(self, "_session", None) is None:
            from pycat.navigator.session import NavigatorSession, context_from_session
            s = NavigatorSession()
            s.intent = self._intent
            try:
                s.ctx = context_from_session(self.central_manager)
            except Exception as exc:  # broad-ok: optional_probe — revision falls back to default context
                debug_log("generated method: could not populate revision context; using defaults", exc)
            self._session = s
        return self._session

    def _peer_alternatives(self, op_id):
        """The peer ops this section could be swapped for (empty if revision is unavailable or none exist)."""
        session = self._ensure_session()
        if session is None:
            return []
        try:
            from pycat.navigator.session import alternatives_for_op
            return alternatives_for_op(session, op_id)
        except Exception as exc:  # broad-ok: optional_probe — alternatives are a nicety, never fatal
            debug_log(f"generated method: could not compute alternatives for {op_id!r}", exc)
            return []

    def _selection_scores(self):
        """The Spec 5 'why this one' scores for the segmenter section, keyed by op-id (empty when revision is off
        or the plan has no segmenter). Computed once per pop-out open, against the session's current pins."""
        session = self._ensure_session()
        if session is None:
            return {}
        try:
            from pycat.navigator.session import segmentation_scores
            return segmentation_scores(session)
        except Exception as exc:  # broad-ok: optional_probe — reasoning is decoration, never fatal
            debug_log("generated method: could not compute selection scores", exc)
            return {}

    def _revise_to(self, current_op_id, new_op_id):
        """Swap ``current_op_id`` for ``new_op_id`` and rebuild the panel from the amended plan (Spec 4). The
        recompiled panel is docked first, then this one's dock removed, so the surface never blinks empty. The
        session (with its pins) is carried forward so the next revision stacks on this one."""
        session = self._ensure_session()
        if session is None:
            return
        from pycat.navigator.session import revise_plan
        amended = revise_plan(session, current_op_id, new_op_id)
        replacement = GeneratedMethodUI(self.viewer, self.central_manager, amended,
                                        review=self._review, intent=self._intent, name=self._name)
        replacement._session = session
        replacement.setup_ui()
        self._remove_dock()

    def _remove_dock(self):
        dock = getattr(self, "_dock", None)
        if dock is None:
            return
        try:
            self.viewer.window.remove_dock_widget(dock)
        except Exception as exc:  # broad-ok: ui_cleanup — a stale dock is harmless if removal fails
            debug_log("generated method: could not remove superseded panel dock", exc)

    def _add_guidance_affordance(self, op_id):
        """A small `?`-style link before each section (Spec 4): clicking it pops out the op's when-to-use /
        advantages / limitations / alternatives in place, and — when peer ops exist — a **Use this instead**
        button per alternative that swaps the step and rebuilds the panel (live revision). Best-effort — a guidance
        link must never block or crash the section it decorates."""
        from PyQt5.QtWidgets import QToolButton
        from pycat.ui.guidance_popout import open_guidance_popout
        alts = self._peer_alternatives(op_id)
        on_revise = (lambda new, cur=op_id: self._revise_to(cur, new)) if alts else None
        btn = QToolButton()
        btn.setText("❔  " + op_id.split(".")[-1].replace("_", " "))   # ❔
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setCursor(Qt.PointingHandCursor)
        tip = f"When to use {op_id}, its tradeoffs" + (", and swap it for an alternative" if alts else "")
        btn.setToolTip(tip)
        btn.setStyleSheet("QToolButton { color: #0d7d78; border: none; font-size: 11px; padding: 2px 0; }"
                          "QToolButton:hover { text-decoration: underline; }")
        btn.clicked.connect(lambda _=False, o=op_id, a=alts, r=on_revise:
                            open_guidance_popout(self, o, alternatives=a, on_revise=r,
                                                 scores=self._selection_scores()))
        self.method_layout.addWidget(btn)

    def setup_ui(self):
        self._seed_reviewed_parameters()

        # Header + pixel gate first, exactly like every hand-written panel.
        self._add_workflow_header(self.method_layout, include_pixel_gate=True)

        # One section per planned step, in the executor's order. A mapped step's builder is called into the
        # layout; an unmapped step (or a builder that raises) renders the placeholder instead of vanishing. Each
        # section gets a guidance affordance (Spec 4): a `?` that pops out the op's when-to-use / limitations /
        # alternatives in place.
        for section in resolve_plan_sections(self._plan):
            self._add_guidance_affordance(section.op_id)
            builder = None if section.gap else builder_for(self.central_manager, section.op_id)
            if builder is None:
                self._add_placeholder(section.op_id)
                continue
            try:
                builder(layout=self.method_layout)
            except Exception as exc:  # broad-ok: ui_cleanup — one bad section must not kill the whole panel
                debug_log(f"generated method: builder for {section.op_id!r} raised", exc)
                self._add_placeholder(section.op_id)

        # Footer, always present.
        self.central_manager.toolbox_functions_ui._add_save_and_clear(layout=self.method_layout)

        # Wrap + dock through the same scroll-area path as CondensateAnalysisUI.
        main_widget = QWidget()
        main_widget.setLayout(self.method_layout)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        main_widget.setMinimumWidth(0)
        try:
            _relax_min_widths(main_widget)
        except Exception:  # broad-ok: ui_cleanup — optional width relaxation, non-fatal to the dock
            pass
        scroll_area.setWidget(main_widget)
        # Keep the dock handle so a live revision (Spec 4) can remove this panel once its replacement is docked.
        self._dock = self.viewer.window.add_dock_widget(scroll_area, name=self._name)
        main_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.method_layout.setAlignment(Qt.AlignTop)
        _apply_scroll_guard(main_widget)
