"""
Drawing-layer factory
=====================
One place to create annotation/drawing layers (measurement lines, ROIs, point
markers) that are used for a PURPOSE in a method — replacing both the eager
load-time diameter layers and the ad-hoc per-method Shapes creation.

Every layer created here is:
  - seeded (for Shapes) with one invisible near-zero-length shape so an otherwise
    empty Shapes layer reports a FINITE extent (an empty Shapes layer reports a
    NaN extent in this napari build, which makes reset_view / the Home button
    compute a NaN camera zoom and crash the scale-bar overlay);
  - put into the right interactive draw mode and selected, so the user can draw
    immediately;
  - TAGGED with role + purpose via the layer-tag engine, so the drawing layer is
    visible to tag-driven autopopulation and the Tag Inspector (a measurement
    line for cell diameter and an ROI for background are both annotations but
    carry different purposes).

Usage from a method UI (mirrors FRAP's "Add ROI Drawing Layer" button):

    from pycat.toolbox.drawing_layers import add_drawing_layer
    layer = add_drawing_layer(viewer, kind='line', purpose='cell_diameter',
                              name='Cell Diameter')
"""

from __future__ import annotations


# role assigned per drawing kind (see layer_tags CORE_VALUES['role']).
_KIND_ROLE = {
    'line': 'annotation',
    'roi': 'roi',
    'rectangle': 'roi',
    'ellipse': 'roi',
    'polygon': 'roi',
    'points': 'annotation',
}

# default interactive mode per kind.
_KIND_MODE = {
    'line': 'add_line',
    'roi': 'add_rectangle',
    'rectangle': 'add_rectangle',
    'ellipse': 'add_ellipse',
    'polygon': 'add_polygon',
    'points': 'add',
}

# default edge styling for the common measurement layers.
_PURPOSE_STYLE = {
    'cell_diameter': ('white', 5),
    'object_diameter': ('red', 2),
}


def add_drawing_layer(viewer, kind='line', purpose='scratch', name=None,
                      edge_color=None, edge_width=None, activate=True):
    """Create (or reuse) a tagged drawing layer and put it in draw mode.

    viewer     the napari viewer
    kind       'line' | 'roi' | 'rectangle' | 'ellipse' | 'polygon' | 'points'
    purpose    what the layer is FOR (open vocabulary; see layer_tags
               SUGGESTED_VALUES['purpose'] for the common ones)
    name       layer name; defaults to a title-cased purpose
    edge_color / edge_width  override the styling (else derived from purpose/kind)
    activate   select the layer and enter draw mode so the user can draw at once

    Returns the layer (existing one reused if a layer of that name is present).
    """
    import numpy as _np

    if name is None:
        name = purpose.replace('_', ' ').title() if purpose else kind.title()

    # Reuse an existing layer of the same name rather than duplicate.
    existing = None
    try:
        if name in [l.name for l in viewer.layers]:
            existing = viewer.layers[name]
    except Exception:
        existing = None

    # Styling.
    if edge_color is None or edge_width is None:
        _c, _w = _PURPOSE_STYLE.get(purpose, ('yellow', 2))
        edge_color = edge_color or _c
        edge_width = edge_width if edge_width is not None else _w

    is_points = (kind == 'points')

    if existing is not None:
        layer = existing
    else:
        if is_points:
            layer = viewer.add_points(name=name, size=8, face_color=edge_color)
        else:
            shape_type = 'line' if kind == 'line' else (
                'rectangle' if kind in ('roi', 'rectangle') else
                'ellipse' if kind == 'ellipse' else 'polygon')
            layer = viewer.add_shapes(name=name, edge_color=edge_color,
                                      edge_width=edge_width)
            # Seed one invisible near-zero-length shape so the empty Shapes layer
            # reports a finite extent (guards the NaN-extent / Home-button crash).
            try:
                if shape_type == 'line':
                    seed = _np.array([[0.0, 0.0], [0.0, 1e-4]])
                else:
                    seed = _np.array([[0.0, 0.0], [0.0, 1e-4],
                                      [1e-4, 1e-4], [1e-4, 0.0]])
                layer.add(seed, shape_type=shape_type, edge_width=0.0)
                layer.current_edge_width = edge_width
            except Exception:
                pass

    # Tag it: role from kind, purpose as given. Marked user_set for purpose so a
    # deliberate choice isn't re-inferred away; role inferred.
    try:
        from pycat.utils import layer_tags as _LT
        _LT.tag_layer(layer, 'role', _KIND_ROLE.get(kind, 'annotation'),
                      source='inferred')
        if purpose:
            _LT.tag_layer(layer, 'purpose', purpose, source='user_set',
                          confidence=1.0, overwrite=True)
        _LT.tag_layer(layer, 'provenance', 'pycat-generated', source='inferred')
    except Exception:
        pass

    # Activate: select + enter the draw mode so the user can draw immediately.
    if activate:
        try:
            viewer.layers.selection.active = layer
            layer.mode = _KIND_MODE.get(kind, 'add_line' if not is_points else 'add')
        except Exception:
            pass

    return layer
