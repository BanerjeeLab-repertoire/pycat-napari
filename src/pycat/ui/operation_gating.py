"""Runnability gating for the live UI — disable a Run button, with a stated reason, when the current
session does not provide an operation's declared ``requirements``.

The decision logic already exists and is tested headlessly (``navigator.operation_spec``): a spec's
``requirements`` (from the controlled ``tag_registry.REQUIREMENTS`` vocabulary — z-stack, time axis,
calibrated pixel size, two channels, GPU) checked against the set of facts the session currently
provides. **What was missing is the set of facts** — nothing computed "what does the loaded data give
us right now". :func:`session_facts` is that function; :func:`gate_run_button` wires it to a button so a
3D operation greys out on a 2D image saying *"needs a 3D z-stack"* instead of failing when clicked.

Fail-open by construction: if the facts cannot be computed (a bug, an unexpected repo shape), the button
is LEFT ENABLED — a gating helper must never lock the user out of their own tool because of its own
error.
"""
from __future__ import annotations


def session_facts(central_manager=None, viewer=None) -> set:
    """The requirement-fact names the CURRENT session provides — the ``available`` set
    :func:`navigator.operation_spec.operation_availability` checks against.

    Each token is derived from the existing predicate the rest of the app already uses (so the gate
    agrees with the tools): ``time_axis``←``has_time_axis``, ``pixel_size``←``has_real_pixel_size``,
    ``gpu``←``gpu_available``; ``z_stack`` from a loaded image layer's ``axis_order`` tag (or the file's
    ``n_z``); ``two_channels`` from the file's ``n_channels``. Every probe is guarded — a missing signal
    simply means that fact is absent, never an exception.
    """
    facts: set = set()

    dr = None
    try:
        dr = central_manager.active_data_class.data_repository
    except Exception:                                # broad-ok: optional_probe — no active data class → no repo facts
        dr = None

    if dr is not None:
        try:
            from pycat.utils.frame_interval import has_time_axis
            if has_time_axis(dr):
                facts.add('time_axis')
        except Exception:                            # broad-ok: optional_probe — predicate unavailable → fact absent
            pass
        try:
            from pycat.utils.pixel_size import has_real_pixel_size
            if has_real_pixel_size(dr):
                facts.add('pixel_size')
        except Exception:                            # broad-ok: optional_probe — predicate unavailable → fact absent
            pass
        try:
            common = (dr.get('file_metadata', {}) or {}).get('common', {}) or {}
            if int(common.get('n_channels') or 0) >= 2:
                facts.add('two_channels')
            if int(common.get('n_z') or 0) > 1:
                facts.add('z_stack')                 # metadata fallback for the z-axis
        except Exception:                            # broad-ok: optional_probe — metadata shape varies → skip
            pass

    # z-stack, primary signal: a loaded image layer tagged with a Z axis.
    if viewer is not None:
        try:
            from pycat.utils.layer_tags import get_tag
            import napari.layers as _nl
            for layer in getattr(viewer, 'layers', []):
                if not isinstance(layer, _nl.Image):
                    continue
                axes = str(get_tag(layer, 'axis_order', '') or '')
                if 'Z' in axes or str(get_tag(layer, 'stack_axis', '') or '') == 'Z':
                    facts.add('z_stack')
                    break
        except Exception:                            # broad-ok: optional_probe — tags/napari absent → rely on metadata fallback
            pass

    try:
        from pycat.toolbox.gpu_utils import gpu_available
        if gpu_available():
            facts.add('gpu')
    except Exception:                                # broad-ok: optional_probe — no GPU backend → fact absent
        pass

    return facts


def gate_run_button(button, requirements, central_manager=None, viewer=None, *, base_tooltip=""):
    """Disable ``button`` with a stated reason when the session lacks the operation's ``requirements``,
    and keep it in sync as layers change. Returns the ``refresh`` callable (call it to re-evaluate).

    ``requirements`` is a tuple from ``tag_registry.REQUIREMENTS`` (usually ``spec.requirements``). The
    reason phrasing (*"needs a 3D z-stack"*) comes straight from that vocabulary via
    ``operation_availability``. Fail-open: any error leaves the button enabled.
    """
    from pycat.navigator.operation_spec import OperationSpec, operation_availability

    reqs = tuple(requirements or ())
    spec = OperationSpec(id="", role="", summary="", target=None, produces="",
                         aliases=(), registered_by=None, requirements=reqs)

    def refresh(*_):
        try:
            facts = session_facts(central_manager, viewer)
            can, reason = operation_availability(spec, facts)
        except Exception:                            # broad-ok: ui_cleanup — fail-open — never lock the user out on our error
            try:
                button.setEnabled(True)
            except Exception:                        # broad-ok: ui_cleanup — button already gone
                pass
            return
        try:
            button.setEnabled(bool(can))
            if can:
                button.setToolTip(base_tooltip)
            else:
                tip = f"Unavailable — {reason}."
                button.setToolTip(f"{tip}\n{base_tooltip}" if base_tooltip else tip)
        except Exception:                            # broad-ok: ui_cleanup — button torn down between events
            pass

    refresh()
    if viewer is not None:
        for _sig in ('inserted', 'removed'):
            try:
                getattr(viewer.layers.events, _sig).connect(refresh)
            except Exception:                        # broad-ok: optional_probe — older napari event API → skip live refresh
                pass
    return refresh
